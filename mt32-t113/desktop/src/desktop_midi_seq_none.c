/* desktop_midi_seq_none.c - the OS MIDI port, when there isn't one.
 *
 * Built when ALSA's headers are absent (a container, a minimal image) or on an
 * OS with no back end here. Everything else about the build still works: the
 * serial, FIFO/stdin and SMF sources do not need a sequencer, and the failure
 * says which package to install rather than silently doing nothing.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "desktop_midi.h"
#include "mtp_log.h"

#include <stdio.h>

const char *dseq_backend_name(void) { return "none"; }

mtp_status dseq_open(const char *connect_to)
{
    (void)connect_to;
    MTP_LOGE("this build has no OS MIDI port support");
#if defined(__linux__)
    MTP_LOGE("  rebuild with ALSA headers installed: apt install libasound2-dev");
    MTP_LOGE("  or pipe bytes in instead:  --midi-fifo /tmp/mt32.midi");
#else
    MTP_LOGE("  use --midi-tty, --midi-fifo or --midi-smf instead");
#endif
    return MTP_ERR_INVAL;
}

void dseq_close(void) { }
int  dseq_pollfd_count(void)                { return 0; }
int  dseq_fill_pollfds(void *pfds, int max) { (void)pfds; (void)max; return 0; }
void dseq_dispatch(void *pfds, int n)       { (void)pfds; (void)n; }

void dseq_list(void)
{
    printf("no OS MIDI port support in this build\n");
#if defined(__linux__)
    printf("  install libasound2-dev and rebuild to get the ALSA sequencer\n");
#endif
}
