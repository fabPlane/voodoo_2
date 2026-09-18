/* desktop_midi.h - the desktop's MIDI sources, and the hub they feed.
 *
 * mtp_midi.h is a single stamped-byte FIFO, because that is what a UART is.
 * On a desktop the same FIFO can be fed from several places at once, and
 * deliberately is: the whole point of this build is that a person can drive the
 * same code from whatever they happen to own.
 *
 *   tty      a real 31250-baud serial line. The highest-fidelity source there
 *            is, because it is bit-for-bit what the T113's UART2 will see:
 *            a USB-serial adapter, an FTDI+optocoupler DIN input, or a game
 *            port cable off a DOS machine's MPU-401.
 *   fifo     a named pipe or stdin, for amidi/aseqdump/DOSBox/anything that
 *            can write raw MIDI bytes to a file descriptor.
 *   seq      ALSA sequencer (Linux) or CoreMIDI (macOS): a virtual input port
 *            that a keyboard or a DAW can be routed to.
 *   smf      a Standard MIDI File, played out against the clock, for
 *            unattended runs.
 *
 * Every source converts to the same thing: bytes with arrival timestamps,
 * pushed into one FIFO, parsed by port/src/mtp_midi_parser.c exactly as on the
 * target. No source gets a shortcut into the synth.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef DESKTOP_MIDI_H
#define DESKTOP_MIDI_H

#include "mtp_platform.h"

/* All of these are called before mtp_midi_open(). Each returns MTP_OK or an
 * error; more than one may be added. */
mtp_status desktop_midi_add_tty(const char *path, uint32_t baud);
mtp_status desktop_midi_add_fifo(const char *path);   /* "-" means stdin */
mtp_status desktop_midi_add_smf(const char *path, int loop);
mtp_status desktop_midi_add_seq(const char *connect_to); /* NULL = just listen */

/* Producer side, called from source threads. t_us is mtp_time_us(). */
void desktop_midi_push(const uint8_t *bytes, size_t n, uint32_t t_us);

/* Diagnostics beyond what mtp_midi.h exposes. */
unsigned desktop_midi_source_count(void);
const char *desktop_midi_source_desc(unsigned i);
uint32_t desktop_midi_ring_peak(void);

/* Lists the OS MIDI ports that could be connected to, and returns. */
void desktop_midi_list_ports(void);

/* ---- implemented per OS, in desktop_midi_seq_{alsa,core}.c or the stub ---- */
mtp_status dseq_open(const char *connect_to);
void       dseq_close(void);
const char *dseq_backend_name(void);
void       dseq_list(void);
/* Poll integration; CoreMIDI uses its own thread and returns 0 here. */
int        dseq_pollfd_count(void);
int        dseq_fill_pollfds(void *pfds, int max);   /* struct pollfd * */
void       dseq_dispatch(void *pfds, int n);

#endif /* DESKTOP_MIDI_H */
