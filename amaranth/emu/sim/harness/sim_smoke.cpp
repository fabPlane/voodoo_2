/*
 * sim_smoke.cpp — standalone check of the sim_harness C API.
 *
 * Not part of upstream.  `make -C amaranth/emu/sim smoke` links this against
 * exactly the objects the Glide2x sim backend links against
 * (sim_harness.o + VCoreSim__ALL.a + verilated.o + verilated_threads.o +
 * voodoo_trace_writer.o) and runs it, minus libglide2x, X11 and zlib.
 *
 * Two reasons it exists:
 *
 *  1. It is the "assert the C++ symbol exists" test the harness copy needs.
 *     The memory backdoor reaches into the Verilator model by hierarchical
 *     name (CoreSim__DOT__fbWriteRam__DOT__mem); if an Amaranth rename ever
 *     moves that array, this fails to COMPILE, immediately and locally,
 *     instead of silently producing a black screenshot half an hour into a
 *     trace replay.
 *
 *  2. It runs in seconds and needs no 32-bit X11/zlib, so the harness can be
 *     validated on a machine that cannot build the Glide stack.
 *
 * It includes ONLY sim_harness.h, so it also proves the public API is
 * self-sufficient.  Exit status 0 = pass.
 */

#include "sim_harness.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

static int failures = 0;

static void check(bool ok, const char *what) {
    printf("[sim_smoke] %-34s %s\n", what, ok ? "ok" : "FAIL");
    if (!ok) failures++;
}

static void check_eq(uint32_t got, uint32_t want, const char *what) {
    bool ok = (got == want);
    printf("[sim_smoke] %-34s %s (got 0x%08x, want 0x%08x)\n",
           what, ok ? "ok" : "FAIL", got, want);
    if (!ok) failures++;
}

int main(void) {
    if (sim_init() != 0) {
        fprintf(stderr, "[sim_smoke] sim_init failed\n");
        return 2;
    }

    /* --- bus: a register read must come back, not time out ------------- */
    uint64_t c0 = sim_get_cycle();
    uint32_t status = sim_read(0x000000);
    check(sim_get_cycle() > c0, "bus read advanced the clock");
    check(status != 0xDEAD0000u, "bus read did not time out");
    printf("[sim_smoke] status register = 0x%08x\n", status);

    /* The stub CoreSim answers the status register with "idle"; the real
     * core must too, or sim_idle_wait() never returns. */
    check(sim_idle_wait() != 0, "sim_idle_wait returned");

    sim_write(0x000100, 0x12345678u);  /* must not hang */
    check(true, "bus write completed");

    /* --- framebuffer backdoor ------------------------------------------ */
    const uint32_t kWords = 4096;
    std::vector<uint32_t> src(kWords), dst(kWords, 0xFFFFFFFFu);
    for (uint32_t i = 0; i < kWords; i++) src[i] = 0xF0000000u ^ (i * 2654435761u);

    sim_write_fb_bulk(0, src.data(), kWords);
    sim_read_fb(0, dst.data(), kWords);
    bool fb_ok = true;
    for (uint32_t i = 0; i < kWords; i++) fb_ok &= (dst[i] == src[i]);
    check(fb_ok, "fb bulk write/read round-trip");

    /* Offset access must be word-indexed from a BYTE offset. */
    sim_write_fb_bulk(0x1000, src.data(), 16);
    dst.assign(kWords, 0);
    sim_read_fb(0x1000, dst.data(), 16);
    bool fb_off_ok = true;
    for (uint32_t i = 0; i < 16; i++) fb_off_ok &= (dst[i] == src[i]);
    check(fb_off_ok, "fb backdoor honours byte offset");

    /* The last addressable word: catches an array one size too small. */
    const uint32_t fb_last_byte = 4u * 1024u * 1024u - 4u;
    uint32_t sentinel = 0xA5A5C3C3u, read_back = 0;
    sim_write_fb_bulk(fb_last_byte, &sentinel, 1);
    sim_read_fb(fb_last_byte, &read_back, 1);
    check_eq(read_back, sentinel, "fb backdoor reaches 4 MiB-4");

    /* --- texture backdoor ---------------------------------------------- */
    sim_write_tex_bulk(0, src.data(), kWords);
    dst.assign(kWords, 0);
    sim_read_tex(0, dst.data(), kWords);
    bool tex_ok = true;
    for (uint32_t i = 0; i < kWords; i++) tex_ok &= (dst[i] == src[i]);
    check(tex_ok, "tex bulk write/read round-trip");

    const uint32_t tex_last_byte = 8u * 1024u * 1024u - 4u;
    sentinel = 0x5A5A3C3Cu;
    read_back = 0;
    sim_write_tex_bulk(tex_last_byte, &sentinel, 1);
    sim_read_tex(tex_last_byte, &read_back, 1);
    check_eq(read_back, sentinel, "tex backdoor reaches 8 MiB-4");

    /* --- swapCountReg load path ---------------------------------------- */
    for (uint32_t v = 0; v < 4; v++) {
        sim_set_swap_count(v);
        check_eq(sim_get_swap_count(), v, "swap count round-trip");
    }

    /* --- the counters that are meant to stay stubbed ------------------- */
    check(sim_get_fb_fill_hits() == 0 && sim_get_tex_fill_misses() == 0,
          "stubbed sim_get_* still return 0");
    check(sim_get_total_read_count() > 0, "harness bus stats counted reads");

    sim_tick(100);
    sim_flush_fb_cache();
    check(true, "tick + flush_fb_cache survived");

    sim_shutdown();

    printf("[sim_smoke] %s (%d failure%s)\n",
           failures ? "FAILED" : "PASSED", failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
