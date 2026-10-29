// MicroBlaze V MCS hello, the Kintex counterpart of the dock's AE350 demo
// (fpga/hw/gowin/ae350/fw): boot marker + counter in the mailbox (GPO1,
// JTAG diag page 5), greeting on the MCS UART (115200 8N1, fixed in the IP).
// GPI1 is a free-running 100 MHz counter (the AE350's MBOX_CNT).
#include <stdint.h>
#define REG32(a) (*(volatile uint32_t *)(a))
#define IO        0x80000000u   // IO module (pg048 / MicroBlaze V MCS)
#define UART_TX   (IO + 0x04)
#define UART_STAT (IO + 0x08)   // bit 3: transmitter busy
#define GPO1      (IO + 0x10)
#define GPI1      (IO + 0x20)

static void putc_(char c) {
    while (REG32(UART_STAT) & 0x08) {}
    REG32(UART_TX) = (uint8_t)c;
}
static void puts_(const char *s) { while (*s) putc_(*s++); }
static void puthex(uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) putc_("0123456789abcdef"[(v >> i) & 15]);
}

int main(void) {
    REG32(GPO1) = 0x4B7B0071u;                 // "booted"
    puts_("\r\nVoodoo Kintex: MicroBlaze V up\r\n");
    uint32_t n = 0;
    for (;;) {
        REG32(GPO1) = 0x4B700000u | (n & 0xfffff);
        if ((n & 0xffff) == 0) { puts_("tick "); puthex(n); puts_(" clk "); puthex(REG32(GPI1)); puts_("\r\n"); }
        n++;
    }
}
