/* emu_storage.c - mtp_storage.h over semihosting.
 *
 * On the board this is FAT on the SMHC controller. Here it is the host's own
 * filesystem, reached through the same debugger channel as the console, so the
 * *interface* gets exercised for real -- open, size, read, close, load-whole-
 * file, and, importantly, the failure path.
 *
 * The failure path is the normal case and always will be: MT-32 ROMs are
 * Roland's and this repository will never contain them (docs/PLAN.md). So the
 * behaviour port/DESIGN.md section 4.3 specifies -- name the exact path that
 * was not found, keep the console up, keep the audio running, never hang --
 * is what this image does every time it is run without ROM dumps, which makes
 * it the most-tested path in the port rather than the least.
 *
 * Paths are taken relative to a root string, exactly as host_storage_posix.c
 * does, so the same --control-rom argument works in all three harnesses.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

#include "mtp_storage.h"
#include "mtp_log.h"
#include "semihost.h"

struct mtp_file { int handle; long size; };

static const char *g_root = ".";
static int g_mounted;
static struct mtp_file g_files[4];
static char g_path[256];

void emu_storage_set_root(const char *r) { if (r) g_root = r; }

static const char *join(const char *path)
{
    unsigned n = 0, i;
    if (path[0] == '/') { /* absolute: use as given */
        while (path[n] && n < sizeof(g_path) - 1u) { g_path[n] = path[n]; n++; }
        g_path[n] = '\0';
        return g_path;
    }
    for (i = 0; g_root[i] && n < sizeof(g_path) - 2u; i++) g_path[n++] = g_root[i];
    if (n && g_path[n - 1u] != '/') g_path[n++] = '/';
    for (i = 0; path[i] && n < sizeof(g_path) - 1u; i++) g_path[n++] = path[i];
    g_path[n] = '\0';
    return g_path;
}

mtp_status mtp_storage_mount(void)
{
    if (!semihost_available()) {
        MTP_LOGW("storage: no semihosting host; the card is empty");
        g_mounted = 1;              /* not an error: see DESIGN.md 4.3 */
        return MTP_OK;
    }
    g_mounted = 1;
    MTP_LOGI("storage: host filesystem via semihosting, root '%s'", g_root);
    return MTP_OK;
}

void mtp_storage_unmount(void) { g_mounted = 0; }

mtp_status mtp_storage_open(const char *path, mtp_file **out)
{
    int h, i;
    if (!g_mounted) return MTP_ERR_STATE;
    if (!path || !out) return MTP_ERR_INVAL;

    for (i = 0; i < 4; i++) if (g_files[i].handle == 0) break;
    if (i == 4) return MTP_ERR_NOMEM;

    h = semihost_open(join(path), SEMIHOST_R);
    if (h < 0) return MTP_ERR_NOENT;

    g_files[i].handle = h;
    g_files[i].size   = semihost_flen(h);
    *out = &g_files[i];
    return MTP_OK;
}

void mtp_storage_close(mtp_file *f)
{
    if (!f || f->handle == 0) return;
    semihost_close(f->handle);
    f->handle = 0;
    f->size = 0;
}

long mtp_storage_size(mtp_file *f) { return f ? f->size : -1; }

long mtp_storage_read(mtp_file *f, void *dst, long n)
{
    int got;
    if (!f || f->handle == 0 || n <= 0) return -1;
    got = semihost_read(f->handle, dst, (unsigned)n);
    return got < 0 ? -1 : (long)got;
}

int mtp_storage_exists(const char *path)
{
    mtp_file *f;
    if (mtp_storage_open(path, &f) != MTP_OK) return 0;
    mtp_storage_close(f);
    return 1;
}

mtp_status mtp_storage_load(const char *path, void *dst, long cap, long *out_len)
{
    mtp_file *f;
    mtp_status s;
    long size, got;

    s = mtp_storage_open(path, &f);
    if (s != MTP_OK) return s;

    size = mtp_storage_size(f);
    if (size < 0)      { mtp_storage_close(f); return MTP_ERR_IO; }
    if (size > cap)    { mtp_storage_close(f);
                         MTP_LOGE("'%s' is %ld bytes, buffer is %ld", path, size, cap);
                         return MTP_ERR_NOMEM; }

    got = mtp_storage_read(f, dst, size);
    mtp_storage_close(f);
    if (got != size) return MTP_ERR_IO;
    if (out_len) *out_len = got;
    return MTP_OK;
}
