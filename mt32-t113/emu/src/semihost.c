/* semihost.c - ARM semihosting: the host filesystem, and argv.
 *
 * Semihosting is a debugger convention, not a device: the image executes
 * `svc 0x123456` with a call number in r0 and a parameter block in r1, and
 * whatever is attached -- QEMU, OpenOCD over JTAG, a DS-5 probe -- performs the
 * operation on the host's behalf. It costs nothing when it is not enabled,
 * which is why it is safe to leave in an image that will later run on a board.
 *
 * Two things here earn their place:
 *
 *   1. SYS_OPEN/SYS_WRITE/SYS_CLOSE gives the audio sink somewhere to put the
 *      samples. The moment ROM dumps exist, `make run WAV=out.wav` boots the
 *      bare-metal image and leaves a listenable file on the host -- the same
 *      thing port/host writes, produced by the same render loop, on a
 *      Cortex-A7. Note that OpenOCD implements these calls too, so on real
 *      hardware over JTAG this path still works.
 *
 *   2. SYS_GET_CMDLINE gives the image an argv. Without it every scenario
 *      would need its own build; with it, emu/main.c takes the same flags
 *      port/host/main.c takes and `make test` runs the whole conformance
 *      matrix off one image.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>
#include "semihost.h"

#define SYS_OPEN         0x01
#define SYS_CLOSE        0x02
#define SYS_WRITE        0x05
#define SYS_READ         0x06
#define SYS_SEEK         0x0A
#define SYS_FLEN         0x0C
#define SYS_GET_CMDLINE  0x15

static int g_available = 1;   /* cleared if the first call traps */

static inline intptr_t sh_call(uint32_t op, const void *block)
{
    register uint32_t r0 __asm__("r0") = op;
    register const void *r1 __asm__("r1") = block;
    __asm__ volatile("svc 0x123456" : "+r"(r0) : "r"(r1) : "memory");
    return (intptr_t)r0;
}

void semihost_set_available(int on) { g_available = on; }
int  semihost_available(void)       { return g_available; }

/* mode: SEMIHOST_W ("wb") or SEMIHOST_R ("rb"). */
int semihost_open(const char *path, int mode)
{
    uint32_t block[3];
    size_t n = 0;
    if (!g_available) return -1;
    while (path[n]) n++;
    block[0] = (uint32_t)(uintptr_t)path;
    block[1] = (uint32_t)mode;
    block[2] = (uint32_t)n;
    return (int)sh_call(SYS_OPEN, block);
}

/* Returns bytes written, or -1. Semihosting SYS_WRITE returns the number NOT
 * written, which is the classic way to misread this interface. */
int semihost_write(int handle, const void *buf, unsigned len)
{
    uint32_t block[3];
    intptr_t left;
    if (!g_available || handle < 0) return -1;
    block[0] = (uint32_t)handle;
    block[1] = (uint32_t)(uintptr_t)buf;
    block[2] = len;
    left = sh_call(SYS_WRITE, block);
    if (left < 0) return -1;
    return (int)(len - (unsigned)left);
}

/* Returns bytes read, or -1. Like SYS_WRITE, SYS_READ returns the number NOT
 * read, and a full-length return means "nothing was read". */
int semihost_read(int handle, void *buf, unsigned len)
{
    uint32_t block[3];
    intptr_t left;
    if (!g_available || handle < 0) return -1;
    block[0] = (uint32_t)handle;
    block[1] = (uint32_t)(uintptr_t)buf;
    block[2] = len;
    left = sh_call(SYS_READ, block);
    if (left < 0 || (unsigned)left > len) return -1;
    return (int)(len - (unsigned)left);
}

long semihost_flen(int handle)
{
    uint32_t block[1];
    if (!g_available || handle < 0) return -1;
    block[0] = (uint32_t)handle;
    return (long)sh_call(SYS_FLEN, block);
}

int semihost_seek(int handle, long pos)
{
    uint32_t block[2];
    if (!g_available || handle < 0) return -1;
    block[0] = (uint32_t)handle;
    block[1] = (uint32_t)pos;
    return (int)sh_call(SYS_SEEK, block);
}

int semihost_close(int handle)
{
    uint32_t block[1];
    if (!g_available || handle < 0) return -1;
    block[0] = (uint32_t)handle;
    return (int)sh_call(SYS_CLOSE, block);
}

/* Fills buf with the host command line, NUL terminated. Returns its length,
 * or -1 if the host declined (no semihosting, or no arguments configured).
 * QEMU supplies this from `-semihosting-config arg=...`. */
int semihost_cmdline(char *buf, unsigned cap)
{
    uint32_t block[2];
    if (!g_available) return -1;
    buf[0] = '\0';
    block[0] = (uint32_t)(uintptr_t)buf;
    block[1] = cap - 1u;
    if (sh_call(SYS_GET_CMDLINE, block) != 0) return -1;
    if (block[1] >= cap) block[1] = cap - 1u;
    buf[block[1]] = '\0';
    return (int)block[1];
}
