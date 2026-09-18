/* timer.c - the ARM architected generic timer: mtp_time, and the block tick.
 *
 * This is the one driver in emu/ that *does* transfer to the T113 essentially
 * unchanged, and that is not an accident -- port/include/mtp_time.h chose the
 * generic timer over a SoC peripheral for exactly this reason: it is an
 * architectural register block, it is always running, and it does not stop
 * when a core WFIs. The T113's runs at 24 MHz (boot/BRINGUP.md section 4.3);
 * QEMU's `-M virt` runs at 62.5 MHz. Neither number is compiled in: CNTFRQ is
 * read at init and everything below is derived from it.
 *
 * Two jobs:
 *
 *   mtp_time_us/us64/delay_us   the port's timebase, from CNTPCT
 *   emu_tick_start              a periodic PL1 physical-timer interrupt, which
 *                               is this image's stand-in for the I2S DMA
 *                               block-completion interrupt
 *
 * The tick rearms from an absolute compare value (CNTP_CVAL), not from a
 * countdown reloaded in the handler (CNTP_TVAL). That matters: with TVAL every
 * interrupt latency adds permanently to the period and the audio clock walks
 * away from real time, which would quietly turn a late render into a slow
 * clock instead of into the underrun it really is. With CVAL the deadline
 * sequence is fixed at start and lateness is visible -- emu_tick_late_max_us()
 * reports the worst one seen.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include "emu.h"
#include "mtp_time.h"

static uint64_t g_freq;
static uint64_t g_base;
static void   (*g_tick_fn)(void);
static uint64_t g_period;       /* in timer ticks */
static uint64_t g_next;         /* absolute CVAL of the next deadline */
static uint32_t g_ticks;
static uint32_t g_late_max_us;
static int      g_running;

static inline uint64_t cntpct(void)
{
    uint32_t lo, hi;
    __asm__ volatile("isb\n\tmrrc p15, 0, %0, %1, c14" : "=r"(lo), "=r"(hi));
    return ((uint64_t)hi << 32) | lo;
}

static inline uint32_t cntfrq(void)
{
    uint32_t f;
    __asm__ volatile("mrc p15, 0, %0, c14, c0, 0" : "=r"(f));
    return f;
}

static inline void cntp_cval(uint64_t v)
{
    uint32_t lo = (uint32_t)v, hi = (uint32_t)(v >> 32);
    __asm__ volatile("mcrr p15, 2, %0, %1, c14" :: "r"(lo), "r"(hi));
}

static inline void cntp_ctl(uint32_t v)
{
    __asm__ volatile("mcr p15, 0, %0, c14, c2, 1" :: "r"(v));
    __asm__ volatile("isb");
}

uint32_t emu_timer_frequency(void) { return (uint32_t)g_freq; }
uint64_t emu_timer_ticks(void)     { return cntpct(); }

static uint64_t ticks_to_us(uint64_t t)
{
    /* Split so that the multiply cannot overflow at 62.5 MHz over a long run:
     * t * 1000000 alone wraps after about five minutes. */
    return (t / g_freq) * 1000000ull + ((t % g_freq) * 1000000ull) / g_freq;
}

mtp_status mtp_time_init(void)
{
    g_freq = cntfrq();
    if (g_freq == 0u) {
        /* A loader that never programmed CNTFRQ. On the T113 the right answer
         * is 24 MHz; say so loudly rather than dividing by zero. */
        g_freq = 24000000ull;
    }
    g_base = cntpct();
    return MTP_OK;
}

uint64_t mtp_time_us64(void) { return ticks_to_us(cntpct() - g_base); }
uint32_t mtp_time_us(void)   { return (uint32_t)mtp_time_us64(); }

void mtp_time_delay_us(uint32_t us)
{
    uint64_t target = cntpct() + (g_freq * (uint64_t)us) / 1000000ull;
    while (cntpct() < target) { }
}

/* ---------------------------------------------------------- the tick ---- */

static void timer_isr(void)
{
    uint64_t now = cntpct();
    uint64_t late;

    /* Catch up rather than drift: if we were so late that more than one
     * deadline has passed, advance past all of them. Each skipped deadline is
     * a block the "DMA" played, and the sink counts it as an underrun -- which
     * is the honest answer, not a silent slowdown. */
    late = now > g_next ? now - g_next : 0u;
    if (late) {
        uint32_t late_us = (uint32_t)ticks_to_us(late);
        if (late_us > g_late_max_us) g_late_max_us = late_us;
    }

    do {
        g_next += g_period;
        g_ticks++;
        if (g_tick_fn) g_tick_fn();
    } while (g_next <= now);

    cntp_cval(g_next);
    cntp_ctl(1u);               /* ENABLE, unmasked */
}

void emu_tick_start(uint32_t period_us, void (*tick)(void))
{
    g_tick_fn = tick;
    g_period  = (g_freq * (uint64_t)period_us) / 1000000ull;
    if (g_period == 0u) g_period = 1u;
    g_ticks = 0u;
    g_late_max_us = 0u;

    emu_gic_set_handler(EMU_IRQ_PTIMER, timer_isr);
    emu_gic_enable(EMU_IRQ_PTIMER, 0x80u);

    g_next = cntpct() + g_period;
    cntp_cval(g_next);
    cntp_ctl(1u);
    g_running = 1;
    emu_irq_enable();
}

void emu_tick_stop(void)
{
    cntp_ctl(0u);
    g_running = 0;
    g_tick_fn = 0;
}

uint32_t emu_tick_count(void)        { return g_ticks; }
uint32_t emu_tick_late_max_us(void)  { return g_late_max_us; }
