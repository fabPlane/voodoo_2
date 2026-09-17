/* mtp_midi.h - the MIDI source.
 *
 * Deliberately a *byte* source, not a message source. The UART interrupt does
 * the minimum defensible work -- stamp the byte and drop it in a FIFO -- and
 * all the interpretation (running status, sysex reassembly, the 0xF7 that
 * never arrives) lives in portable code in src/mtp_midi_parser.c, where it can
 * be unit-tested on a laptop. See PORTING.md, "The MIDI path".
 *
 * Each byte carries its own arrival timestamp because at 31250 baud a byte
 * takes 320 us, which is longer than an audio block: knowing when a byte
 * arrived is the difference between a chord and an arpeggio.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_MIDI_H
#define MTP_MIDI_H

#include "mtp_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t t_us;   /* mtp_time_us() at the moment the byte was received */
    uint8_t  byte;
    uint8_t  pad[3];
} mtp_midi_byte;

/* baud is 31250 for MIDI. It is a parameter only so that a host stub or a
 * bench rig can run a stream faster than real time. */
mtp_status mtp_midi_open(uint32_t baud);
void       mtp_midi_close(void);

/* Copies up to max stamped bytes out of the FIFO. Returns the count, which may
 * be 0. Never blocks. */
size_t mtp_midi_read(mtp_midi_byte *dst, size_t max);

/* Monotonic count of bytes lost to FIFO overrun (application too slow) plus
 * UART overrun (interrupt too slow). Should stay at zero for ever; if it does
 * not, the render loop is overrunning its block period. */
uint32_t mtp_midi_overruns(void);

/* Monotonic count of framing/parity errors seen by the UART. On an
 * opto-isolated DIN input a non-zero value usually means the optocoupler, not
 * the software. */
uint32_t mtp_midi_frame_errors(void);

/* True once the source can produce no more bytes, ever. Always false on the
 * target (a UART is never finished); the host stub uses it to end the run when
 * the input file is exhausted. */
int mtp_midi_eof(void);

#ifdef __cplusplus
}
#endif

#endif /* MTP_MIDI_H */
