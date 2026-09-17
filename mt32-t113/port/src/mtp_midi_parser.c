/* mtp_midi_parser.c - see include/mtp_midi_parser.h.
 * No allocation, no libc beyond memcpy. SPDX-License-Identifier: 0BSD */

#include "mtp_midi_parser.h"

#include <string.h>

unsigned mtp_midi_msg_len(uint8_t status)
{
    if (status < 0x80u) return 0;
    switch (status & 0xF0u) {
    case 0x80: case 0x90: case 0xA0: case 0xB0: case 0xE0: return 3;
    case 0xC0: case 0xD0:                                  return 2;
    default: break;
    }
    switch (status) {
    case 0xF1: return 2;   /* MTC quarter frame  */
    case 0xF2: return 3;   /* song position      */
    case 0xF3: return 2;   /* song select        */
    case 0xF6: return 1;   /* tune request       */
    default:   return 0;   /* F0/F4/F5/F7 and real time are not "short" */
    }
}

void mtp_midi_parser_init(mtp_midi_parser *p, uint8_t *sysex_buf,
                          uint32_t sysex_cap, mtp_midi_sink sink, void *user)
{
    memset(p, 0, sizeof(*p));
    p->sysex_buf = sysex_buf;
    p->sysex_cap = sysex_cap;
    p->sink      = sink;
    p->user      = user;
}

static void emit_short(mtp_midi_parser *p)
{
    mtp_midi_msg m;
    m.kind      = MTP_MSG_SHORT;
    m.t_us      = p->t_us;
    m.msg       = (uint32_t)p->pending[0]
                | ((uint32_t)p->pending[1] << 8)
                | ((uint32_t)p->pending[2] << 16);
    m.sysex     = NULL;
    m.sysex_len = 0;
    p->stat_short++;
    if (p->sink) p->sink(&m, p->user);
    /* Running status survives: the next data byte reuses pending[0]. */
    p->pending_len = 1;
}

static void emit_realtime(mtp_midi_parser *p, uint8_t b)
{
    mtp_midi_msg m;
    m.kind      = MTP_MSG_REALTIME;
    m.t_us      = p->t_us;
    m.msg       = b;
    m.sysex     = NULL;
    m.sysex_len = 0;
    p->stat_realtime++;
    if (p->sink) p->sink(&m, p->user);
}

static void emit_sysex(mtp_midi_parser *p)
{
    mtp_midi_msg m;
    if (p->sysex_overflow) {
        /* We never saw the whole thing, so we must not hand a half message to
         * a synth that will checksum it. Count it and move on. */
        p->stat_sysex_truncated++;
    } else {
        m.kind      = MTP_MSG_SYSEX;
        m.t_us      = p->t_us;
        m.msg       = 0;
        m.sysex     = p->sysex_buf;
        m.sysex_len = p->sysex_len;
        p->stat_sysex++;
        if (p->sink) p->sink(&m, p->user);
    }
    p->in_sysex       = 0;
    p->sysex_len      = 0;
    p->sysex_overflow = 0;
}

static void sysex_push(mtp_midi_parser *p, uint8_t b)
{
    if (p->sysex_len < p->sysex_cap) {
        p->sysex_buf[p->sysex_len++] = b;
    } else {
        p->sysex_overflow = 1;
    }
}

void mtp_midi_parser_feed(mtp_midi_parser *p, const mtp_midi_byte *bytes,
                          size_t n)
{
    size_t i;
    for (i = 0; i < n; i++) {
        uint8_t b = bytes[i].byte;
        p->t_us = bytes[i].t_us;

        /* System Real Time: single byte, may appear anywhere, never disturbs
         * running status and never disturbs a sysex in progress. */
        if (b >= 0xF8u) {
            emit_realtime(p, b);
            continue;
        }

        if (b >= 0x80u) {
            /* Status byte. */
            if (p->in_sysex) {
                if (b == 0xF7u) {          /* proper end of exclusive */
                    sysex_push(p, b);
                    emit_sysex(p);
                    continue;
                }
                /* Any other status aborts the sysex. Real devices do this and
                 * so does mt32emu's own parser. */
                p->stat_sysex_aborted++;
                p->in_sysex       = 0;
                p->sysex_len      = 0;
                p->sysex_overflow = 0;
            }

            if (b == 0xF0u) {
                p->in_sysex       = 1;
                p->sysex_len      = 0;
                p->sysex_overflow = 0;
                p->running_status = 0;   /* sysex clears running status */
                sysex_push(p, b);
                continue;
            }
            if (b == 0xF7u) {
                /* Lone EOX with no sysex open: ignore. */
                continue;
            }

            p->pending[0]    = b;
            p->pending[1]    = 0;
            p->pending[2]    = 0;
            p->pending_len   = 1;
            p->pending_need  = (uint8_t)mtp_midi_msg_len(b);
            /* System common (F1..F6) clears running status; channel messages
             * set it. */
            p->running_status = (b < 0xF0u) ? b : 0u;
            if (p->pending_need == 0u) {   /* 0xF4/0xF5: undefined, ignore */
                p->pending_len = 0;
            } else if (p->pending_need == 1u) {   /* e.g. tune request */
                emit_short(p);
                p->pending_len = 0;
            }
            continue;
        }

        /* Data byte. */
        if (p->in_sysex) {
            sysex_push(p, b);
            continue;
        }

        if (p->pending_len == 0u) {
            if (p->running_status == 0u) {
                p->stat_dropped_data++;    /* joined the stream mid-message */
                continue;
            }
            p->pending[0]   = p->running_status;
            p->pending_need = (uint8_t)mtp_midi_msg_len(p->running_status);
            p->pending_len  = 1;
        }

        if (p->pending_len < 3u) p->pending[p->pending_len] = b;
        p->pending_len++;

        if (p->pending_len >= p->pending_need) {
            emit_short(p);
            /* emit_short leaves pending_len == 1 so running status continues.
             * For system common there is no running status, so clear. */
            if (p->pending[0] >= 0xF0u) p->pending_len = 0;
        }
    }
}
