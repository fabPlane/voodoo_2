/* printf.c - the smallest printf that satisfies this image and mt32emu.
 *
 * We are linking -nostdlib against an arm-linux-gnueabihf toolchain, so there
 * is no newlib here to retarget: glibc's stdio wants a full Linux process
 * (TLS, locales, file locking, __libc_start_main) and will not come up on bare
 * metal. See emu/FINDINGS.md for why that toolchain was used anyway.
 *
 * So this file *is* the retarget. Everything funnels into _write(1, ...), so
 * the contract port/PORTING.md section 4.1 describes -- "_write: route to the
 * debug UART" -- is exactly what happens, and a newlib build would drop in
 * with this file deleted and nothing else changed.
 *
 * Supported: %c %s %d %i %u %x %X %p %f %% with 0/-/+/space flags, field
 * width, precision, and the l/ll/z length modifiers. Not supported: %e %g,
 * positional arguments, wide characters. mt32emu's printDebug call sites use
 * %s, %d, %u, %x and %02x; ours are overridden by a ReportHandler anyway.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include "emu.h"

int _write(int fd, const char *buf, int len);

typedef struct {
    char    *dst;       /* NULL => straight to the console */
    size_t   cap;
    size_t   len;       /* characters that would have been written */
} sink;

static void emit(sink *s, char c)
{
    if (s->dst) {
        if (s->len + 1u < s->cap) s->dst[s->len] = c;
    } else {
        emu_console_putc(c);
    }
    s->len++;
}

static void emit_pad(sink *s, char c, int n)
{
    while (n-- > 0) emit(s, c);
}

static int u64_to_str(char *buf, uint64_t v, unsigned base, int upper)
{
    static const char lo[] = "0123456789abcdef";
    static const char up[] = "0123456789ABCDEF";
    const char *digits = upper ? up : lo;
    char tmp[24];
    int n = 0, i;
    if (v == 0u) tmp[n++] = '0';
    while (v) { tmp[n++] = digits[v % base]; v /= base; }
    for (i = 0; i < n; i++) buf[i] = tmp[n - 1 - i];
    return n;
}

/* %f only. Six digits by default, like C. Values beyond 2^63 print as "inf"
 * because nothing in this image produces one and guessing would be worse. */
static int f64_to_str(char *buf, double v, int prec)
{
    int n = 0, i;
    uint64_t ip;
    double frac;
    if (v != v) { buf[0]='n'; buf[1]='a'; buf[2]='n'; return 3; }
    if (v < 0.0) { buf[n++] = '-'; v = -v; }
    if (v >= 9.2e18) { buf[n++]='i'; buf[n++]='n'; buf[n++]='f'; return n; }
    /* round at the requested precision so 0.9999995 prints as 1.000000 */
    {
        double r = 0.5;
        for (i = 0; i < prec; i++) r /= 10.0;
        v += r;
    }
    ip = (uint64_t)v;
    frac = v - (double)ip;
    n += u64_to_str(buf + n, ip, 10u, 0);
    if (prec > 0) {
        buf[n++] = '.';
        for (i = 0; i < prec; i++) {
            int d;
            frac *= 10.0;
            d = (int)frac;
            if (d < 0) d = 0;
            if (d > 9) d = 9;
            buf[n++] = (char)('0' + d);
            frac -= (double)d;
        }
    }
    return n;
}

static int core(sink *s, const char *fmt, va_list ap)
{
    char num[64];

    while (*fmt) {
        int left = 0, zero = 0, plus = 0, space = 0, alt = 0;
        int width = 0, prec = -1, lng = 0, nlen = 0, neg = 0;
        const char *str = NULL;

        if (*fmt != '%') { emit(s, *fmt++); continue; }
        fmt++;
        if (*fmt == '%') { emit(s, '%'); fmt++; continue; }

        for (;;) {
            if      (*fmt == '-') { left = 1;  fmt++; }
            else if (*fmt == '0') { zero = 1;  fmt++; }
            else if (*fmt == '+') { plus = 1;  fmt++; }
            else if (*fmt == ' ') { space = 1; fmt++; }
            else if (*fmt == '#') { alt = 1;   fmt++; }
            else break;
        }
        if (*fmt == '*') { width = va_arg(ap, int); fmt++;
                           if (width < 0) { left = 1; width = -width; } }
        else while (*fmt >= '0' && *fmt <= '9') width = width * 10 + (*fmt++ - '0');
        if (*fmt == '.') {
            fmt++; prec = 0;
            if (*fmt == '*') { prec = va_arg(ap, int); fmt++; }
            else while (*fmt >= '0' && *fmt <= '9') prec = prec * 10 + (*fmt++ - '0');
        }
        while (*fmt == 'l') { lng++; fmt++; }
        if (*fmt == 'z' || *fmt == 'h' || *fmt == 'j' || *fmt == 't') fmt++;

        switch (*fmt++) {
        case 'c':
            num[0] = (char)va_arg(ap, int); nlen = 1; str = num; prec = -1;
            break;
        case 's': {
            const char *p = va_arg(ap, const char *);
            int n = 0;
            if (!p) p = "(null)";
            while (p[n] && (prec < 0 || n < prec)) n++;
            str = p; nlen = n; prec = -1;
            break;
        }
        case 'd': case 'i': {
            int64_t v = lng >= 2 ? va_arg(ap, long long)
                      : lng == 1 ? (int64_t)va_arg(ap, long)
                                 : (int64_t)va_arg(ap, int);
            uint64_t m;
            if (v < 0) { neg = 1; m = (uint64_t)(-(v + 1)) + 1u; }
            else m = (uint64_t)v;
            nlen = u64_to_str(num, m, 10u, 0);
            str = num;
            break;
        }
        case 'u': {
            uint64_t v = lng >= 2 ? va_arg(ap, unsigned long long)
                       : lng == 1 ? (uint64_t)va_arg(ap, unsigned long)
                                  : (uint64_t)va_arg(ap, unsigned int);
            nlen = u64_to_str(num, v, 10u, 0); str = num;
            break;
        }
        case 'x': case 'X': {
            int upper = fmt[-1] == 'X';
            uint64_t v = lng >= 2 ? va_arg(ap, unsigned long long)
                       : lng == 1 ? (uint64_t)va_arg(ap, unsigned long)
                                  : (uint64_t)va_arg(ap, unsigned int);
            nlen = u64_to_str(num, v, 16u, upper); str = num;
            if (alt && v) { emit(s, '0'); emit(s, upper ? 'X' : 'x'); }
            break;
        }
        case 'p': {
            uint64_t v = (uint64_t)(uintptr_t)va_arg(ap, void *);
            emit(s, '0'); emit(s, 'x');
            nlen = u64_to_str(num, v, 16u, 0); str = num;
            break;
        }
        case 'f': case 'F': {
            double v = va_arg(ap, double);
            if (v < 0.0) neg = 1;
            nlen = f64_to_str(num, v, prec < 0 ? 6 : prec);
            if (neg) { str = num + 1; nlen--; } else str = num;
            prec = -1;
            break;
        }
        default:
            emit(s, '%'); emit(s, fmt[-1]);
            continue;
        }

        {
            int sign = neg || plus || space;
            int pad = width - nlen - (sign ? 1 : 0);
            if (!left && !zero) emit_pad(s, ' ', pad);
            if (neg) emit(s, '-');
            else if (plus) emit(s, '+');
            else if (space) emit(s, ' ');
            if (!left && zero) emit_pad(s, '0', pad);
            { int i; for (i = 0; i < nlen; i++) emit(s, str[i]); }
            if (left) emit_pad(s, ' ', pad);
        }
    }
    if (s->dst && s->cap) s->dst[s->len < s->cap ? s->len : s->cap - 1u] = '\0';
    return (int)s->len;
}

int vsnprintf(char *dst, size_t cap, const char *fmt, va_list ap)
{
    sink s; s.dst = dst; s.cap = cap; s.len = 0;
    return core(&s, fmt, ap);
}

int snprintf(char *dst, size_t cap, const char *fmt, ...)
{
    va_list ap; int n;
    va_start(ap, fmt); n = vsnprintf(dst, cap, fmt, ap); va_end(ap);
    return n;
}

int vsprintf(char *dst, const char *fmt, va_list ap)
{
    return vsnprintf(dst, (size_t)-1, fmt, ap);
}

int sprintf(char *dst, const char *fmt, ...)
{
    va_list ap; int n;
    va_start(ap, fmt); n = vsnprintf(dst, (size_t)-1, fmt, ap); va_end(ap);
    return n;
}

int vprintf(const char *fmt, va_list ap)
{
    sink s; s.dst = NULL; s.cap = 0; s.len = 0;
    return core(&s, fmt, ap);
}

int printf(const char *fmt, ...)
{
    va_list ap; int n;
    va_start(ap, fmt); n = vprintf(fmt, ap); va_end(ap);
    return n;
}

int puts(const char *s)
{
    while (*s) emu_console_putc(*s++);
    emu_console_putc('\n');
    return 0;
}

int putchar(int c) { emu_console_putc((char)c); return c; }
int fputc(int c, void *stream) { (void)stream; emu_console_putc((char)c); return c; }
int fputs(const char *s, void *stream) { (void)stream; while (*s) emu_console_putc(*s++); return 0; }
int fflush(void *stream) { (void)stream; emu_console_flush(); return 0; }

int vfprintf(void *stream, const char *fmt, va_list ap)
{
    (void)stream;
    return vprintf(fmt, ap);
}

int fwrite_stub(const void *p, size_t sz, size_t n, void *stream)
{
    (void)stream;
    emu_console_write((const char *)p, (unsigned)(sz * n));
    return (int)n;
}

int fprintf(void *stream, const char *fmt, ...)
{
    va_list ap; int n;
    (void)stream;
    va_start(ap, fmt); n = vprintf(fmt, ap); va_end(ap);
    return n;
}
