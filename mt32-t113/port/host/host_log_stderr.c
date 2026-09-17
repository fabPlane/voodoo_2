/* host_log_stderr.c - log, host stub: stderr.
 * SPDX-License-Identifier: 0BSD */

#include "mtp_log.h"
#include <stdarg.h>
#include <stdio.h>

static mtp_log_level g_level = MTP_LOG_INFO;
static const char *const TAG[] = { "", "E", "W", "I", "D" };

void mtp_log_init(mtp_log_level l)      { g_level = l; }
void mtp_log_set_level(mtp_log_level l) { g_level = l; }
mtp_log_level mtp_log_get_level(void)   { return g_level; }

void mtp_log(mtp_log_level level, const char *fmt, ...)
{
    va_list ap;
    if (level > g_level || level == MTP_LOG_NONE) return;
    fprintf(stderr, "[%s] ", TAG[level]);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
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
