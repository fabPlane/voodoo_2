/* desktop_midi_seq_core.c - MIDI in from CoreMIDI (macOS).
 *
 * UNVERIFIED. This file has never been compiled or run: the session that wrote
 * it had no Mac. It is written from Apple's CoreMIDI documentation and the
 * conventional use of MIDIClientCreate / MIDIDestinationCreate /
 * MIDIInputPortCreate, and it is guarded so that it is never built anywhere
 * else. Treat the first macOS build as a bring-up, not a regression.
 * See FINDINGS.md.
 *
 * Shape, which mirrors the ALSA file:
 *   - a virtual destination named "mt32-t113", so a DAW or any MIDI-capable
 *     application can send to it as if it were a hardware module;
 *   - an input port connected to real sources (a USB keyboard, an interface),
 *     so a keyboard works with no routing step at all.
 *
 * Both funnel into desktop_midi_push() as raw bytes, so everything downstream
 * -- parser, sysex reassembly, timestamps -- is the same code as on Linux and
 * as on the T113. CoreMIDI hands us packets on its own high-priority thread,
 * which is why the hub's FIFO is mutex-protected.
 *
 * The MIDIReadProc API is the MIDI 1.0 one. It is marked deprecated in favour
 * of MIDIInputPortCreateWithProtocol from macOS 11, but it is still present and
 * still delivers MIDI 1.0 byte streams, which is exactly what an MT-32 wants;
 * the newer API would hand us UMP packets that we would have to convert back.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "desktop_midi.h"
#include "mtp_time.h"
#include "mtp_log.h"

#include <CoreFoundation/CoreFoundation.h>
#include <CoreMIDI/CoreMIDI.h>

#include <stdio.h>
#include <string.h>

static MIDIClientRef    g_client;
static MIDIPortRef      g_in_port;
static MIDIEndpointRef  g_virtual_dest;
static int              g_open;

const char *dseq_backend_name(void) { return "CoreMIDI"; }

static void name_of(MIDIObjectRef obj, char *dst, size_t cap)
{
    CFStringRef s = NULL;
    dst[0] = 0;
    if (MIDIObjectGetStringProperty(obj, kMIDIPropertyDisplayName, &s) == noErr
        && s != NULL) {
        CFStringGetCString(s, dst, (CFIndex)cap, kCFStringEncodingUTF8);
        CFRelease(s);
    }
    if (!dst[0]) snprintf(dst, cap, "(unnamed)");
}

/* CoreMIDI's callback thread. Keep it to a memcpy and a push. */
static void read_proc(const MIDIPacketList *pkts, void *refcon, void *src_refcon)
{
    const MIDIPacket *p;
    UInt32 i;
    uint32_t t = mtp_time_us();

    (void)refcon; (void)src_refcon;
    if (!pkts) return;

    p = &pkts->packet[0];
    for (i = 0; i < pkts->numPackets; i++) {
        if (p->length > 0)
            desktop_midi_push(p->data, (size_t)p->length, t);
        p = MIDIPacketNext(p);
    }
}

mtp_status dseq_open(const char *connect_to)
{
    OSStatus st;
    CFStringRef cf_name;
    ItemCount n, i;
    unsigned connected = 0;

    if (g_open) return MTP_ERR_STATE;

    cf_name = CFStringCreateWithCString(NULL, "mt32-t113", kCFStringEncodingUTF8);
    st = MIDIClientCreate(cf_name, NULL, NULL, &g_client);
    if (st != noErr) {
        CFRelease(cf_name);
        MTP_LOGE("MIDIClientCreate failed (OSStatus %d)", (int)st);
        return MTP_ERR_IO;
    }

    /* A destination other applications can send to. */
    st = MIDIDestinationCreate(g_client, cf_name, read_proc, NULL,
                               &g_virtual_dest);
    if (st != noErr)
        MTP_LOGW("no virtual MIDI destination (OSStatus %d); real sources only",
                 (int)st);
    else
        MTP_LOGI("midi in: CoreMIDI virtual destination \"mt32-t113\"");

    st = MIDIInputPortCreate(g_client, CFSTR("mt32-t113 in"), read_proc, NULL,
                             &g_in_port);
    CFRelease(cf_name);
    if (st != noErr) {
        MTP_LOGE("MIDIInputPortCreate failed (OSStatus %d)", (int)st);
        MIDIClientDispose(g_client);
        return MTP_ERR_IO;
    }

    /* connect_to: NULL or "" connects every source, which is what a person
     * with one keyboard wants; "none" connects none and leaves only the
     * virtual destination; anything else is matched against the source name. */
    if (connect_to && !strcmp(connect_to, "none")) {
        MTP_LOGI("  no hardware sources connected (--midi-seq none)");
    } else {
        n = MIDIGetNumberOfSources();
        for (i = 0; i < n; i++) {
            MIDIEndpointRef src = MIDIGetSource(i);
            char nm[256];
            if (!src) continue;
            name_of(src, nm, sizeof(nm));
            if (connect_to && *connect_to && strstr(nm, connect_to) == NULL)
                continue;
            if (MIDIPortConnectSource(g_in_port, src, NULL) == noErr) {
                MTP_LOGI("  connected source: %s", nm);
                connected++;
            } else {
                MTP_LOGW("  cannot connect source: %s", nm);
            }
        }
        if (connected == 0u)
            MTP_LOGW("  no MIDI sources connected%s",
                     (connect_to && *connect_to) ? " matching that name" : "");
    }

    g_open = 1;
    return MTP_OK;
}

void dseq_close(void)
{
    if (!g_open) return;
    if (g_virtual_dest) MIDIEndpointDispose(g_virtual_dest);
    if (g_in_port)      MIDIPortDispose(g_in_port);
    if (g_client)       MIDIClientDispose(g_client);
    g_virtual_dest = 0; g_in_port = 0; g_client = 0;
    g_open = 0;
}

/* CoreMIDI delivers on its own thread, so there is nothing for the hub's
 * poll() loop to watch. */
int  dseq_pollfd_count(void)                { return 0; }
int  dseq_fill_pollfds(void *pfds, int max) { (void)pfds; (void)max; return 0; }
void dseq_dispatch(void *pfds, int n)       { (void)pfds; (void)n; }

void dseq_list(void)
{
    ItemCount n, i;
    MIDIClientRef client = 0;
    CFStringRef cf = CFStringCreateWithCString(NULL, "mt32-t113 list",
                                               kCFStringEncodingUTF8);
    if (MIDIClientCreate(cf, NULL, NULL, &client) != noErr) {
        CFRelease(cf);
        printf("CoreMIDI unavailable\n");
        return;
    }
    CFRelease(cf);

    n = MIDIGetNumberOfSources();
    printf("CoreMIDI sources that can send MIDI:\n");
    for (i = 0; i < n; i++) {
        char nm[256];
        name_of(MIDIGetSource(i), nm, sizeof(nm));
        printf("  %2u  %s\n", (unsigned)i, nm);
    }
    if (n == 0) printf("  (none)\n");
    MIDIClientDispose(client);
}
