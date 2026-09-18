/* main.c - mt32-desktop: the port, running on a laptop, out of the speakers.
 *
 * Everything between the MIDI socket and the sound card here is the same code
 * that will run on the T113: port/src/mtp_midi_parser.c, port/src/mtp_render.c,
 * and port/host/engine_mt32emu.cpp. What is different is the five files under
 * desktop/src that implement port/include -- a sound card instead of I2S+DMA, a
 * tty or a sequencer instead of UART2, a directory instead of FAT on microSD.
 * That is the whole of docs/PLAN.md section 0.5 in one binary: if the ring
 * discipline, the sysex reassembly or the back-pressure handling is wrong, it is
 * wrong here too, where you can hear it.
 *
 * What it is NOT is evidence about speed. See desktop_status.h.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "mtp_platform.h"
#include "mtp_audio.h"
#include "mtp_midi.h"
#include "mtp_storage.h"
#include "mtp_time.h"
#include "mtp_log.h"
#include "mtp_engine.h"
#include "mtp_render.h"

#include "desktop_audio.h"
#include "desktop_midi.h"
#include "desktop_status.h"

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void desktop_storage_set_root(const char *r);

static volatile sig_atomic_t g_quit;
static void on_signal(int sig) { (void)sig; g_quit = 1; }

static void usage(const char *argv0)
{
    printf(
"mt32-desktop -- the mt32-t113 port, on a desktop, in real time\n"
"\n"
"usage: %s [options]\n"
"\n"
"MIDI in (any number, all at once):\n"
"  --midi-tty DEV[,BAUD]  serial line, default 31250 baud. The same bytes the\n"
"                         T113's UART2 will see: a USB-serial adapter, an\n"
"                         opto-isolated DIN input, an MPU-401 game port cable\n"
"  --midi-seq [SRC]       OS MIDI port: ALSA sequencer on Linux, CoreMIDI on\n"
"                         macOS. SRC is a client:port, or a name to match\n"
"  --midi-fifo PATH       named pipe (created if absent); '-' means stdin\n"
"  --midi-smf FILE        play a Standard MIDI File and exit\n"
"  --midi-loop            keep replaying the SMF\n"
"  --list-midi            list the OS MIDI ports that can send to us\n"
"\n"
"Audio out:\n"
"  --audio BACKEND        auto (default), alsa, pulse, jack, coreaudio, null\n"
"  --device NAME          substring of the playback device name\n"
"  --periods N            device periods behind our ring (2..8, default 3)\n"
"  --tap-wav FILE         also write everything played to a WAV file\n"
"  --list-devices         list playback devices and exit\n"
"\n"
"Engine and ROMs:\n"
"  --engine NAME          auto (default), fake"
#ifdef MTP_WITH_MT32EMU
                                              ", mt32emu"
#endif
                                                        "\n"
"  --roms DIR             directory holding the ROM images\n"
"  --machine NAME         mt32 (default) or cm32l; picks the ROM file names\n"
"  --control-rom PATH     control ROM, overriding --roms/--machine\n"
"  --pcm-rom PATH         PCM ROM, overriding --roms/--machine\n"
"  --no-reverb            open the synth with reverb off\n"
"  --partials N           partial limit, 32 is a real MT-32 (default 32)\n"
"\n"
"Timing:\n"
"  --rate HZ              48000 (default) or 32000; see port/PORTING.md\n"
"  --block N              frames per block, default 128 (2.67 ms at 48 kHz)\n"
"  --ring N               ring depth in blocks, default 3\n"
"  --lookahead N          schedule events N frames ahead, default 0\n"
"  --seconds S            stop after S seconds (0 = until Ctrl-C)\n"
"\n"
"Reporting:\n"
"  --status-ms N          status line interval, default 500; 0 turns it off\n"
"  -v                     debug logging\n"
"  -q                     errors only\n",
    argv0);
}

int main(int argc, char **argv)
{
    const char *engine_name = "auto";
    const char *roms_dir = "roms";
    const char *machine = "mt32";
    const char *control_rom = NULL, *pcm_rom = NULL;
    const char *audio_backend = "auto", *audio_device = NULL, *tap = NULL;
    const char *smf = NULL;
    int   smf_loop = 0, reverb = 1, verbose = 0, quiet = 0, i;
    uint32_t rate = 48000, block = 128, ring = 3, lookahead = 0, partials = 32;
    unsigned periods = 3, status_ms = 500;
    double seconds = 0.0;
    int have_source = 0;

    char ctrl_buf[1024], pcm_buf[1024];
    const mtp_engine_vtable *vt = NULL;
    mtp_engine *inst = NULL;
    mtp_engine_config ecfg;
    mtp_audio_config acfg;
    static mtp_render_ctx ctx;       /* 64 kB of sysex buffers: not on the stack */
    desktop_status_sample st;
    mtp_status s;
    uint64_t t_start, render_us = 0;

    /* Sources are registered as the arguments are read, so the log reads in the
     * order the user wrote them. Everything else is applied after parsing. */
    mtp_time_init();
    mtp_log_init(MTP_LOG_INFO);

    for (i = 1; i < argc; i++) {
        const char *a = argv[i];
        #define NEXT(var) do { if (i + 1 >= argc) { MTP_LOGE("%s needs a value", a); return 2; } var = argv[++i]; } while (0)
        if (!strcmp(a, "--help") || !strcmp(a, "-h")) { usage(argv[0]); return 0; }
        else if (!strcmp(a, "-v")) verbose = 1;
        else if (!strcmp(a, "-q")) quiet = 1;
        else if (!strcmp(a, "--audio"))      NEXT(audio_backend);
        else if (!strcmp(a, "--device"))     NEXT(audio_device);
        else if (!strcmp(a, "--tap-wav"))    NEXT(tap);
        else if (!strcmp(a, "--engine"))     NEXT(engine_name);
        else if (!strcmp(a, "--roms"))       NEXT(roms_dir);
        else if (!strcmp(a, "--machine"))    NEXT(machine);
        else if (!strcmp(a, "--control-rom"))NEXT(control_rom);
        else if (!strcmp(a, "--pcm-rom"))    NEXT(pcm_rom);
        else if (!strcmp(a, "--no-reverb"))  reverb = 0;
        else if (!strcmp(a, "--midi-loop"))  smf_loop = 1;
        else if (!strcmp(a, "--midi-smf"))   NEXT(smf);
        else if (!strcmp(a, "--periods")) { const char *v; NEXT(v); periods = (unsigned)atoi(v); }
        else if (!strcmp(a, "--partials")){ const char *v; NEXT(v); partials = (uint32_t)atoi(v); }
        else if (!strcmp(a, "--rate"))    { const char *v; NEXT(v); rate = (uint32_t)atoi(v); }
        else if (!strcmp(a, "--block"))   { const char *v; NEXT(v); block = (uint32_t)atoi(v); }
        else if (!strcmp(a, "--ring"))    { const char *v; NEXT(v); ring = (uint32_t)atoi(v); }
        else if (!strcmp(a, "--lookahead")){const char *v; NEXT(v); lookahead = (uint32_t)atoi(v); }
        else if (!strcmp(a, "--seconds")) { const char *v; NEXT(v); seconds = atof(v); }
        else if (!strcmp(a, "--status-ms")){const char *v; NEXT(v); status_ms = (unsigned)atoi(v); }
        else if (!strcmp(a, "--list-devices")) {
            desktop_audio_set_backend(audio_backend);
            desktop_audio_list_devices();
            return 0;
        }
        else if (!strcmp(a, "--list-midi")) { desktop_midi_list_ports(); return 0; }
        else if (!strcmp(a, "--midi-tty")) {
            const char *v; char dev[512]; char *comma; uint32_t baud = 31250;
            NEXT(v);
            snprintf(dev, sizeof(dev), "%s", v);
            comma = strchr(dev, ',');
            if (comma) { *comma = 0; baud = (uint32_t)atoi(comma + 1); }
            if (desktop_midi_add_tty(dev, baud) != MTP_OK) return 1;
            have_source = 1;
        }
        else if (!strcmp(a, "--midi-fifo")) {
            const char *v; NEXT(v);
            if (desktop_midi_add_fifo(v) != MTP_OK) return 1;
            have_source = 1;
        }
        else if (!strcmp(a, "--midi-seq")) {
            const char *v = NULL;
            if (i + 1 < argc && argv[i + 1][0] != '-') v = argv[++i];
            if (desktop_midi_add_seq(v) != MTP_OK) return 1;
            have_source = 1;
        }
        else { MTP_LOGE("unknown option '%s' (try --help)", a); return 2; }
        #undef NEXT
    }

    mtp_log_init(verbose ? MTP_LOG_DEBUG : (quiet ? MTP_LOG_ERROR : MTP_LOG_INFO));
    desktop_status_init(status_ms, status_ms != 0u);

    if (smf) {
        if (desktop_midi_add_smf(smf, smf_loop) != MTP_OK) return 1;
        have_source = 1;
    }
    if (!have_source) {
        MTP_LOGW("no MIDI source given. Nothing will play; --help lists them.");
#if defined(__linux__) || defined(__APPLE__)
        MTP_LOGW("  the usual one:  --midi-seq");
#endif
    }

    if (ring < 2u || ring > 16u)    { MTP_LOGE("--ring must be 2..16"); return 2; }
    if (block < 16u || block > 4096u){ MTP_LOGE("--block must be 16..4096"); return 2; }

    /* ---- storage and ROM paths ---------------------------------------- */

    desktop_storage_set_root(".");
    mtp_storage_mount();

    if (!control_rom || !pcm_rom) {
        const char *cn = !strcmp(machine, "cm32l") ? "CM32L_CONTROL.ROM"
                                                   : "MT32_CONTROL.ROM";
        const char *pn = !strcmp(machine, "cm32l") ? "CM32L_PCM.ROM"
                                                   : "MT32_PCM.ROM";
        snprintf(ctrl_buf, sizeof(ctrl_buf), "%s/%s", roms_dir, cn);
        snprintf(pcm_buf,  sizeof(pcm_buf),  "%s/%s", roms_dir, pn);
        if (!control_rom) control_rom = ctrl_buf;
        if (!pcm_rom)     pcm_rom     = pcm_buf;
    }

    /* ---- engine -------------------------------------------------------- */

    if (!strcmp(engine_name, "fake")) {
        vt = &mtp_engine_fake;
#ifdef MTP_WITH_MT32EMU
    } else if (!strcmp(engine_name, "mt32emu")) {
        vt = &mtp_engine_mt32emu;
    } else if (!strcmp(engine_name, "auto")) {
        if (mtp_storage_exists(control_rom) && mtp_storage_exists(pcm_rom)) {
            vt = &mtp_engine_mt32emu;
        } else {
            MTP_LOGW("no ROMs at '%s' and '%s': falling back to the fake engine.",
                     control_rom, pcm_rom);
            MTP_LOGW("  It is eight sine voices, not an MT-32. It exists so the");
            MTP_LOGW("  ring, the parser and the audio path can be exercised");
            MTP_LOGW("  without Roland's ROMs. See desktop/README.md.");
            vt = &mtp_engine_fake;
        }
#else
    } else if (!strcmp(engine_name, "auto")) {
        MTP_LOGW("this build has no mt32emu (configure with -DMT32EMU_SOURCE_DIR)");
        vt = &mtp_engine_fake;
    } else if (!strcmp(engine_name, "mt32emu")) {
        MTP_LOGE("this build has no mt32emu. Reconfigure with");
        MTP_LOGE("  -DMT32EMU_SOURCE_DIR=../bench/vendor/munt/mt32emu");
        return 2;
#endif
    } else {
        MTP_LOGE("unknown engine '%s'", engine_name);
        return 2;
    }

    memset(&ecfg, 0, sizeof(ecfg));
    ecfg.control_rom_path = control_rom;
    ecfg.pcm_rom_path     = pcm_rom;
    ecfg.output_rate      = rate;
    ecfg.max_partials     = partials;
    ecfg.reverb_enabled   = reverb;

    s = vt->open(&ecfg, &inst);
    if (s != MTP_OK) {
        MTP_LOGE("engine '%s' did not open: %s", vt->name, mtp_strerror(s));
        MTP_LOGE("  ROMs are Roland's and are not in this repository. Dump your");
        MTP_LOGE("  own from hardware, put them in %s/, and try again;", roms_dir);
        MTP_LOGE("  or run with --engine fake to exercise everything but the sound.");
        return 1;
    }

    /* ---- audio --------------------------------------------------------- */

    desktop_audio_set_backend(audio_backend);
    if (audio_device) desktop_audio_set_device(audio_device);
    desktop_audio_set_periods(periods);
    if (tap && desktop_audio_set_tap(tap) != MTP_OK) return 1;

    acfg.sample_rate      = rate;
    acfg.channels         = 2;
    acfg.frames_per_block = (uint16_t)block;
    acfg.block_count      = (uint8_t)ring;
    s = mtp_audio_open(&acfg);
    if (s != MTP_OK) {
        MTP_LOGE("audio did not open: %s", mtp_strerror(s));
        MTP_LOGE("  headless? try --audio null, which paces in real time and");
        MTP_LOGE("  plays nothing. Add --tap-wav out.wav to keep the audio.");
        return 1;
    }

    s = mtp_midi_open(31250);
    if (s != MTP_OK) { MTP_LOGE("midi did not open: %s", mtp_strerror(s)); return 1; }

    s = mtp_render_init(&ctx, vt, inst, block, rate, ring - 1u, lookahead);
    if (s != MTP_OK) { MTP_LOGE("render init: %s", mtp_strerror(s)); return 1; }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    {
        unsigned n;
        for (n = 0; n < desktop_midi_source_count(); n++)
            MTP_LOGI("source %u: %s", n, desktop_midi_source_desc(n));
    }
    MTP_LOGI("running. Ctrl-C to stop.");

    /* ---- the loop ------------------------------------------------------ */

    memset(&st, 0, sizeof(st));
    st.block_period_us = (uint32_t)((1000000ull * block) / rate);
    t_start = mtp_time_us64();

    for (;;) {
        uint64_t t0, t1;
        unsigned produced;

        if (g_quit) break;

        t0 = mtp_time_us64();
        produced = mtp_render_pump(&ctx, 8);
        t1 = mtp_time_us64();

        if (produced > 0u) {
            render_us += (t1 - t0);
        } else {
            /* The ring is full: sleep until the device has taken a block. On
             * the T113 this is WFI woken by the DMA completion interrupt. */
            if (mtp_audio_wait(50000u) != MTP_OK && !mtp_midi_eof())
                MTP_LOGW("audio device has not asked for a block in 50 ms");
        }

        st.wall_us    = mtp_time_us64() - t_start;
        st.audio_us   = (uint64_t)ctx.stats.blocks * 1000000ull * block / rate;
        st.render_us  = render_us;
        st.blocks     = ctx.stats.blocks;
        st.underruns  = mtp_audio_underruns();
        st.queued     = mtp_audio_queued();
        st.min_queued = ctx.stats.min_queued;
        st.worst_render_us = ctx.stats.worst_render_us;
        st.midi_bytes = ctx.stats.midi_bytes;
        st.short_msgs = ctx.parser.stat_short;
        st.sysex_msgs = ctx.parser.stat_sysex;
        st.realtime_msgs   = ctx.parser.stat_realtime;
        st.midi_overruns   = mtp_midi_overruns();
        st.parse_dropped   = ctx.parser.stat_dropped_data;
        st.parse_truncated = ctx.parser.stat_sysex_truncated;
        st.parse_aborted   = ctx.parser.stat_sysex_aborted;
        st.backpressure    = ctx.stats.engine_backpressure;
        st.ring_peak       = desktop_midi_ring_peak();
        desktop_status_tick(&st);

        if (seconds > 0.0 && (double)st.audio_us / 1e6 >= seconds) break;

        /* A file source that has run out ends the run, after enough silence
         * for release tails and reverb to finish. A wire never ends. */
        if (mtp_midi_eof()) {
            static uint64_t quiet_from;
            if (!quiet_from) quiet_from = st.audio_us;
            if (st.audio_us - quiet_from > 2000000ull) break;
        }
    }

    {
        uint32_t calls = 0, maxf = 0;
        desktop_audio_callback_stats(&calls, &maxf);
        uint32_t waits = 0, tmo = 0, worst = 0;
        desktop_audio_wait_stats(&waits, &tmo, &worst);
        MTP_LOGI("device asked for audio %u times, at most %u frames at once",
                 calls, maxf);
        MTP_LOGI("render loop slept %u times, %u woke on the timer, worst %u us",
                 waits, tmo, worst);
    }
    desktop_status_summary(&st, vt->name, rate, block, ring);

    mtp_midi_close();
    mtp_audio_close();
    vt->close(inst);
    mtp_storage_unmount();

    return st.underruns ? 1 : 0;
}
