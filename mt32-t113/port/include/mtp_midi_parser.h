/* mtp_midi_parser.h - MIDI byte stream -> messages. Portable, no allocation.
 *
 * Handles what a 31250-baud wire actually delivers:
 *   - running status (a note stream is 2 bytes per note, not 3)
 *   - System Real Time (0xF8..0xFF) interleaved *inside* any other message,
 *     including inside a sysex, which is legal and does happen
 *   - sysex reassembled across arbitrarily many calls. An MT-32 timbre bank
 *     dump is ~16 kB, which is 5.2 seconds of wire time and some two thousand
 *     audio blocks: the parser has to survive being interrupted constantly
 *   - a sysex that never terminates, because the cable was pulled
 *   - data bytes with no status, because we started listening mid-stream
 *
 * The sysex buffer is caller-supplied and fixed. 32768 is the right number: it
 * is what mt32emu itself caps at (mt32emu/globals.h:122,
 * MT32EMU_MAX_STREAM_BUFFER_SIZE) because the hardware units only have 32 kB
 * of RAM, so nothing longer can be meaningful.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_MIDI_PARSER_H
#define MTP_MIDI_PARSER_H

#include "mtp_platform.h"
#include "mtp_midi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MTP_SYSEX_MAX 32768u

typedef enum {
    MTP_MSG_SHORT,      /* 1-3 byte channel or system-common message */
    MTP_MSG_SYSEX,      /* complete F0..F7                           */
    MTP_MSG_REALTIME    /* single byte, 0xF8..0xFF                   */
} mtp_msg_kind;

typedef struct {
    mtp_msg_kind   kind;
    uint32_t       t_us;    /* arrival time of the message's last byte */
    /* MTP_MSG_SHORT / MTP_MSG_REALTIME */
    uint32_t       msg;     /* status | data1<<8 | data2<<16          */
    /* MTP_MSG_SYSEX */
    const uint8_t *sysex;   /* points into the parser's buffer; valid until
                             * the next mtp_midi_parser_feed() call     */
    uint32_t       sysex_len;
} mtp_midi_msg;

/* Called for each complete message, in stream order. */
typedef void (*mtp_midi_sink)(const mtp_midi_msg *m, void *user);

typedef struct {
    uint8_t  *sysex_buf;
    uint32_t  sysex_cap;
    uint32_t  sysex_len;
    uint8_t   running_status;
    uint8_t   pending[3];   /* status + data bytes collected so far */
    uint8_t   pending_len;
    uint8_t   pending_need; /* total bytes the pending message needs */
    uint8_t   in_sysex;
    uint8_t   sysex_overflow;
    uint32_t  t_us;         /* timestamp of the byte being processed */
    /* diagnostics */
    uint32_t  stat_short;
    uint32_t  stat_sysex;
    uint32_t  stat_realtime;
    uint32_t  stat_dropped_data;   /* data byte with no running status   */
    uint32_t  stat_sysex_truncated;/* sysex longer than the buffer       */
    uint32_t  stat_sysex_aborted;  /* new status byte inside a sysex     */
    mtp_midi_sink sink;
    void     *user;
} mtp_midi_parser;

void mtp_midi_parser_init(mtp_midi_parser *p, uint8_t *sysex_buf,
                          uint32_t sysex_cap, mtp_midi_sink sink, void *user);

/* Feeds n stamped bytes. Calls the sink zero or more times, synchronously. */
void mtp_midi_parser_feed(mtp_midi_parser *p, const mtp_midi_byte *bytes,
                          size_t n);

/* Length in bytes of a channel/system-common message with this status byte,
 * including the status byte. 0 for anything that is not one. */
unsigned mtp_midi_msg_len(uint8_t status);

#ifdef __cplusplus
}
#endif

#endif /* MTP_MIDI_PARSER_H */
