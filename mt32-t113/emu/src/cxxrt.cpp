/* cxxrt.cpp - the C++ runtime, such as it is.
 *
 * port/PORTING.md section 4.3 gives this file almost verbatim, and the
 * justification with it: all 285 of mt32emu's allocations happen inside
 * Synth::open(), none during rendering (measured, section 3), and the only
 * thing that frees them is Synth::close(), which on this device means reboot.
 * So operator new goes to the arena and operator delete is very nearly a
 * no-op -- retarget.c's free() does reclaim the most recent block, which is
 * what makes the 1 MB PCM ROM staging buffer cheap to release after open().
 *
 * With -fno-exceptions these must not throw, so a failed allocation is fatal
 * and says so rather than returning NULL into code that will not check it.
 *
 * -fno-threadsafe-statics means __cxa_guard_acquire/release are not emitted,
 * and they are deliberately absent from this file: if a link ever asks for
 * them, the library was built without that flag and the build is wrong.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stddef.h>

extern "C" {
    void *malloc(size_t n);
    void  free(void *p);
    int   printf(const char *fmt, ...);
    void  emu_exit(int code);
    unsigned emu_heap_used(void);
    unsigned emu_heap_size(void);
}

static void out_of_memory(size_t n)
{
    printf("\n*** operator new(%u) failed: arena is %u of %u bytes used\n",
           (unsigned)n, emu_heap_used(), emu_heap_size());
    printf("*** raise HEAP_SIZE in link.ld (port/PORTING.md section 7 sizes it)\n");
    emu_exit(66);
}

void *operator new(size_t n)
{
    void *p = malloc(n ? n : 1u);
    if (!p) out_of_memory(n);
    return p;
}

void *operator new[](size_t n)
{
    void *p = malloc(n ? n : 1u);
    if (!p) out_of_memory(n);
    return p;
}

void operator delete(void *p)            { free(p); }
void operator delete[](void *p)          { free(p); }
void operator delete(void *p, size_t)    { free(p); }
void operator delete[](void *p, size_t)  { free(p); }
