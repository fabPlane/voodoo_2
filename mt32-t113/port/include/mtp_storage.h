/* mtp_storage.h - read-only storage for ROMs and config.
 *
 * Scope on purpose: open, size, read, close, exists. No writing, no directory
 * enumeration, no seeking. Everything this project reads off the card is read
 * once, whole, at boot: two ROM images and one text file. Anything that needs
 * more than this (log files, SoundFont streaming) is a new requirement and
 * should extend the interface then, not now.
 *
 * mt32emu's own file abstraction (mt32emu/File.h) wants a flat const Bit8u*
 * plus a size, so the natural shape here is "read the whole thing into a
 * buffer I give you" -- see mtp_storage_load(). The streaming read exists for
 * the 1 MB CM-32L PCM ROM, where reading in chunks lets the caller show
 * progress rather than stall for a second with no console output.
 *
 * SPDX-License-Identifier: 0BSD
 */
#ifndef MTP_STORAGE_H
#define MTP_STORAGE_H

#include "mtp_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mtp_file mtp_file;

/* Brings up the card and mounts the first FAT partition. Idempotent. */
mtp_status mtp_storage_mount(void);
void       mtp_storage_unmount(void);

/* Paths are '/'-separated and relative to the volume root: "roms/MT32_CONTROL.ROM". */
mtp_status mtp_storage_open(const char *path, mtp_file **out);
void       mtp_storage_close(mtp_file *f);

/* Size in bytes, or -1. */
long mtp_storage_size(mtp_file *f);

/* Reads up to n bytes; returns the count read, 0 at end of file, or -1. */
long mtp_storage_read(mtp_file *f, void *dst, long n);

/* 1 if the path can be opened, 0 otherwise. Used for the "is there a CM-32L
 * ROM set on this card?" probe at boot. */
int mtp_storage_exists(const char *path);

/* Convenience: open, read the whole file into dst (capacity cap), close.
 * On success *out_len holds the byte count. Returns MTP_ERR_NOMEM if the file
 * is larger than cap -- which for a ROM means the wrong file, and the caller
 * should say so rather than truncate. */
mtp_status mtp_storage_load(const char *path, void *dst, long cap, long *out_len);

#ifdef __cplusplus
}
#endif

#endif /* MTP_STORAGE_H */
