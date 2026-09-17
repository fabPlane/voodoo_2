/* host_storage_posix.c - storage, host stub: the filesystem.
 * SPDX-License-Identifier: 0BSD */

#include "mtp_storage.h"
#include "mtp_log.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct mtp_file { FILE *fp; long size; };

static const char *g_root = ".";
void host_storage_set_root(const char *r) { if (r) g_root = r; }

static void join(char *dst, size_t cap, const char *path)
{
    if (path[0] == '/') { snprintf(dst, cap, "%s", path); return; }
    snprintf(dst, cap, "%s/%s", g_root, path);
}

mtp_status mtp_storage_mount(void)   { return MTP_OK; }
void       mtp_storage_unmount(void) { }

mtp_status mtp_storage_open(const char *path, mtp_file **out)
{
    char full[1024];
    mtp_file *f;
    if (!path || !out) return MTP_ERR_INVAL;
    join(full, sizeof(full), path);
    f = (mtp_file *)calloc(1, sizeof(*f));
    if (!f) return MTP_ERR_NOMEM;
    f->fp = fopen(full, "rb");
    if (!f->fp) { free(f); return MTP_ERR_NOENT; }
    fseek(f->fp, 0, SEEK_END); f->size = ftell(f->fp); fseek(f->fp, 0, SEEK_SET);
    *out = f;
    return MTP_OK;
}

void mtp_storage_close(mtp_file *f)
{
    if (!f) return;
    if (f->fp) fclose(f->fp);
    free(f);
}

long mtp_storage_size(mtp_file *f) { return f ? f->size : -1; }

long mtp_storage_read(mtp_file *f, void *dst, long n)
{
    if (!f || !f->fp || n < 0) return -1;
    return (long)fread(dst, 1, (size_t)n, f->fp);
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
    mtp_status s = mtp_storage_open(path, &f);
    long n;
    if (s != MTP_OK) return s;
    if (f->size > cap) { mtp_storage_close(f); return MTP_ERR_NOMEM; }
    n = mtp_storage_read(f, dst, f->size);
    mtp_storage_close(f);
    if (n != f->size && n < 0) return MTP_ERR_IO;
    if (out_len) *out_len = n;
    return MTP_OK;
}
