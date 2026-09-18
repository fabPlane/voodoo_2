/* desktop_midi_src.h - internal interface between the MIDI hub and its
 * per-source back ends. Not used above the seam.
 * SPDX-License-Identifier: 0BSD */
#ifndef DESKTOP_MIDI_SRC_H
#define DESKTOP_MIDI_SRC_H

#include "mtp_platform.h"

/* desktop_midi_tty.c: a real serial line at a non-standard baud rate.
 * Returns an fd in raw, non-blocking mode, or -1. */
int dtty_open(const char *path, uint32_t baud);

/* desktop_midi_smf.c: a Standard MIDI File, played against mtp_time_us(). */
mtp_status dsmf_open(const char *path, int loop);
void       dsmf_pump(void);     /* pushes whatever is due; never blocks */
int        dsmf_eof(void);
void       dsmf_close(void);

#endif /* DESKTOP_MIDI_SRC_H */
