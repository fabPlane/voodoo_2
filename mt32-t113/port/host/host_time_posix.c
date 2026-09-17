/* host_time_posix.c - timebase, host stub: CLOCK_MONOTONIC.
 * SPDX-License-Identifier: 0BSD */

#define _POSIX_C_SOURCE 200809L
#include "mtp_time.h"
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
    nanosleep(&ts, 0);
}
