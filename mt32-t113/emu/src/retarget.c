/* retarget.c - the libc surface, bare metal.
 *
 * port/PORTING.md section 4.1 lists what mt32emu needs retargeted. This file
 * is that list, implemented, and it is deliberately short:
 *
 *   _sbrk                     the heap the linker script reserved
 *   _write                    -> the PL011 console
 *   _read _close _lseek _fstat _isatty      -ENOSYS stubs
 *   malloc/calloc/realloc/free                a bump allocator, see below
 *   memcpy memset memmove memcmp strlen ...   GCC emits calls to these
 *   rand                      section 4.9: our own, not glibc's reentrant one
 *   abort __assert_fail __errno_location __stack_chk_*
 *   __cxa_atexit __dso_handle __cxa_pure_virtual   (C++ runtime, section 4.1)
 *
 * WHY A BUMP ALLOCATOR. port/PORTING.md section 4.3 argues for one directly:
 * all 285 of mt32emu's allocations happen inside Synth::open(), none during
 * rendering (measured, section 3), and the only thing that frees them is
 * Synth::close(), which on this device means "reboot". free() is therefore a
 * no-op except for the one case that is free and useful -- freeing the most
 * recent allocation, which makes the ROM staging buffers in
 * host/engine_mt32emu.cpp cheap to release.
 *
 * The consequence is stated rather than hidden: an open/close/open cycle leaks
 * the whole arena. This image runs three scenarios back to back, so it does
 * exactly that, which is why it prints the heap high-water mark at the end --
 * that number is the one port/PORTING.md section 7 asks to be logged.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stddef.h>
#include <stdint.h>
#include "emu.h"

extern char __heap_start[], __heap_end[];

static char    *g_brk;
static uint32_t g_high_water;
static void    *g_last_block;
static size_t   g_last_size;

/* ------------------------------------------------------------ _sbrk ----- */
void *_sbrk(intptr_t incr)
{
    char *prev;
    if (g_brk == NULL) g_brk = __heap_start;
    prev = g_brk;
    if (incr > 0 && (size_t)(__heap_end - g_brk) < (size_t)incr)
        return (void *)-1;              /* caller turns this into MTP_ERR_NOMEM */
    g_brk += incr;
    if ((uint32_t)(g_brk - __heap_start) > g_high_water)
        g_high_water = (uint32_t)(g_brk - __heap_start);
    return prev;
}

uint32_t emu_heap_used(void)       { return (uint32_t)((g_brk ? g_brk : __heap_start) - __heap_start); }
uint32_t emu_heap_size(void)       { return (uint32_t)(__heap_end - __heap_start); }
uint32_t emu_heap_high_water(void) { return g_high_water; }

/* ------------------------------------------------------- the allocator -- */
void *malloc(size_t n)
{
    void *p;
    if (n == 0u) n = 1u;
    n = (n + 7u) & ~(size_t)7u;         /* 8-byte alignment: doubles, NEON  */
    p = _sbrk((intptr_t)n);
    if (p == (void *)-1) return NULL;
    g_last_block = p;
    g_last_size  = n;
    return p;
}

void free(void *p)
{
    /* The one case worth handling: the most recent block. It makes the 1 MB
     * PCM ROM staging buffer in the mt32emu binding actually cost nothing if
     * the caller frees it straight back. */
    if (p != NULL && p == g_last_block) {
        (void)_sbrk(-(intptr_t)g_last_size);
        g_last_block = NULL;
        g_last_size  = 0u;
    }
}

void *calloc(size_t n, size_t sz)
{
    size_t total = n * sz;
    void *p;
    if (n != 0u && total / n != sz) return NULL;   /* overflow */
    p = malloc(total);
    if (p) {
        char *d = (char *)p;
        while (total--) *d++ = 0;
    }
    return p;
}

void *realloc(void *old, size_t n)
{
    void *p = malloc(n);
    if (p && old) {
        /* We do not know the old size; copying n bytes is safe only because
         * the heap is one contiguous arena and old+n stays inside it. */
        const char *s = (const char *)old;
        char *d = (char *)p;
        size_t i, avail = (size_t)(__heap_end - (char *)old);
        size_t k = n < avail ? n : avail;
        for (i = 0; i < k; i++) d[i] = s[i];
    }
    return p;
}

/* ------------------------------------------------- string.h essentials -- */
void *memcpy(void *d, const void *s, size_t n)
{
    unsigned char *dd = (unsigned char *)d;
    const unsigned char *ss = (const unsigned char *)s;
    /* word copy when both ends line up; this is the hot one, mt32emu copies
     * the expanded PCM ROM and every sysex through it */
    if (((uintptr_t)dd | (uintptr_t)ss) % 4u == 0u) {
        uint32_t *dw = (uint32_t *)dd;
        const uint32_t *sw = (const uint32_t *)ss;
        while (n >= 4u) { *dw++ = *sw++; n -= 4u; }
        dd = (unsigned char *)dw; ss = (const unsigned char *)sw;
    }
    while (n--) *dd++ = *ss++;
    return d;
}

void *memmove(void *d, const void *s, size_t n)
{
    unsigned char *dd = (unsigned char *)d;
    const unsigned char *ss = (const unsigned char *)s;
    if (dd == ss || n == 0u) return d;
    if (dd < ss) return memcpy(d, s, n);
    dd += n; ss += n;
    while (n--) *--dd = *--ss;
    return d;
}

void *memset(void *d, int c, size_t n)
{
    unsigned char *dd = (unsigned char *)d;
    unsigned char v = (unsigned char)c;
    if ((uintptr_t)dd % 4u == 0u) {
        uint32_t w = (uint32_t)v * 0x01010101u;
        uint32_t *dw = (uint32_t *)dd;
        while (n >= 4u) { *dw++ = w; n -= 4u; }
        dd = (unsigned char *)dw;
    }
    while (n--) *dd++ = v;
    return d;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = (const unsigned char *)a, *y = (const unsigned char *)b;
    while (n--) { if (*x != *y) return (int)*x - (int)*y; x++; y++; }
    return 0;
}

void *memchr(const void *s, int c, size_t n)
{
    const unsigned char *p = (const unsigned char *)s;
    while (n--) { if (*p == (unsigned char)c) return (void *)p; p++; }
    return NULL;
}

size_t strlen(const char *s) { const char *p = s; while (*p) p++; return (size_t)(p - s); }
size_t strnlen(const char *s, size_t m) { size_t n = 0; while (n < m && s[n]) n++; return n; }
int strcmp(const char *a, const char *b)
{ while (*a && *a == *b) { a++; b++; } return (int)(unsigned char)*a - (int)(unsigned char)*b; }
int strncmp(const char *a, const char *b, size_t n)
{ while (n && *a && *a == *b) { a++; b++; n--; } return n ? (int)(unsigned char)*a - (int)(unsigned char)*b : 0; }
char *strcpy(char *d, const char *s) { char *r = d; while ((*d++ = *s++) != '\0') {} return r; }
char *strncpy(char *d, const char *s, size_t n)
{ char *r = d; while (n && (*d = *s)) { d++; s++; n--; } while (n--) *d++ = '\0'; return r; }
char *strchr(const char *s, int c)
{ for (;; s++) { if (*s == (char)c) return (char *)s; if (!*s) return NULL; } }
char *strrchr(const char *s, int c)
{ const char *last = NULL; for (;; s++) { if (*s == (char)c) last = s; if (!*s) break; } return (char *)last; }
char *strcat(char *d, const char *s) { char *r = d; while (*d) d++; while ((*d++ = *s++) != '\0') {} return r; }
char *strstr(const char *h, const char *n)
{
    size_t ln = strlen(n);
    if (!ln) return (char *)h;
    for (; *h; h++) if (!strncmp(h, n, ln)) return (char *)h;
    return NULL;
}

/* ------------------------------------------------------- rand (4.9) ----- */
/* port/PORTING.md section 4.9: TVP.cpp uses rand() on the render path and only
 * needs two low bits that vary. glibc's goes through _REENT and takes a lock;
 * this is a 3-line xorshift and the linker prefers it. */
static uint32_t g_rand_state = 0x1234567u;
int rand(void)
{
    uint32_t x = g_rand_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    g_rand_state = x;
    return (int)(x & 0x7FFFFFFFu);
}
void srand(unsigned s) { g_rand_state = s ? (uint32_t)s : 1u; }

/* ---------------------------------------------- errno and friends ------- */
static int g_errno;
int *__errno_location(void) { return &g_errno; }
int  __errno;

/* glibc's libm.a is built with the stack protector on some configurations. */
uintptr_t __stack_chk_guard = 0xDEADBEEFu;
void __stack_chk_fail(void)
{
    printf("\n*** stack smashing detected ***\n");
    emu_exit(70);
}

/* --------------------------------------------------- exit / semihosting - */
/* ARM semihosting. SYS_EXIT_EXTENDED (0x20) takes {reason, exit_code}, which
 * is the only way to get a non-zero exit status out of a 32-bit ARM guest --
 * plain SYS_EXIT (0x18) on AArch32 carries the reason only, so a failing test
 * would look like a pass. That distinction is the whole reason `make test`
 * can assert on anything. */
static void semihost_exit(int code)
{
    volatile uint32_t block[2];
    block[0] = 0x20026u;                /* ADP_Stopped_ApplicationExit      */
    block[1] = (uint32_t)code;
    __asm__ volatile(
        "mov r0, #0x20\n"
        "mov r1, %0\n"
        "svc 0x123456\n"
        :: "r" (block) : "r0", "r1", "memory");
    /* If semihosting is not enabled the SVC traps to our own vector, which
     * prints and spins. Nothing below should ever run. */
}

void emu_exit(int code)
{
    emu_console_flush();
    semihost_exit(code);
    for (;;) emu_wfi();
}

void emu_exit_ok(void) { emu_exit(0); }

void abort(void)
{
    printf("\n*** abort() ***\n");
    emu_exit(134);
}

void exit(int code) { emu_exit(code); }
void _exit(int code) { emu_exit(code); }

void __assert_fail(const char *expr, const char *file, unsigned line,
                   const char *fn)
{
    printf("\n*** assert: %s at %s:%u (%s)\n", expr, file, line,
           fn ? fn : "?");
    emu_exit(134);
}

/* ------------------------------------------------- file descriptor stubs  */
int _write(int fd, const char *buf, int len)
{
    (void)fd;
    emu_console_write(buf, (unsigned)len);
    return len;
}
int write(int fd, const void *buf, size_t n) { return _write(fd, (const char *)buf, (int)n); }
int _read(int fd, char *buf, int len)   { (void)fd; (void)buf; (void)len; return -1; }
int _close(int fd)                      { (void)fd; return -1; }
long _lseek(int fd, long off, int w)    { (void)fd; (void)off; (void)w; return -1; }
int _fstat(int fd, void *st)            { (void)fd; (void)st; return -1; }
int _isatty(int fd)                     { (void)fd; return 1; }
int _getpid(void)                       { return 1; }
int _kill(int pid, int sig)             { (void)pid; (void)sig; return -1; }

/* ---------------------------------------------------- C++ runtime ------- */
/* port/PORTING.md section 4.1: one-line stubs, because the device never exits.
 * -fno-threadsafe-statics removes __cxa_guard_*, so they are absent here on
 * purpose: if a link ever asks for them, the library was built without that
 * flag and the build is wrong. */
void *__dso_handle = (void *)&__dso_handle;
int __cxa_atexit(void (*f)(void *), void *a, void *d) { (void)f; (void)a; (void)d; return 0; }
void __cxa_finalize(void *d) { (void)d; }
void __aeabi_atexit(void *obj, void (*f)(void *), void *d) { (void)obj; (void)f; (void)d; }
void __cxa_pure_virtual(void)
{
    printf("\n*** pure virtual call ***\n");
    emu_exit(134);
}
