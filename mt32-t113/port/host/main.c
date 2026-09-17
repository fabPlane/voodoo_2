/* main.c - host harness for the mt32-t113 platform layer.
 *
 * Wires the five stubs to the portable render loop and runs it. The point is
 * that everything below main() -- the ring discipline, the MIDI parser, the
 * timestamping, the back-pressure handling -- is the *same code* that will run
 * on the T113, with a different set of five files underneath.
 *
 * SPDX-License-Identifier: 0BSD */

#include "mtp_platform.h"
#include "mtp_audio.h"
#include "mtp_midi.h"
#include "mtp_storage.h"
#include "mtp_time.h"
#include "mtp_log.h"
#include "mtp_engine.h"
#include "mtp_render.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void host_audio_set_path(const char *p);
void host_audio_set_realtime(int on);
void host_midi_set_path(const char *p);
void host_midi_set_paced(int on);
void host_storage_set_root(const char *r);

static void usage(const char *argv0)
{
    fprintf(stderr,
"usage: %s [options]\n"
"  --midi FILE        raw MIDI byte stream to play (default: built-in demo)\n"
"  --wav FILE         output WAV (default: out.wav)\n"
"  --root DIR         storage root for ROMs/config (default: .)\n"
"  --engine NAME      fake"
#ifdef MTP_WITH_MT32EMU
                             " | mt32emu"
#endif
                                        "  (default: fake)\n"
"  --control-rom P    control ROM path, relative to --root\n"
"  --pcm-rom P        PCM ROM path, relative to --root\n"
"  --rate HZ          output sample rate (default 48000)\n"
"  --block N          frames per audio block (default 128)\n"
"  --ring N           ring depth in blocks (default 3)\n"
"  --seconds S        stop after S seconds of audio (default 4)\n"
"  --realtime         pace the sink and the MIDI source in real time\n"
"  --lookahead N      schedule events N output frames ahead (default 0)\n"
"  -v                 verbose\n", argv0);
}

/* A short built-in stream so the harness does something useful with no
 * arguments: a C major arpeggio using running status, then a sysex that sets
 * an MT-32 display message, then note offs. Bytes exactly as a wire carries
 * them. */
static const uint8_t DEMO[] = {
    0x90, 60, 100,          /* note on, status given                       */
          64, 100,          /* running status                              */
          67, 100,
          72, 100,
    /* MT-32 display: F0 41 10 16 12 20 00 00 <20 chars> <sum> F7           */
    0xF0, 0x41, 0x10, 0x16, 0x12, 0x20, 0x00, 0x00,
    'H','E','L','L','O',' ','M','T','-','3','2',' ',' ',' ',' ',' ',' ',' ',' ',' ',
    0x00, 0xF7,
    0xFE,                   /* active sensing, mid-stream                  */
    0x80, 60, 0,
          64, 0,
          67, 0,
          72, 0
};

static int write_demo(const char *path)
{
    FILE *fp = fopen(path, "wb");
    if (!fp) return -1;
    fwrite(DEMO, 1, sizeof(DEMO), fp);
    fclose(fp);
    return 0;
}

int main(int argc, char **argv)
{
    const char *midi_path = NULL, *wav_path = "out.wav", *root = ".";
    const char *engine_name = "fake";
    const char *control_rom = NULL, *pcm_rom = NULL;
    uint32_t rate = 48000, block = 128, ring = 3, lookahead = 0;
    double seconds = 4.0;
    int realtime = 0, verbose = 0, i;
    char demo_path[512];

    const mtp_engine_vtable *vt = &mtp_engine_fake;
    mtp_engine *inst = NULL;
    mtp_engine_config ecfg;
    mtp_audio_config acfg;
    static mtp_render_ctx ctx;   /* 64 kB of sysex buffers: not on the stack */
    mtp_status s;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--midi") && i + 1 < argc)         midi_path = argv[++i];
        else if (!strcmp(argv[i], "--wav") && i + 1 < argc)     wav_path = argv[++i];
        else if (!strcmp(argv[i], "--root") && i + 1 < argc)    root = argv[++i];
        else if (!strcmp(argv[i], "--engine") && i + 1 < argc)  engine_name = argv[++i];
        else if (!strcmp(argv[i], "--control-rom") && i+1<argc) control_rom = argv[++i];
        else if (!strcmp(argv[i], "--pcm-rom") && i + 1 < argc) pcm_rom = argv[++i];
        else if (!strcmp(argv[i], "--rate") && i + 1 < argc)    rate = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--block") && i + 1 < argc)   block = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--ring") && i + 1 < argc)    ring = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = atof(argv[++i]);
        else if (!strcmp(argv[i], "--lookahead") && i+1<argc)   lookahead = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--realtime"))                realtime = 1;
        else if (!strcmp(argv[i], "-v"))                        verbose = 1;
        else { usage(argv[0]); return 2; }
    }

    mtp_time_init();
    mtp_log_init(verbose ? MTP_LOG_DEBUG : MTP_LOG_INFO);

    if (!strcmp(engine_name, "fake")) {
        vt = &mtp_engine_fake;
#ifdef MTP_WITH_MT32EMU
    } else if (!strcmp(engine_name, "mt32emu")) {
        vt = &mtp_engine_mt32emu;
#endif
    } else {
        MTP_LOGE("unknown engine '%s'", engine_name);
        return 2;
    }

    host_storage_set_root(root);
    if (mtp_storage_mount() != MTP_OK) { MTP_LOGE("mount failed"); return 1; }

    if (!midi_path) {
        snprintf(demo_path, sizeof(demo_path), "%s", "demo.syx");
        if (write_demo(demo_path) != 0) { MTP_LOGE("cannot write demo"); return 1; }
        midi_path = demo_path;
        MTP_LOGI("no --midi given, using built-in demo stream (%s)", demo_path);
    }

    host_audio_set_path(wav_path);
    host_audio_set_realtime(realtime);
    host_midi_set_path(midi_path);
    host_midi_set_paced(realtime);

    memset(&ecfg, 0, sizeof(ecfg));
    ecfg.control_rom_path = control_rom;
    ecfg.pcm_rom_path     = pcm_rom;
    ecfg.output_rate      = rate;
    ecfg.max_partials     = 32;
    ecfg.reverb_enabled   = 1;

    s = vt->open(&ecfg, &inst);
    if (s != MTP_OK) { MTP_LOGE("engine open: %s", mtp_strerror(s)); return 1; }

    acfg.sample_rate      = rate;
    acfg.channels         = 2;
    acfg.frames_per_block = (uint16_t)block;
    acfg.block_count      = (uint8_t)ring;
    s = mtp_audio_open(&acfg);
    if (s != MTP_OK) { MTP_LOGE("audio open: %s", mtp_strerror(s)); return 1; }

    s = mtp_midi_open(31250);
    if (s != MTP_OK) { MTP_LOGE("midi open: %s", mtp_strerror(s)); return 1; }

    s = mtp_render_init(&ctx, vt, inst, block, rate, ring - 1u, lookahead);
    if (s != MTP_OK) { MTP_LOGE("render init: %s", mtp_strerror(s)); return 1; }

    {
        uint32_t max_blocks = (uint32_t)(seconds * rate / block);
        uint64_t t0 = mtp_time_us64(), wall;
        mtp_render_run(&ctx, max_blocks);
        wall = mtp_time_us64() - t0;

        printf("\n--- run ---\n");
        printf("engine              %s\n", vt->name);
        printf("output              %u Hz, %u frames/block, ring %u\n",
               rate, block, ring);
        printf("blocks committed    %u  (%.3f s of audio)\n",
               ctx.stats.blocks,
               (double)ctx.stats.blocks * block / (double)rate);
        printf("wall time           %.3f s\n", (double)wall / 1e6);
        if (wall)
            printf("real-time factor    %.3f\n",
                   ((double)wall / 1e6) /
                   ((double)ctx.stats.blocks * block / (double)rate));
        printf("midi bytes          %u\n", ctx.stats.midi_bytes);
        printf("short messages      %u\n", ctx.stats.short_msgs);
        printf("sysex messages      %u\n", ctx.stats.sysex_msgs);
        printf("parser: short %u sysex %u realtime %u\n",
               ctx.parser.stat_short, ctx.parser.stat_sysex,
               ctx.parser.stat_realtime);
        printf("parser: orphan data %u, sysex truncated %u, sysex aborted %u\n",
               ctx.parser.stat_dropped_data, ctx.parser.stat_sysex_truncated,
               ctx.parser.stat_sysex_aborted);
        printf("engine back-pressure %u\n", ctx.stats.engine_backpressure);
        printf("underruns           %u\n", ctx.stats.underruns);
        printf("worst render        %u us  (block period %.0f us)\n",
               ctx.stats.worst_render_us,
               1e6 * (double)block / (double)rate);
        printf("min ring occupancy  %u of %u\n",
               ctx.stats.min_queued == 0xFFFFFFFFu ? 0 : ctx.stats.min_queued,
               ring);
        printf("wav                 %s\n", wav_path);
    }

    mtp_midi_close();
    mtp_audio_close();
    vt->close(inst);
    mtp_storage_unmount();
    return ctx.stats.underruns ? 1 : 0;
}
