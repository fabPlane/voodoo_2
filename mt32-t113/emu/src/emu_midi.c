/* emu_midi.c - mtp_midi.h from a byte stream linked into the image.
 *
 * On the board this is UART2's RX interrupt: read RBR, stamp it, push it into
 * a ring (port/DESIGN.md section 3.3). `-M virt` has a second PL011 but no
 * way to drive 31250 baud into it from a test script, so the stream comes from
 * .rodata instead -- the same bytes port/host/test.sh asserts against, via
 * tools/gen_vectors.py.
 *
 * What is preserved, and it is the part that matters, is the *arrival
 * pattern*. In paced mode a byte becomes readable only once
 * mtp_time_us() says its 320 us of wire time has elapsed, from the same
 * generic timer that drives the audio deadline. So the parser and the render
 * loop see bytes dribbling in across thousands of audio blocks exactly as they
 * will on a real cable -- which is the only way the 16 kB bank-dump case tests
 * anything. Unpaced mode delivers the stream as fast as the loop asks for it,
 * which is what port/host/test.sh's first three cases do, so that the counters
 * come out identical.
 *
 * What is NOT modelled: framing errors, UART overrun, and the FIFO overrun
 * that port/include/mtp_midi.h counts. Those counters exist here and are
 * always zero, because nothing in this image can produce one. On silicon they
 * are the first numbers to look at.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>

#include "mtp_midi.h"
#include "mtp_time.h"
#include "mtp_log.h"
#include "midi_vectors.h"

static const uint8_t *g_data;
static uint32_t g_len;
static uint32_t g_pos;
static uint32_t g_baud = 31250u;
static uint32_t g_us_per_byte;
static uint64_t g_t0;
static int      g_paced;
static const char *g_name = "(none)";

int strcmp(const char *a, const char *b);

void emu_midi_select(const char *name, int paced)
{
    const midi_vector *v;
    g_paced = paced;
    for (v = midi_vectors; v->name; v++) {
        if (name && strcmp(name, v->name) == 0) {
            g_data = v->bytes;
            g_len  = v->len;
            g_name = v->name;
            return;
        }
    }
    g_data = NULL;
    g_len  = 0u;
    g_name = "(none)";
}

const char *emu_midi_name(void) { return g_name; }
uint32_t    emu_midi_length(void) { return g_len; }

mtp_status mtp_midi_open(uint32_t baud)
{
    if (baud) g_baud = baud;
    g_us_per_byte = 10000000u / g_baud;     /* 10 bits per byte -> 320 us */
    g_pos = 0u;
    g_t0  = mtp_time_us64();
    MTP_LOGI("midi: stream '%s', %u bytes, %u baud (%u us/byte)%s",
             g_name, g_len, g_baud, g_us_per_byte, g_paced ? "" : ", unpaced");
    return MTP_OK;
}

void mtp_midi_close(void) { g_pos = g_len; }

size_t mtp_midi_read(mtp_midi_byte *dst, size_t max)
{
    size_t n = 0;
    if (!g_data) return 0;

    while (n < max && g_pos < g_len) {
        uint64_t due_us = (uint64_t)g_pos * g_us_per_byte;
        if (g_paced && (mtp_time_us64() - g_t0) < due_us)
            break;                          /* byte is not on the wire yet */
        dst[n].byte   = g_data[g_pos];
        dst[n].t_us   = (uint32_t)(g_t0 + due_us);
        dst[n].pad[0] = dst[n].pad[1] = dst[n].pad[2] = 0u;
        g_pos++;
        n++;
    }
    return n;
}

uint32_t mtp_midi_overruns(void)     { return 0u; }
uint32_t mtp_midi_frame_errors(void) { return 0u; }
int      mtp_midi_eof(void)          { return g_data == NULL || g_pos >= g_len; }
