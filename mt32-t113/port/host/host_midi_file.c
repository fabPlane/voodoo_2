/* host_midi_file.c - the MIDI source, host stub: a file.
 *
 * Two input shapes, because both are useful:
 *   .syx / .raw / .mid-as-bytes  -- a raw byte stream, played out at the real
 *                                   31250-baud rate against mtp_time_us(), so
 *                                   the parser and the render loop see exactly
 *                                   the arrival pattern a UART would produce.
 *   MTP_HOST_MIDI_FAST=1         -- the same bytes with no pacing, for a fast
 *                                   regression run.
 *
 * A standard MIDI file is *not* parsed here. An SMF is a different thing from
 * a wire stream (delta times, tracks, meta events) and turning one into the
 * other belongs in a tool, not in the port. bench/ (workstream A) is where the
 * SMF player lives.
 *
 * SPDX-License-Identifier: 0BSD */

#include "mtp_midi.h"
#include "mtp_time.h"
#include "mtp_log.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t  *g_data;
static long      g_len;
static long      g_pos;
static uint32_t  g_baud = 31250;
static uint32_t  g_us_per_byte;
static uint64_t  g_t0;
static int       g_paced = 1;
static uint32_t  g_overruns;
static const char *g_path;

void host_midi_set_path(const char *p) { g_path = p; }
void host_midi_set_paced(int on)       { g_paced = on; }

mtp_status mtp_midi_open(uint32_t baud)
{
    FILE *fp;
    if (baud) g_baud = baud;
    g_us_per_byte = 10000000u / g_baud;   /* 10 bits per byte -> 320 us */

    if (!g_path) { g_data = NULL; g_len = 0; g_pos = 0; return MTP_OK; }

    fp = fopen(g_path, "rb");
    if (!fp) { MTP_LOGE("cannot read %s", g_path); return MTP_ERR_NOENT; }
    fseek(fp, 0, SEEK_END); g_len = ftell(fp); fseek(fp, 0, SEEK_SET);
    g_data = (uint8_t *)malloc((size_t)(g_len > 0 ? g_len : 1));
    if (!g_data) { fclose(fp); return MTP_ERR_NOMEM; }
    if (fread(g_data, 1, (size_t)g_len, fp) != (size_t)g_len) {
        fclose(fp); free(g_data); g_data = NULL; return MTP_ERR_IO;
    }
    fclose(fp);
    g_pos = 0;
    g_t0 = mtp_time_us64();
    MTP_LOGI("midi: %s, %ld bytes, %u baud (%u us/byte)%s",
             g_path, g_len, g_baud, g_us_per_byte, g_paced ? "" : ", unpaced");
    return MTP_OK;
}

void mtp_midi_close(void)
{
    free(g_data); g_data = NULL; g_len = 0; g_pos = 0;
}

size_t mtp_midi_read(mtp_midi_byte *dst, size_t max)
{
    size_t n = 0;
    if (!g_data) return 0;

    while (n < max && g_pos < g_len) {
        uint64_t due_us = (uint64_t)g_pos * g_us_per_byte;
        uint64_t now;
        if (g_paced) {
            now = mtp_time_us64() - g_t0;
            if (now < due_us) break;         /* byte not on the wire yet */
        } else {
            now = due_us;
        }
        dst[n].byte = g_data[g_pos];
        dst[n].t_us = (uint32_t)(g_t0 + due_us);
        dst[n].pad[0] = dst[n].pad[1] = dst[n].pad[2] = 0;
        g_pos++;
        n++;
    }
    return n;
}

uint32_t mtp_midi_overruns(void)     { return g_overruns; }
uint32_t mtp_midi_frame_errors(void) { return 0; }
int      mtp_midi_eof(void)          { return g_data == NULL || g_pos >= g_len; }
