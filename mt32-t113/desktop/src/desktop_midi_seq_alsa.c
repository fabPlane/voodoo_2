/* desktop_midi_seq_alsa.c - MIDI in from the ALSA sequencer (Linux).
 *
 * Creates a client called "mt32-t113" with one writable, subscribable port, so
 * that anything on the system -- a USB keyboard, a DAW, aconnect, DOSBox's
 * ALSA MIDI output -- can be routed to it exactly as it would be routed to a
 * real MT-32 hanging off a MIDI interface.
 *
 * The sequencer delivers parsed events, not bytes, so we run them back through
 * snd_midi_event_decode() into the byte stream that mtp_midi.h promises. That
 * is not a detour: it keeps every source in this build funnelling through
 * port/src/mtp_midi_parser.c, which is the code that has to be right on the
 * target. snd_midi_event_no_status(1) stops ALSA re-compressing to running
 * status on the way out, so what we push is one complete message per event.
 *
 * Compiled only when ALSA's development headers are present; the build falls
 * back to desktop_midi_seq_none.c otherwise, and every other MIDI source still
 * works.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "desktop_midi.h"
#include "mtp_time.h"
#include "mtp_log.h"

#include <alsa/asoundlib.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>

static snd_seq_t        *g_seq;
static int               g_port = -1;
static snd_midi_event_t *g_dec;

const char *dseq_backend_name(void) { return "ALSA seq"; }

mtp_status dseq_open(const char *connect_to)
{
    int err;

    if (g_seq) return MTP_ERR_STATE;

    err = snd_seq_open(&g_seq, "default", SND_SEQ_OPEN_INPUT, 0);
    if (err < 0) {
        MTP_LOGE("ALSA sequencer unavailable: %s", snd_strerror(err));
        MTP_LOGE("  (no /dev/snd/seq -- load snd-seq, or use --midi-fifo instead)");
        g_seq = NULL;
        return MTP_ERR_IO;
    }
    snd_seq_set_client_name(g_seq, "mt32-t113");
    snd_seq_nonblock(g_seq, 1);

    g_port = snd_seq_create_simple_port(g_seq, "MIDI in",
                SND_SEQ_PORT_CAP_WRITE | SND_SEQ_PORT_CAP_SUBS_WRITE,
                SND_SEQ_PORT_TYPE_MIDI_GENERIC |
                SND_SEQ_PORT_TYPE_MIDI_MT32    |
                SND_SEQ_PORT_TYPE_SYNTHESIZER  |
                SND_SEQ_PORT_TYPE_APPLICATION);
    if (g_port < 0) {
        MTP_LOGE("cannot create sequencer port: %s", snd_strerror(g_port));
        snd_seq_close(g_seq); g_seq = NULL;
        return MTP_ERR_IO;
    }

    /* A decoder big enough for mt32emu's own sysex cap, so a 32 kB timbre
     * dump arrives as one push rather than being split by our buffer. */
    err = snd_midi_event_new(32768, &g_dec);
    if (err < 0) {
        MTP_LOGE("snd_midi_event_new: %s", snd_strerror(err));
        snd_seq_close(g_seq); g_seq = NULL;
        return MTP_ERR_NOMEM;
    }
    snd_midi_event_no_status(g_dec, 1);

    MTP_LOGI("midi in: ALSA seq client %d:%d \"mt32-t113\"",
             snd_seq_client_id(g_seq), g_port);

    if (connect_to && *connect_to) {
        snd_seq_addr_t addr;
        if (snd_seq_parse_address(g_seq, &addr, connect_to) < 0) {
            MTP_LOGW("cannot resolve MIDI source '%s'; leaving the port "
                     "unconnected (use aconnect)", connect_to);
        } else if ((err = snd_seq_connect_from(g_seq, g_port,
                                               addr.client, addr.port)) < 0) {
            MTP_LOGW("cannot subscribe to %d:%d: %s",
                     addr.client, addr.port, snd_strerror(err));
        } else {
            MTP_LOGI("  subscribed to %d:%d (%s)", addr.client, addr.port,
                     connect_to);
        }
    } else {
        MTP_LOGI("  connect something to it:  aconnect <src> %d:%d",
                 snd_seq_client_id(g_seq), g_port);
    }
    return MTP_OK;
}

void dseq_close(void)
{
    if (g_dec) { snd_midi_event_free(g_dec); g_dec = NULL; }
    if (g_seq) { snd_seq_close(g_seq); g_seq = NULL; g_port = -1; }
}

int dseq_pollfd_count(void)
{
    return g_seq ? snd_seq_poll_descriptors_count(g_seq, POLLIN) : 0;
}

int dseq_fill_pollfds(void *pfds, int max)
{
    int n;
    if (!g_seq) return 0;
    n = snd_seq_poll_descriptors_count(g_seq, POLLIN);
    if (n > max) n = max;
    if (n <= 0) return 0;
    return snd_seq_poll_descriptors(g_seq, (struct pollfd *)pfds,
                                    (unsigned)n, POLLIN);
}

void dseq_dispatch(void *pfds, int n)
{
    unsigned short revents = 0;
    uint8_t buf[32768];

    if (!g_seq) return;
    if (snd_seq_poll_descriptors_revents(g_seq, (struct pollfd *)pfds,
                                         (unsigned)n, &revents) < 0)
        return;
    if (!(revents & POLLIN)) return;

    for (;;) {
        snd_seq_event_t *ev = NULL;
        long len;
        if (snd_seq_event_input(g_seq, &ev) < 0 || ev == NULL) break;
        len = snd_midi_event_decode(g_dec, buf, (long)sizeof(buf), ev);
        if (len > 0)
            desktop_midi_push(buf, (size_t)len, mtp_time_us());
        /* A decode error is a control event (port subscribe, etc.) we do not
         * care about; snd_midi_event_decode returns -ENOENT for those. */
    }
}

void dseq_list(void)
{
    snd_seq_t *seq;
    snd_seq_client_info_t *cinfo;
    snd_seq_port_info_t *pinfo;

    if (snd_seq_open(&seq, "default", SND_SEQ_OPEN_DUPLEX, 0) < 0) {
        printf("ALSA sequencer unavailable\n");
        return;
    }
    snd_seq_client_info_alloca(&cinfo);
    snd_seq_port_info_alloca(&pinfo);

    printf("ALSA sequencer ports that can send MIDI:\n");
    snd_seq_client_info_set_client(cinfo, -1);
    while (snd_seq_query_next_client(seq, cinfo) >= 0) {
        int client = snd_seq_client_info_get_client(cinfo);
        snd_seq_port_info_set_client(pinfo, client);
        snd_seq_port_info_set_port(pinfo, -1);
        while (snd_seq_query_next_port(seq, pinfo) >= 0) {
            unsigned int caps = snd_seq_port_info_get_capability(pinfo);
            if ((caps & (SND_SEQ_PORT_CAP_READ | SND_SEQ_PORT_CAP_SUBS_READ))
                != (SND_SEQ_PORT_CAP_READ | SND_SEQ_PORT_CAP_SUBS_READ))
                continue;
            printf("  %3d:%-2d  %-28s %s\n",
                   client, snd_seq_port_info_get_port(pinfo),
                   snd_seq_client_info_get_name(cinfo),
                   snd_seq_port_info_get_name(pinfo));
        }
    }
    snd_seq_close(seq);
}
