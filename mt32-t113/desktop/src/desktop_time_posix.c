/* desktop_time_posix.c - mtp_time on a desktop: CLOCK_MONOTONIC.
 *
 * The same shape as port/host/host_time_posix.c. It is repeated here rather
 * than shared because the platform layer is the thing being implemented three
 * times, and a seam that is only implemented once in each direction is not
 * evidence of anything. CLOCK_MONOTONIC exists on macOS from 10.12.
 *
 * SPDX-License-Identifier: 0BSD
 */

#define _POSIX_C_SOURCE 200809L
#include "mtp_time.h"

#include <errno.h>
#include <time.h>

static uint64_t g_base;

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000ull;
}

mtp_status mtp_time_init(void) { g_base = now_us(); return MTP_OK; }
uint64_t   mtp_time_us64(void) { return now_us() - g_base; }
uint32_t   mtp_time_us(void)   { return (uint32_t)mtp_time_us64(); }

void mtp_time_delay_us(uint32_t us)
{
    struct timespec ts;
    ts.tv_sec  = (time_t)(us / 1000000u);
    ts.tv_nsec = (long)(us % 1000000u) * 1000L;
    while (nanosleep(&ts, &ts) != 0 && errno == EINTR) { }
}
