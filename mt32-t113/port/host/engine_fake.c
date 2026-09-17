/* engine_fake.c - a synthesiser that is not one.
 *
 * Eight voices of decaying sine, MIDI note to frequency, 32 kHz internal clock
 * so that timestamps behave exactly as mt32emu's do, and a deliberately small
 * event queue so that back-pressure in the render loop gets exercised rather
 * than assumed. It allocates once at open and never again -- the same contract
 * the real engine is held to.
 *
 * Its job is to make port/ runnable before ROMs, before mt32emu, before
 * silicon. It is not a preview of the sound.
 *
 * SPDX-License-Identifier: 0BSD */

#include "mtp_engine.h"
#include "mtp_log.h"

#include <stdlib.h>
#include <string.h>
#include <math.h>

#define FAKE_RATE     32000u     /* same as MT32EMU_SAMPLE_RATE */
#define VOICES        8
#define QUEUE_SIZE    64
#define SYSEX_STORAGE 8192

typedef struct {
    int      active;
    uint8_t  key;
    float    phase;
    float    step;
    float    amp;
    float    decay;
} voice;

typedef struct {
    uint32_t t_samples;
    uint32_t msg;          /* 0 => sysex */
    uint32_t sysex_off;
    uint32_t sysex_len;
} event;

struct mtp_engine {
    uint32_t out_rate;
    uint32_t rendered;      /* in FAKE_RATE ticks */
    double   frac;          /* output-rate to FAKE_RATE accumulator */
    voice    v[VOICES];
    event    q[QUEUE_SIZE];
    unsigned q_head, q_tail;
    uint8_t  sysex[SYSEX_STORAGE];
    uint32_t sysex_used;
    uint32_t sysex_seen;
    char     lcd[21];
};

static float key_to_step(uint8_t key)
{
    double f = 440.0 * pow(2.0, ((double)key - 69.0) / 12.0);
    return (float)(f * 2.0 * 3.14159265358979 / (double)FAKE_RATE);
}

static mtp_status fake_open(const mtp_engine_config *cfg, mtp_engine **out)
{
    mtp_engine *e = (mtp_engine *)calloc(1, sizeof(*e));
    if (!e) return MTP_ERR_NOMEM;
    e->out_rate = cfg && cfg->output_rate ? cfg->output_rate : 48000u;
    memcpy(e->lcd, "fake engine         ", 20);
    e->lcd[20] = 0;
    *out = e;
    MTP_LOGI("engine: fake, %u voices, internal %u Hz, output %u Hz",
             VOICES, FAKE_RATE, e->out_rate);
    return MTP_OK;
}

static void fake_close(mtp_engine *e) { free(e); }

static uint32_t fake_timebase(mtp_engine *e)  { (void)e; return FAKE_RATE; }
static uint32_t fake_rendered(mtp_engine *e)  { return e->rendered; }

static unsigned q_next(unsigned i) { return (i + 1u) % QUEUE_SIZE; }

static mtp_status fake_short(mtp_engine *e, uint32_t msg, uint32_t ts)
{
    if (q_next(e->q_tail) == e->q_head) return MTP_ERR_AGAIN;
    e->q[e->q_tail].t_samples = ts;
    e->q[e->q_tail].msg       = msg ? msg : 0xFFu;
    e->q[e->q_tail].sysex_len = 0;
    e->q_tail = q_next(e->q_tail);
    return MTP_OK;
}

static mtp_status fake_sysex(mtp_engine *e, const uint8_t *data, uint32_t len,
                             uint32_t ts)
{
    if (q_next(e->q_tail) == e->q_head) return MTP_ERR_AGAIN;
    if (e->sysex_used + len > SYSEX_STORAGE) {
        /* Mirrors mt32emu's behaviour when its preallocated sysex ring is
         * full: refuse rather than allocate. The render loop retries. */
        return MTP_ERR_AGAIN;
    }
    memcpy(e->sysex + e->sysex_used, data, len);
    e->q[e->q_tail].t_samples = ts;
    e->q[e->q_tail].msg       = 0;
    e->q[e->q_tail].sysex_off = e->sysex_used;
    e->q[e->q_tail].sysex_len = len;
    e->sysex_used += len;
    e->q_tail = q_next(e->q_tail);
    return MTP_OK;
}

static void note_on(mtp_engine *e, uint8_t key, uint8_t vel)
{
    int i, victim = 0;
    float weakest = 1e9f;
    for (i = 0; i < VOICES; i++) {
        if (!e->v[i].active) { victim = i; goto take; }
        if (e->v[i].amp < weakest) { weakest = e->v[i].amp; victim = i; }
    }
take:
    e->v[victim].active = 1;
    e->v[victim].key    = key;
    e->v[victim].phase  = 0.0f;
    e->v[victim].step   = key_to_step(key);
    e->v[victim].amp    = (float)vel / 127.0f * 0.25f;
    e->v[victim].decay  = 0.99994f;
}

static void note_off(mtp_engine *e, uint8_t key)
{
    int i;
    for (i = 0; i < VOICES; i++)
        if (e->v[i].active && e->v[i].key == key) e->v[i].decay = 0.9990f;
}

static void apply(mtp_engine *e, const event *ev)
{
    uint8_t status, d1, d2;
    if (ev->sysex_len) {
        e->sysex_seen++;
        /* Consume storage back to empty once the queue drains -- crude, but
         * it models "the space is reclaimed when the event is processed". */
        if (e->q_head == e->q_tail) e->sysex_used = 0;
        return;
    }
    status = (uint8_t)(ev->msg & 0xFFu);
    d1     = (uint8_t)((ev->msg >> 8) & 0x7Fu);
    d2     = (uint8_t)((ev->msg >> 16) & 0x7Fu);
    switch (status & 0xF0u) {
    case 0x90: if (d2) note_on(e, d1, d2); else note_off(e, d1); break;
    case 0x80: note_off(e, d1); break;
    case 0xB0: if (d1 == 123u || d1 == 120u) {
                   int i; for (i = 0; i < VOICES; i++) e->v[i].decay = 0.995f;
               }
               break;
    default: break;
    }
}

static int16_t clip(float f)
{
    if (f >  32767.0f) return  32767;
    if (f < -32768.0f) return -32768;
    return (int16_t)f;
}

static void tick(mtp_engine *e, int16_t *dst)
{
    float acc = 0.0f;
    int i;
    for (i = 0; i < VOICES; i++) {
        if (!e->v[i].active) continue;
        acc += e->v[i].amp * sinf(e->v[i].phase);
        e->v[i].phase += e->v[i].step;
        if (e->v[i].phase > 6.283185f) e->v[i].phase -= 6.283185f;
        e->v[i].amp *= e->v[i].decay;
        if (e->v[i].amp < 0.0002f) e->v[i].active = 0;
    }
    dst[0] = dst[1] = clip(acc * 32767.0f);
}

static void fake_render(mtp_engine *e, int16_t *stereo, uint32_t frames)
{
    /* One internal tick per output frame scaled by rate ratio, so that the
     * engine's sample clock advances at FAKE_RATE while we emit at out_rate --
     * exactly the 32 kHz -> 48 kHz relationship mt32emu has internally. */
    double ratio = (double)FAKE_RATE / (double)e->out_rate;
    uint32_t f;
    for (f = 0; f < frames; f++) {
        e->frac += ratio;
        while (e->frac >= 1.0) {
            e->frac -= 1.0;
            /* Process every event whose timestamp has come due. */
            while (e->q_head != e->q_tail) {
                int32_t delta = (int32_t)(e->q[e->q_head].t_samples - e->rendered);
                if (delta > 0) break;
                apply(e, &e->q[e->q_head]);
                e->q_head = q_next(e->q_head);
            }
            e->rendered++;
        }
        tick(e, stereo + f * 2u);
    }
    if (e->q_head == e->q_tail) e->sysex_used = 0;
}

static void fake_display(mtp_engine *e, char *dst21)
{
    memcpy(dst21, e->lcd, 21);
}

const mtp_engine_vtable mtp_engine_fake = {
    "fake",
    fake_open,
    fake_close,
    fake_timebase,
    fake_rendered,
    fake_short,
    fake_sysex,
    fake_render,
    fake_display
};
