/* console.c - PL011 UART console for QEMU `-M virt`.
 *
 * THIS DRIVER IS THE ONE PART OF emu/ THAT DOES NOT TRANSFER TO THE T113.
 * `-M virt` gives you an ARM PrimeCell PL011 at 0x09000000; the T113's UARTs
 * are 16550-compatible at 0x02500000 + n*0x400 (boot/BRINGUP.md section 6.2,
 * and note that every T113 board puts the console on a different one). The
 * seam is three functions -- init, putc, poll -- so the board port is a new
 * file of about forty lines and nothing above it changes.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include "emu.h"

#define PL011_BASE  0x09000000u

#define UARTDR      0x000
#define UARTFR      0x018
#define UARTIBRD    0x024
#define UARTFBRD    0x028
#define UARTLCR_H   0x02C
#define UARTCR      0x030
#define UARTIMSC    0x038
#define UARTICR     0x044

#define FR_RXFE     (1u << 4)
#define FR_TXFF     (1u << 5)
#define FR_BUSY     (1u << 3)

static volatile uint32_t *reg(uint32_t off)
{
    return (volatile uint32_t *)(uintptr_t)(PL011_BASE + off);
}

void emu_console_init(void)
{
    /* QEMU's PL011 transmits regardless, but a real one does not, and this
     * image is meant to be the same code on both. 115200 8N1 from a nominal
     * 24 MHz UARTCLK: divisor 13.0208 -> IBRD 13, FBRD 1. QEMU ignores the
     * divisors entirely. */
    *reg(UARTCR)    = 0u;                    /* disable while reconfiguring */
    *reg(UARTICR)   = 0x7FFu;                /* clear all interrupts        */
    *reg(UARTIBRD)  = 13u;
    *reg(UARTFBRD)  = 1u;
    *reg(UARTLCR_H) = (3u << 5) | (1u << 4); /* 8 bits, FIFOs on            */
    *reg(UARTIMSC)  = 0u;                    /* polled: no interrupts       */
    *reg(UARTCR)    = (1u << 0) | (1u << 8) | (1u << 9);  /* UARTEN TXE RXE */
}

void emu_console_putc(char c)
{
    if (c == '\n') emu_console_putc('\r');
    while (*reg(UARTFR) & FR_TXFF) { }
    *reg(UARTDR) = (uint32_t)(unsigned char)c;
}

void emu_console_write(const char *s, unsigned len)
{
    unsigned i;
    for (i = 0; i < len; i++) emu_console_putc(s[i]);
}

/* -1 if nothing is waiting. Not used by the render loop; it exists so a board
 * bring-up session has a way in. */
int emu_console_getc(void)
{
    if (*reg(UARTFR) & FR_RXFE) return -1;
    return (int)(*reg(UARTDR) & 0xFFu);
}

void emu_console_flush(void)
{
    while (*reg(UARTFR) & FR_BUSY) { }
}
