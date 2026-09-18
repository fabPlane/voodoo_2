/* desktop_status.c - see desktop_status.h, especially the part about the RTF.
 * SPDX-License-Identifier: 0BSD */

#include "desktop_status.h"

#include <stdio.h>
#include <string.h>

#if defined(__linux__) || defined(__APPLE__)
#include <unistd.h>
#define HAVE_ISATTY 1
#endif

static unsigned g_interval_ms = 500;
static int      g_enabled = 1;
static int      g_line_open;        /* a \r-terminated line is on screen */
static uint64_t g_last_wall;
static uint64_t g_last_audio;
static uint64_t g_last_render;
static double   g_rtf_peak;
static double   g_rtf_now;

void desktop_status_init(unsigned interval_ms, int enabled)
{
    g_interval_ms = interval_ms ? interval_ms : 500u;
    g_enabled = enabled;
#ifdef HAVE_ISATTY
    if (g_enabled && !isatty(2)) {
        /* Redirected to a file: keep the numbers, drop the carriage returns. */
        g_enabled = 2;
    }
#endif
}

void desktop_status_clear_line(void)
{
    if (g_line_open) {
        fputs("\r                                                             "
              "                          \r", stderr);
        g_line_open = 0;
    }
}

void desktop_status_tick(const desktop_status_sample *s)
{
    double d_audio, d_render;

    if (!g_enabled) return;
    if (s->wall_us - g_last_wall < (uint64_t)g_interval_ms * 1000ull) return;

    d_audio  = (double)(s->audio_us  - g_last_audio);
    d_render = (double)(s->render_us - g_last_render);
    if (d_audio > 0.0) {
        g_rtf_now = d_render / d_audio;
        if (g_rtf_now > g_rtf_peak) g_rtf_peak = g_rtf_now;
    }
    g_last_wall   = s->wall_us;
    g_last_audio  = s->audio_us;
    g_last_render = s->render_us;

    desktop_status_clear_line();
    fprintf(stderr,
            "%6.1fs  rtf %.3f (desktop, not the A7 gate)  ring %u  under %u  "
            "midi %uB %um %usx  err %u%s",
            (double)s->audio_us / 1e6,
            g_rtf_now,
            s->queued,
            s->underruns,
            s->midi_bytes, s->short_msgs, s->sysex_msgs,
            s->midi_overruns + s->parse_dropped + s->parse_truncated
                + s->parse_aborted,
            g_enabled == 2 ? "\n" : "\r");
    if (g_enabled != 2) g_line_open = 1;
    fflush(stderr);
}

void desktop_status_summary(const desktop_status_sample *s,
                            const char *engine_name,
                            uint32_t sample_rate,
                            uint32_t frames_per_block,
                            uint32_t ring_blocks)
{
    double audio_s = (double)s->audio_us / 1e6;
    double rtf_avg = s->audio_us ? (double)s->render_us / (double)s->audio_us : 0.0;
    double worst_block_rtf = s->block_period_us
        ? (double)s->worst_render_us / (double)s->block_period_us : 0.0;

    desktop_status_clear_line();

    printf("\n--- run ---\n");
    printf("engine               %s\n", engine_name);
    printf("output               %u Hz, %u frames/block (%.2f ms), ring %u\n",
           sample_rate, frames_per_block,
           (double)s->block_period_us / 1000.0, ring_blocks);
    printf("audio produced       %.3f s in %u blocks\n", audio_s, s->blocks);
    printf("wall time            %.3f s\n", (double)s->wall_us / 1e6);
    printf("\n");
    printf("underruns            %u%s\n", s->underruns,
           s->underruns ? "   <-- audible dropouts" : "");
    printf("min ring occupancy   %u of %u%s\n",
           s->min_queued == 0xFFFFFFFFu ? 0u : s->min_queued, ring_blocks,
           (s->min_queued != 0xFFFFFFFFu && s->min_queued == 0u)
               ? "   <-- one bad block from a click" : "");
    printf("worst block render   %u us of a %u us budget (%.2f of one block)\n",
           s->worst_render_us, s->block_period_us, worst_block_rtf);
    printf("\n");
    printf("midi bytes           %u\n", s->midi_bytes);
    printf("messages             %u short, %u sysex, %u realtime\n",
           s->short_msgs, s->sysex_msgs, s->realtime_msgs);
    printf("midi fifo peak       %u bytes\n", s->ring_peak);
    printf("midi fifo overruns   %u\n", s->midi_overruns);
    printf("parse: orphan data   %u\n", s->parse_dropped);
    printf("parse: sysex > 32 kB %u\n", s->parse_truncated);
    printf("parse: sysex aborted %u\n", s->parse_aborted);
    printf("engine back-pressure %u\n", s->backpressure);
    printf("\n");
    printf("real-time factor     %.4f average, %.4f peak over a "
           "%u ms window\n", rtf_avg, g_rtf_peak, g_interval_ms);
    printf("  This is CPU time inside the render loop divided by the audio it\n"
           "  produced, measured on THIS machine. It is not the number in\n"
           "  docs/PLAN.md section 0. That gate is 0.5-0.6 on one Cortex-A7 at\n"
           "  1.2 GHz with the PCM ROM in DDR3, and nothing measured on an x86\n"
           "  or Apple-silicon desktop predicts it: different ISA, different\n"
           "  cache, different memory system. Use bench/rtf on real silicon.\n");
}
