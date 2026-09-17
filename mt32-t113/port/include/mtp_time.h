/* mtp_time.h - the timebase.
 *
 * One free-running microsecond counter. On the T113 this is the ARM
 * architectural generic timer (CNTVCT), which is 64-bit, always runs, and does
 * not stop when a core WFIs -- unlike a peripheral timer, which is also a
 * perfectly good source but one more thing to configure.
 *
 * Everything in this port measures *differences*, so the 32-bit wrap at ~71.6
 * minutes is harmless as long as callers subtract in uint32_t. The 64-bit call
 * exists for log timestamps and for uptime, where the wrap would be confusing.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_TIME_H
#define MTP_TIME_H

#include "mtp_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

mtp_status mtp_time_init(void);

/* Microseconds since mtp_time_init(). Wraps; subtract in uint32_t. */
uint32_t mtp_time_us(void);

/* Same clock, no wrap. */
uint64_t mtp_time_us64(void);

/* Busy-wait. For driver setup only -- never call this from the render loop. */
void mtp_time_delay_us(uint32_t us);

#ifdef __cplusplus
}
#endif

#endif /* MTP_TIME_H */
