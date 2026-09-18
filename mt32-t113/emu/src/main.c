/* main.c - the bare-metal harness.
 *
 * Deliberately the same program as port/host/main.c: same arguments, same
 * sequence, and -- this is the contract -- the same printed counters, in the
 * same words, so that port/host/test.sh's assertions can be made against a
 * Cortex-A7 booting from reset. docs/PLAN.md section 0.5 rule 2.
 *
 * Everything between mtp_render_init() and the counter dump is code from
 * port/src, compiled unchanged. The five platform files under it are this
 * directory's; the five under port/host are the host's; the T113's will be a
 * third set. Nothing above the seam knows which.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>

#include "mtp_platform.h"
#include "mtp_audio.h"
#include "mtp_midi.h"
#include "mtp_storage.h"
#include "mtp_time.h"
#include "mtp_log.h"
#include "mtp_engine.h"
#include "mtp_render.h"

#include "emu.h"
#include "semihost.h"
#include "midi_vectors.h"

#ifdef MTP_WITH_MT32EMU
extern const mtp_engine_vtable mtp_engine_mt32emu_fakerom;
#endif

int printf(const char *fmt, ...);
size_t strlen(const char *s);
int strcmp(const char *a, const char *b);

void emu_midi_select(const char *name, int paced);
const char *emu_midi_name(void);
uint32_t emu_midi_length(void);
void emu_audio_set_wav(const char *path);
void emu_audio_set_sink(const char *name);
const char *emu_audio_sink_name(void);
uint32_t emu_audio_virtio_completed(void);
uint32_t emu_audio_virtio_max_burst(void);
uint32_t emu_audio_dma_used(void);
void emu_storage_set_root(const char *r);
uint32_t emu_audio_checksum(void);
uint32_t emu_audio_played(void);

extern char __image_start[], __image_end[], __bss_start[], __bss_end[];
extern char __data_start[], __data_end[], __heap_start[], __heap_end[];
extern char __dma_start[], __dma_end[];

/* 64 KB of sysex buffers inside: static, not on the stack, exactly as the host
 * harness does it. */
static mtp_render_ctx g_ctx;

/* ------------------------------------------------------------- arguments */

#define MAX_ARGS 24
static char  g_cmdline[512];
static char *g_argv[MAX_ARGS];
static int   g_argc;

static void split_cmdline(void)
{
    char *p = g_cmdline;
    g_argc = 0;
    /* The host hands us the whole command line including argv[0]; skip it so
     * the flags line up with port/host/main.c's. */
    while (*p && g_argc < MAX_ARGS) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) break;
        g_argv[g_argc++] = p;
        while (*p && *p != ' ' && *p != '\t') p++;
        if (*p) *p++ = '\0';
    }
}

/* Small parsers, because there is no strtoul here and pulling glibc's in
 * would mean pulling glibc in. */
static uint32_t atou(const char *s)
{
    uint32_t v = 0u;
    while (*s >= '0' && *s <= '9') v = v * 10u + (uint32_t)(*s++ - '0');
    return v;
}

/* "4", "1.5", "0.25" -> milliseconds*1000, i.e. thousandths of a second. */
static uint32_t atomilli(const char *s)
{
    uint32_t whole = 0u, frac = 0u, scale = 1000u;
    while (*s >= '0' && *s <= '9') whole = whole * 10u + (uint32_t)(*s++ - '0');
    if (*s == '.') {
        s++;
        while (*s >= '0' && *s <= '9' && scale > 1u) {
            scale /= 10u;
            frac += (uint32_t)(*s++ - '0') * scale;
        }
    }
    return whole * 1000u + frac;
}

static void print_entry_state(void)
{
    static const char *const MODE[32] = {
        /* 0x00..0x0F unused */
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        /* 0x10 */ "USR", "FIQ", "IRQ", "SVC", 0, 0, "MON", "ABT",
        /* 0x18 */ 0, 0, "HYP", "UND", 0, 0, 0, "SYS"
    };
    uint32_t cpsr = emu_entry_state[0];
    const char *m = MODE[cpsr & 0x1Fu];

    /* boot/BRINGUP.md section 4.4 asks for exactly this dump, because it is
     * what answers the secure-vs-non-secure question on real silicon. The same
     * code runs there. */
    printf("entry cpsr          0x%08x  mode %s  I=%u F=%u A=%u T=%u\n",
           cpsr, m ? m : "???",
           (cpsr >> 7) & 1u, (cpsr >> 6) & 1u, (cpsr >> 8) & 1u,
           (cpsr >> 5) & 1u);
    printf("entry sctlr         0x%08x  MMU=%u Dcache=%u Icache=%u align=%u\n",
           emu_entry_state[1], emu_entry_state[1] & 1u,
           (emu_entry_state[1] >> 2) & 1u, (emu_entry_state[1] >> 12) & 1u,
           (emu_entry_state[1] >> 1) & 1u);
    printf("entry actlr/vbar    0x%08x / 0x%08x\n",
           emu_entry_state[2], emu_entry_state[3]);
    printf("midr                0x%08x  part 0x%03x r%up%u\n",
           emu_entry_state[4], (emu_entry_state[4] >> 4) & 0xFFFu,
           (emu_entry_state[4] >> 20) & 0xFu, emu_entry_state[4] & 0xFu);
    printf("id_pfr1             0x%08x  security=%u virt=%u genTimer=%u\n",
           emu_entry_state[5], (emu_entry_state[5] >> 4) & 0xFu,
           (emu_entry_state[5] >> 12) & 0xFu, (emu_entry_state[5] >> 16) & 0xFu);
    printf("cntfrq at entry     %u Hz\n", emu_entry_state[6]);
    printf("loader r0/r1/r2     0x%08x / 0x%08x / 0x%08x\n",
           emu_entry_state[7], emu_entry_state[8], emu_entry_state[9]);
    printf("running at          0x%08x\n", emu_entry_state[10]);
}

/* ------------------------------------------------------------------ main */

int main(void)
{
    const char *midi_name = "demo";
    const char *wav_path = NULL, *root = ".", *sink = "timer";
    const char *engine_name = "fake";
    const char *control_rom = NULL, *pcm_rom = NULL;
    uint32_t rate = 48000u, block = 128u, ring = 3u, lookahead = 0u;
    uint32_t seconds_x1000 = 4000u;
    int realtime = 0, verbose = 0, i;
    uint32_t heap_after_open = 0u;

    const mtp_engine_vtable *vt = &mtp_engine_fake;
    mtp_engine *inst = NULL;
    mtp_engine_config ecfg;
    mtp_audio_config acfg;
    mtp_status s;

    emu_console_init();
    mtp_time_init();
    mtp_log_init(MTP_LOG_INFO);
    emu_gic_init();

    /* Arguments come over semihosting; with none we behave like the host
     * harness with no arguments. */
    if (semihost_cmdline(g_cmdline, sizeof(g_cmdline)) > 0) split_cmdline();

    for (i = 1; i < g_argc; i++) {
        const char *a = g_argv[i];
        if      (!strcmp(a, "--midi")   && i + 1 < g_argc) midi_name = g_argv[++i];
        else if (!strcmp(a, "--wav")    && i + 1 < g_argc) wav_path = g_argv[++i];
        else if (!strcmp(a, "--sink")   && i + 1 < g_argc) sink = g_argv[++i];
        else if (!strcmp(a, "--root")   && i + 1 < g_argc) root = g_argv[++i];
        else if (!strcmp(a, "--engine") && i + 1 < g_argc) engine_name = g_argv[++i];
        else if (!strcmp(a, "--control-rom") && i + 1 < g_argc) control_rom = g_argv[++i];
        else if (!strcmp(a, "--pcm-rom") && i + 1 < g_argc) pcm_rom = g_argv[++i];
        else if (!strcmp(a, "--rate")   && i + 1 < g_argc) rate = (uint32_t)atou(g_argv[++i]);
        else if (!strcmp(a, "--block")  && i + 1 < g_argc) block = (uint32_t)atou(g_argv[++i]);
        else if (!strcmp(a, "--ring")   && i + 1 < g_argc) ring = (uint32_t)atou(g_argv[++i]);
        else if (!strcmp(a, "--seconds")&& i + 1 < g_argc) seconds_x1000 = atomilli(g_argv[++i]);
        else if (!strcmp(a, "--lookahead") && i + 1 < g_argc) lookahead = (uint32_t)atou(g_argv[++i]);
        else if (!strcmp(a, "--realtime")) realtime = 1;
        else if (!strcmp(a, "-v")) verbose = 1;
    }
    if (verbose) mtp_log_set_level(MTP_LOG_DEBUG);

    printf("\n=== mt32-t113 bare-metal platform layer ===\n");
    print_entry_state();
    printf("mmu now             %s\n",
#if defined(EMU_MMU) && EMU_MMU
           "on, flat 1:1, D+I cache on, audio ring Non-cacheable"
#else
           "OFF (bootm handover state, untouched) -- all timings are meaningless"
#endif
          );
    printf("timebase            %u Hz generic timer (CNTPCT)\n",
           emu_timer_frequency());
    printf("loaded image        0x%08x..0x%08x  %u KiB (text+rodata+data)\n",
           (unsigned)(uintptr_t)__image_start, (unsigned)(uintptr_t)__data_end,
           (unsigned)((__data_end - __image_start) / 1024));
    printf("reserved footprint  0x%08x..0x%08x  %u KiB (image+bss+stacks+heap+dma)\n",
           (unsigned)(uintptr_t)__image_start, (unsigned)(uintptr_t)__image_end,
           (unsigned)((__image_end - __image_start) / 1024));
    printf("  .data/.bss        %u B / %u B\n",
           (unsigned)(__data_end - __data_start),
           (unsigned)(__bss_end - __bss_start));
    printf("  heap              0x%08x, %u KiB\n",
           (unsigned)(uintptr_t)__heap_start, emu_heap_size() / 1024u);
    printf("  uncached window   0x%08x, %u KiB\n",
           (unsigned)(uintptr_t)__dma_start,
           (unsigned)((__dma_end - __dma_start) / 1024));
    printf("\n");

    if (!strcmp(engine_name, "fake")) {
        vt = &mtp_engine_fake;
#ifdef MTP_WITH_MT32EMU
    } else if (!strcmp(engine_name, "mt32emu")) {
        vt = &mtp_engine_mt32emu;
    } else if (!strcmp(engine_name, "mt32emu-fakerom")) {
        /* The real library on fabricated ROMs: no Roland data, but every line
         * of the emulator actually runs. See src/engine_mt32emu_fake_roms.c++ */
        vt = &mtp_engine_mt32emu_fakerom;
#endif
    } else {
        MTP_LOGE("unknown engine '%s' (this image has: fake"
#ifdef MTP_WITH_MT32EMU
                 ", mt32emu, mt32emu-fakerom"
#endif
                 ")", engine_name);
        emu_exit(2);
    }

    emu_storage_set_root(root);
    if (mtp_storage_mount() != MTP_OK) { MTP_LOGE("mount failed"); emu_exit(1); }

    emu_midi_select(midi_name, realtime);
    if (emu_midi_length() == 0u) {
        MTP_LOGE("no such built-in stream '%s'; have: demo bank bad", midi_name);
        emu_exit(2);
    }
    if (wav_path) emu_audio_set_wav(wav_path);
    emu_audio_set_sink(sink);

    ecfg.control_rom_path = control_rom;
    ecfg.pcm_rom_path     = pcm_rom;
    ecfg.output_rate      = rate;
    ecfg.max_partials     = 32u;
    ecfg.reverb_enabled   = 1;

    s = vt->open(&ecfg, &inst);
    if (s != MTP_OK) {
        /* port/DESIGN.md section 4.3: a module that is quiet and talkative
         * over serial is debuggable; one that is bricked is not. This is the
         * ROMs-absent path, and it is the normal one. */
        MTP_LOGE("engine open: %s", mtp_strerror(s));
        printf("\n--- run ---\n");
        printf("engine              %s (FAILED TO OPEN)\n", vt->name);
        printf("heap high water     %u B of %u B\n",
               emu_heap_high_water(), emu_heap_size());
        printf("--- end ---\n");
        emu_exit(1);
    }

    acfg.sample_rate      = rate;
    acfg.channels         = 2u;
    acfg.frames_per_block = (uint16_t)block;
    acfg.block_count      = (uint8_t)ring;
    s = mtp_audio_open(&acfg);
    if (s != MTP_OK) { MTP_LOGE("audio open: %s", mtp_strerror(s)); emu_exit(1); }

    s = mtp_midi_open(31250u);
    if (s != MTP_OK) { MTP_LOGE("midi open: %s", mtp_strerror(s)); emu_exit(1); }

    s = mtp_render_init(&g_ctx, vt, inst, block, rate, ring - 1u, lookahead);
    if (s != MTP_OK) { MTP_LOGE("render init: %s", mtp_strerror(s)); emu_exit(1); }

    /* port/PORTING.md section 3 measured, on x86-64, that the render path
     * performs zero heap operations once preallocateReverbMemory(true) and
     * configureMIDIEventQueueSysexStorage() have been called. Capture the
     * arena after everything is open, so "heap grown by run" below is
     * render-path allocation and nothing else. */
    heap_after_open = emu_heap_used();

    {
        uint32_t max_blocks = (uint32_t)(((uint64_t)seconds_x1000 * rate)
                                         / (1000ull * block));
        uint64_t t0 = mtp_time_us64(), wall;
        uint32_t audio_us;

        mtp_render_run(&g_ctx, max_blocks);
        wall = mtp_time_us64() - t0;

        audio_us = (uint32_t)(((uint64_t)g_ctx.stats.blocks * block * 1000000ull)
                              / rate);

        /* ---- the counter contract. Same lines as port/host/main.c. ---- */
        printf("\n--- run ---\n");
        printf("engine              %s\n", vt->name);
        printf("output              %u Hz, %u frames/block, ring %u\n",
               rate, block, ring);
        printf("blocks committed    %u  (%u.%03u s of audio)\n",
               g_ctx.stats.blocks, audio_us / 1000000u,
               (audio_us / 1000u) % 1000u);
        printf("wall time           %u.%03u s\n",
               (uint32_t)(wall / 1000000u), (uint32_t)((wall / 1000u) % 1000u));
        if (audio_us)
            printf("real-time factor    %u.%03u\n",
                   (uint32_t)(wall / audio_us),
                   (uint32_t)(((wall % audio_us) * 1000u) / audio_us));
        printf("midi bytes          %u\n", g_ctx.stats.midi_bytes);
        printf("short messages      %u\n", g_ctx.stats.short_msgs);
        printf("sysex messages      %u\n", g_ctx.stats.sysex_msgs);
        printf("parser: short %u sysex %u realtime %u\n",
               g_ctx.parser.stat_short, g_ctx.parser.stat_sysex,
               g_ctx.parser.stat_realtime);
        printf("parser: orphan data %u, sysex truncated %u, sysex aborted %u\n",
               g_ctx.parser.stat_dropped_data,
               g_ctx.parser.stat_sysex_truncated,
               g_ctx.parser.stat_sysex_aborted);
        printf("engine back-pressure %u\n", g_ctx.stats.engine_backpressure);
        printf("underruns           %u\n", g_ctx.stats.underruns);
        printf("worst render        %u us  (block period %u us)\n",
               g_ctx.stats.worst_render_us,
               (uint32_t)(((uint64_t)block * 1000000ull) / rate));
        printf("min ring occupancy  %u of %u\n",
               g_ctx.stats.min_queued == 0xFFFFFFFFu ? 0u : g_ctx.stats.min_queued,
               ring);

        /* ---- and what only the bare-metal run can say ---- */
        printf("stream              %s, %u bytes%s\n", emu_midi_name(),
               emu_midi_length(), realtime ? ", paced at 31250 baud" : "");
        printf("sink                %s\n", emu_audio_sink_name());
        printf("sink blocks played  %u\n", emu_audio_played());
        printf("virtio periods done %u  (max %u completed per irq)\n",
               emu_audio_virtio_completed(), emu_audio_virtio_max_burst());
        printf("uncached used       %u B\n", emu_audio_dma_used());
        printf("sink checksum       0x%08x\n", emu_audio_checksum());
        printf("timer ticks         %u  (worst tick latency %u us)\n",
               emu_tick_count(), emu_tick_late_max_us());
        printf("irqs taken          %u  (spurious %u)\n",
               emu_irq_count(), emu_spurious_count());
        printf("heap after setup    %u B\n", heap_after_open);
        printf("heap high water     %u B of %u B\n",
               emu_heap_high_water(), emu_heap_size());
        printf("heap grown by run   %u B\n",
               emu_heap_high_water() - heap_after_open);
        printf("wav                 %s\n", wav_path ? wav_path : "(none)");
        printf("--- end ---\n");
    }

    mtp_midi_close();
    mtp_audio_close();
    vt->close(inst);
    mtp_storage_unmount();

    emu_exit(g_ctx.stats.underruns ? 1 : 0);
}

/* ------------------------------------------------------------ fatalities */

void emu_fatal_exception(uint32_t which, uint32_t pc)
{
    static const char *const NAME[] = {
        "?", "undefined instruction", "supervisor call", "prefetch abort",
        "data abort", "hyp trap", "fiq"
    };
    uint32_t dfsr = 0, dfar = 0, ifsr = 0, ifar = 0;
    __asm__ volatile("mrc p15, 0, %0, c5, c0, 0" : "=r"(dfsr));
    __asm__ volatile("mrc p15, 0, %0, c6, c0, 0" : "=r"(dfar));
    __asm__ volatile("mrc p15, 0, %0, c5, c0, 1" : "=r"(ifsr));
    __asm__ volatile("mrc p15, 0, %0, c6, c0, 2" : "=r"(ifar));

    printf("\n*** %s at pc 0x%08x\n",
           which < 7u ? NAME[which] : "exception", pc);
    printf("*** dfsr 0x%08x dfar 0x%08x  ifsr 0x%08x ifar 0x%08x\n",
           dfsr, dfar, ifsr, ifar);
    printf("--- end ---\n");
    emu_console_flush();

    /* A supervisor call that lands here means semihosting was not enabled --
     * the exit path would loop for ever, so stop trying. */
    if (which == 2u) { semihost_set_available(0); for (;;) emu_wfi(); }
    emu_exit(128 + (int)which);
}
