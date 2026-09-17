/* mtp_log.h - the log.
 *
 * Goes to the debug UART on the target and to stderr on the host. The render
 * loop must not call this: a printf into a 115200-baud UART is 87 us per
 * character and will blow the block budget. Log at boot, log on error, and use
 * the counters (mtp_audio_underruns, mtp_midi_overruns) for anything periodic.
 *
 * mt32emu talks to this through a ReportHandler subclass, not directly -- see
 * PORTING.md, "printDebug and the libc surface".
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_LOG_H
#define MTP_LOG_H

#include "mtp_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MTP_LOG_NONE  = 0,
    MTP_LOG_ERROR = 1,
    MTP_LOG_WARN  = 2,
    MTP_LOG_INFO  = 3,
    MTP_LOG_DEBUG = 4
} mtp_log_level;

void mtp_log_init(mtp_log_level level);
void mtp_log_set_level(mtp_log_level level);
mtp_log_level mtp_log_get_level(void);

/* printf-style. The implementation is allowed to truncate; it is a log. */
void mtp_log(mtp_log_level level, const char *fmt, ...)
#if defined(__GNUC__)
    __attribute__((format(printf, 2, 3)))
#endif
    ;

#define MTP_LOGE(...) mtp_log(MTP_LOG_ERROR, __VA_ARGS__)
#define MTP_LOGW(...) mtp_log(MTP_LOG_WARN,  __VA_ARGS__)
#define MTP_LOGI(...) mtp_log(MTP_LOG_INFO,  __VA_ARGS__)
#define MTP_LOGD(...) mtp_log(MTP_LOG_DEBUG, __VA_ARGS__)

#ifdef __cplusplus
}
#endif

#endif /* MTP_LOG_H */
