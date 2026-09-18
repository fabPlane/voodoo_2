/* desktop_log.c - mtp_log on a desktop: stderr, with a lock and a clock.
 *
 * Differs from port/host/host_log_stderr.c in two ways that this build needs:
 * it is called from several threads (the MIDI poll thread, CoreMIDI's callback
 * thread, the render loop), so it takes a mutex; and it stamps each line with
 * uptime, because when you are chasing a dropout you want to know whether the
 * warning came before or after it.
 *
 * It also cooperates with the status line in main.c: the status line is written
 * to stderr with a carriage return and no newline, so a log line has to clear
 * it first or the two overwrite each other.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include "mtp_log.h"
#include "mtp_time.h"
#include "desktop_status.h"

#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>

static mtp_log_level   g_level = MTP_LOG_INFO;
static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;
static const char *const TAG[] = { "", "E", "W", "I", "D" };

void mtp_log_init(mtp_log_level l)      { g_level = l; }
void mtp_log_set_level(mtp_log_level l) { g_level = l; }
mtp_log_level mtp_log_get_level(void)   { return g_level; }

void mtp_log(mtp_log_level level, const char *fmt, ...)
{
    va_list ap;
    uint64_t t;

    if (level > g_level || level == MTP_LOG_NONE) return;
    t = mtp_time_us64();

    pthread_mutex_lock(&g_mtx);
    desktop_status_clear_line();
    fprintf(stderr, "[%3u.%03u %s] ",
            (unsigned)(t / 1000000ull), (unsigned)((t / 1000ull) % 1000ull),
            TAG[level]);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
    pthread_mutex_unlock(&g_mtx);
}

const char *mtp_strerror(mtp_status s)
{
    switch (s) {
    case MTP_OK:        return "ok";
    case MTP_ERR_IO:    return "I/O error";
    case MTP_ERR_NOENT: return "no such file";
    case MTP_ERR_NOMEM: return "out of memory";
    case MTP_ERR_INVAL: return "invalid argument";
    case MTP_ERR_AGAIN: return "would block";
    case MTP_ERR_STATE: return "wrong state";
    default:            return "unknown";
    }
}
