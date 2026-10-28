// AE350 hello: boot marker + counter in the mailbox (JTAG diag page 5),
// greeting on UART2 (115200 8N1 from the 50 MHz bus clock).
#include <stdint.h>
#define REG32(a) (*(volatile uint32_t *)(a))
#define MBOX     0xE8000000u
#define MBOX_CNT 0xE8000004u
#define UART2    0xF0300000u
#define U_THR (UART2 + 0x20)
#define U_DLL (UART2 + 0x20)
#define U_DLM (UART2 + 0x24)
#define U_FCR (UART2 + 0x28)
#define U_LCR (UART2 + 0x2C)
#define U_LSR (UART2 + 0x34)
#define BUS_HZ 50000000u

static void putc_(char c) {
    while (!(REG32(U_LSR) & 0x20)) {}
    REG32(U_THR) = (uint8_t)c;
}
static void puts_(const char *s) { while (*s) putc_(*s++); }
static void puthex(uint32_t v) {
    for (int i = 28; i >= 0; i -= 4) putc_("0123456789abcdef"[(v >> i) & 15]);
}

int main(void) {
    REG32(MBOX) = 0xA25B0071u;                 // "booted"
    uint32_t div = (BUS_HZ + 8u * 115200u) / (16u * 115200u);
    REG32(U_LCR) = 0x80;                       // DLAB
    REG32(U_DLL) = div & 0xff;
    REG32(U_DLM) = (div >> 8) & 0xff;
    REG32(U_LCR) = 0x03;                       // 8N1
    REG32(U_FCR) = 0x07;                       // FIFOs on, cleared
    puts_("\r\nVoodoo dock: AE350 A25 up\r\n");
    uint32_t n = 0;
    for (;;) {
        REG32(MBOX) = 0xA2500000u | (n & 0xfffff);
        if ((n & 0xffff) == 0) { puts_("tick "); puthex(n); puts_(" bus "); puthex(REG32(MBOX_CNT)); puts_("\r\n"); }
        n++;
    }
}
