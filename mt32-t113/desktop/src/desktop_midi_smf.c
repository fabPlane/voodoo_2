/* desktop_midi_smf.c - MIDI in from a Standard MIDI File.
 *
 * port/host deliberately does not parse SMF: a .mid is not a wire stream, and
 * DESIGN.md is right that turning one into the other belongs in a tool rather
 * than in the port. This is that tool, and it lives here rather than in port/
 * for the same reason: nothing above the seam knows this file exists. What the
 * render loop sees is bytes with timestamps, exactly as from a UART.
 *
 * What it does:
 *   - merges all tracks into one time-ordered stream (format 0, 1 and 2);
 *   - honours tempo meta events and both division forms (PPQN and SMPTE);
 *   - expands running status, because the wire form we emit is explicit;
 *   - reconstructs F0 sysex (SMF stores F0 <varlen> <body>) and passes F7
 *     escape events through as raw bytes;
 *   - drops meta events, which have no wire representation at all.
 *
 * It is pulled from mtp_midi_read() on the render thread rather than pushed
 * from a timer thread, so an event lands in the audio block whose render call
 * follows it -- the same relationship a UART byte has.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "desktop_midi_src.h"
#include "desktop_midi.h"
#include "mtp_midi_parser.h"
#include "mtp_time.h"
#include "mtp_log.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    uint64_t tick;
    uint32_t seq;      /* stable order for events on the same tick */
    uint32_t off;      /* into the byte pool */
    uint32_t len;
    uint32_t tempo;    /* non-zero: a tempo change, us per quarter note */
    uint64_t t_us;     /* filled in by the tick -> time pass */
} smf_event;

static uint8_t   *g_pool;
static uint32_t   g_pool_len, g_pool_cap;
static smf_event *g_ev;
static uint32_t   g_ev_len, g_ev_cap;
static uint32_t   g_next;
static uint64_t   g_t0;
static uint64_t   g_span_us;
static int        g_loop;
static int        g_started;
static int        g_done;

/* ------------------------------------------------------------------ */

static int pool_put(const uint8_t *b, uint32_t n, uint32_t *off)
{
    if (g_pool_len + n > g_pool_cap) {
        uint32_t cap = g_pool_cap ? g_pool_cap * 2u : 65536u;
        uint8_t *p;
        while (cap < g_pool_len + n) cap *= 2u;
        p = (uint8_t *)realloc(g_pool, cap);
        if (!p) return -1;
        g_pool = p; g_pool_cap = cap;
    }
    *off = g_pool_len;
    memcpy(g_pool + g_pool_len, b, n);
    g_pool_len += n;
    return 0;
}

static smf_event *ev_new(void)
{
    if (g_ev_len == g_ev_cap) {
        uint32_t cap = g_ev_cap ? g_ev_cap * 2u : 4096u;
        smf_event *p = (smf_event *)realloc(g_ev, (size_t)cap * sizeof(smf_event));
        if (!p) return NULL;
        g_ev = p; g_ev_cap = cap;
    }
    memset(&g_ev[g_ev_len], 0, sizeof(smf_event));
    return &g_ev[g_ev_len++];
}

static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3];
}
static uint16_t be16(const uint8_t *p) {
    return (uint16_t)(((uint32_t)p[0]<<8)|p[1]);
}

/* Variable-length quantity. Returns bytes consumed, or 0 on a malformed one. */
static unsigned varlen(const uint8_t *p, const uint8_t *end, uint32_t *out)
{
    uint32_t v = 0;
    unsigned n = 0;
    while (p + n < end && n < 4u) {
        v = (v << 7) | (uint32_t)(p[n] & 0x7Fu);
        if (!(p[n] & 0x80u)) { *out = v; return n + 1u; }
        n++;
    }
    return 0;
}

static int cmp_event(const void *a, const void *b)
{
    const smf_event *x = (const smf_event *)a, *y = (const smf_event *)b;
    if (x->tick < y->tick) return -1;
    if (x->tick > y->tick) return  1;
    if (x->seq  < y->seq)  return -1;
    if (x->seq  > y->seq)  return  1;
    return 0;
}

/* ------------------------------------------------------------------ */

static int parse_track(const uint8_t *p, const uint8_t *end, uint32_t track_ix)
{
    uint64_t tick = 0;
    uint8_t running = 0;
    static uint32_t seq;

    while (p < end) {
        uint32_t delta = 0, len = 0;
        unsigned used = varlen(p, end, &delta);
        uint8_t status;
        if (!used) return -1;
        p += used;
        tick += delta;
        if (p >= end) return -1;

        status = *p;
        if (status < 0x80u) {
            if (!running) return -1;        /* running status with no status */
            status = running;
        } else {
            p++;
            if (status < 0xF0u) running = status;
            else if (status < 0xF8u) running = 0;   /* system common clears it */
        }

        if (status == 0xFFu) {              /* meta */
            uint8_t type;
            if (p >= end) return -1;
            type = *p++;
            used = varlen(p, end, &len);
            if (!used || p + used + len > end) return -1;
            p += used;
            if (type == 0x51u && len == 3u) {
                smf_event *e = ev_new();
                if (!e) return -1;
                e->tick  = tick;
                e->seq   = seq++;
                e->tempo = ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2];
            }
            p += len;
            continue;
        }

        if (status == 0xF0u || status == 0xF7u) {
            smf_event *e;
            uint32_t off;
            used = varlen(p, end, &len);
            if (!used || p + used + len > end) return -1;
            p += used;
            if (status == 0xF0u) {
                /* Wire form is F0 followed by the stored body (which already
                 * ends in F7). Rebuild it in the pool. */
                uint8_t lead = 0xF0u;
                if (pool_put(&lead, 1u, &off) != 0) return -1;
                {
                    uint32_t unused_off;
                    if (len && pool_put(p, len, &unused_off) != 0) return -1;
                }
                e = ev_new();
                if (!e) return -1;
                e->tick = tick; e->seq = seq++;
                e->off = off; e->len = len + 1u;
            } else {
                /* F7 escape: the bytes are already exactly what goes on the
                 * wire, including continuation fragments of a split sysex. */
                if (len == 0u) { continue; }
                if (pool_put(p, len, &off) != 0) return -1;
                e = ev_new();
                if (!e) return -1;
                e->tick = tick; e->seq = seq++;
                e->off = off; e->len = len;
            }
            p += len;
            continue;
        }

        /* Channel voice / system common: 1 or 2 data bytes. */
        {
            uint8_t msg[3];
            unsigned nd = mtp_midi_msg_len(status);
            smf_event *e;
            uint32_t off;
            if (nd == 0u) nd = 1u;
            if (p + (nd - 1u) > end) return -1;
            msg[0] = status;
            if (nd > 1u) msg[1] = p[0];
            if (nd > 2u) msg[2] = p[1];
            p += (nd - 1u);
            if (pool_put(msg, nd, &off) != 0) return -1;
            e = ev_new();
            if (!e) return -1;
            e->tick = tick; e->seq = seq++;
            e->off = off; e->len = nd;
        }
    }
    (void)track_ix;
    return 0;
}

mtp_status dsmf_open(const char *path, int loop)
{
    FILE *fp;
    uint8_t *buf;
    long size;
    uint16_t format, ntrks;
    int16_t division;
    const uint8_t *p, *end;
    uint32_t t;

    if (!path) return MTP_ERR_INVAL;
    fp = fopen(path, "rb");
    if (!fp) { MTP_LOGE("cannot read '%s'", path); return MTP_ERR_NOENT; }
    fseek(fp, 0, SEEK_END); size = ftell(fp); fseek(fp, 0, SEEK_SET);
    if (size < 14) { fclose(fp); MTP_LOGE("'%s' is too short for a MIDI file", path); return MTP_ERR_INVAL; }
    buf = (uint8_t *)malloc((size_t)size);
    if (!buf) { fclose(fp); return MTP_ERR_NOMEM; }
    if (fread(buf, 1, (size_t)size, fp) != (size_t)size) {
        fclose(fp); free(buf); return MTP_ERR_IO;
    }
    fclose(fp);

    if (memcmp(buf, "MThd", 4) != 0 || be32(buf + 4) < 6u) {
        MTP_LOGE("'%s' is not a Standard MIDI File (no MThd)", path);
        free(buf);
        return MTP_ERR_INVAL;
    }
    format   = be16(buf + 8);
    ntrks    = be16(buf + 10);
    division = (int16_t)be16(buf + 12);

    p   = buf + 8 + be32(buf + 4);
    end = buf + size;

    for (t = 0; t < ntrks && p + 8 <= end; t++) {
        uint32_t tlen;
        if (memcmp(p, "MTrk", 4) != 0) {     /* skip unknown chunks */
            tlen = be32(p + 4);
            p += 8 + tlen;
            t--;
            continue;
        }
        tlen = be32(p + 4);
        if (p + 8 + tlen > end) tlen = (uint32_t)(end - (p + 8));
        if (parse_track(p + 8, p + 8 + tlen, t) != 0)
            MTP_LOGW("track %u of '%s' is malformed; using what parsed", t, path);
        p += 8 + tlen;
    }
    free(buf);

    if (g_ev_len == 0u) {
        MTP_LOGE("'%s' contains no playable events", path);
        return MTP_ERR_INVAL;
    }

    qsort(g_ev, g_ev_len, sizeof(smf_event), cmp_event);

    /* Ticks to microseconds, walking tempo changes. */
    {
        uint64_t last_tick = 0, t_us = 0;
        double us_per_tick;
        uint32_t tempo = 500000u;          /* 120 bpm, the SMF default */
        uint32_t i;

        if (division > 0) {
            us_per_tick = (double)tempo / (double)division;
        } else {
            int fps = -(division >> 8);
            int tpf = division & 0xFF;
            if (fps == 29) fps = 30;       /* 29.97 drop frame, near enough */
            us_per_tick = 1000000.0 / ((double)fps * (double)(tpf ? tpf : 1));
        }

        for (i = 0; i < g_ev_len; i++) {
            t_us += (uint64_t)((double)(g_ev[i].tick - last_tick) * us_per_tick);
            last_tick = g_ev[i].tick;
            g_ev[i].t_us = t_us;
            if (g_ev[i].tempo && division > 0) {
                tempo = g_ev[i].tempo;
                us_per_tick = (double)tempo / (double)division;
            }
        }
        g_span_us = t_us + 500000ull;      /* half a second of tail before a loop */
    }

    g_loop = loop;
    g_next = 0;
    g_done = 0;
    g_started = 0;
    MTP_LOGI("midi in: smf '%s', format %u, %u tracks, %u events, %.1f s%s",
             path, format, ntrks, g_ev_len,
             (double)g_ev[g_ev_len - 1].t_us / 1e6, loop ? ", looping" : "");
    return MTP_OK;
}

void dsmf_pump(void)
{
    uint64_t now;

    if (!g_ev || g_done) return;
    if (!g_started) { g_t0 = mtp_time_us64(); g_started = 1; }
    now = mtp_time_us64() - g_t0;

    while (g_next < g_ev_len && g_ev[g_next].t_us <= now) {
        const smf_event *e = &g_ev[g_next];
        if (e->len)
            desktop_midi_push(g_pool + e->off, e->len, (uint32_t)(g_t0 + e->t_us));
        g_next++;
    }

    if (g_next >= g_ev_len) {
        if (!g_loop) { g_done = 1; return; }
        if (now >= g_span_us) {
            /* All notes off on every channel before starting again, so a note
             * left hanging by the end of the file does not survive the loop. */
            uint8_t cc[3];
            unsigned ch;
            for (ch = 0; ch < 16u; ch++) {
                cc[0] = (uint8_t)(0xB0u | ch); cc[1] = 123u; cc[2] = 0u;
                desktop_midi_push(cc, 3u, mtp_time_us());
            }
            g_t0 += g_span_us;
            g_next = 0;
        }
    }
}

int dsmf_eof(void) { return g_ev == NULL || g_done; }

void dsmf_close(void)
{
    free(g_pool); g_pool = NULL; g_pool_len = g_pool_cap = 0;
    free(g_ev);   g_ev = NULL;   g_ev_len = g_ev_cap = 0;
    g_next = 0; g_done = 1;
}
