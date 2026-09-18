/* emu.h - internal interfaces of the bare-metal image.
 *
 * Everything the *port* needs is in port/include; this header is only for the
 * pieces below it that the port never sees: the console, the GIC, the
 * periodic tick, and the exit path.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef EMU_H
#define EMU_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- console (src/console.c) ------------------------------------------ */
void emu_console_init(void);
void emu_console_putc(char c);
void emu_console_write(const char *s, unsigned len);
int  emu_console_getc(void);
void emu_console_flush(void);

/* ---- CPU (src/start.S, src/mmu.c) ------------------------------------- */
void emu_wfi(void);
void emu_irq_enable(void);
void emu_irq_disable(void);
void emu_dcache_clean_range(const void *addr, uint32_t len);

/* Captured by start.S before anything is reconfigured. Layout matches the
 * stores in start.S; see emu_print_entry_state(). */
extern uint32_t emu_entry_state[12];
void emu_print_entry_state(void);

/* ---- GIC (src/gic.c) --------------------------------------------------- */
#define EMU_IRQ_PTIMER  30u     /* PPI 30: non-secure PL1 physical timer    */

typedef void (*emu_irq_handler)(void);

void emu_gic_init(void);
void emu_gic_set_handler(unsigned intid, emu_irq_handler h);
void emu_gic_enable(unsigned intid, unsigned priority);
void emu_irq_dispatch(void);            /* called from the IRQ vector       */
uint32_t emu_irq_count(void);
uint32_t emu_spurious_count(void);

/* ---- generic timer (src/timer.c) --------------------------------------- */
/* mtp_time_* is the port-facing half and lives in the same file. */
uint32_t emu_timer_frequency(void);
uint64_t emu_timer_ticks(void);
/* Starts the PL1 physical timer firing every period_us, calling `tick`.
 * This is the image's stand-in for the I2S DMA block-completion interrupt. */
void emu_tick_start(uint32_t period_us, void (*tick)(void));
void emu_tick_stop(void);
uint32_t emu_tick_count(void);
uint32_t emu_tick_late_max_us(void);    /* worst tick-to-service latency    */

/* ---- exit (src/retarget.c) --------------------------------------------- */
void emu_exit(int code) __attribute__((noreturn));
void emu_exit_ok(void)  __attribute__((noreturn));
void emu_fatal_exception(uint32_t which, uint32_t pc) __attribute__((noreturn));

/* ---- heap accounting (src/retarget.c) ---------------------------------- */
uint32_t emu_heap_used(void);
uint32_t emu_heap_size(void);
uint32_t emu_heap_high_water(void);

#ifdef __cplusplus
}
#endif

#endif /* EMU_H */
