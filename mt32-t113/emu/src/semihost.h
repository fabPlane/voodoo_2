/* semihost.h - see semihost.c. SPDX-License-Identifier: 0BSD */
#ifndef EMU_SEMIHOST_H
#define EMU_SEMIHOST_H

#define SEMIHOST_R  1   /* "rb" */
#define SEMIHOST_W  5   /* "wb" */

int  semihost_open(const char *path, int mode);
int  semihost_write(int handle, const void *buf, unsigned len);
int  semihost_read(int handle, void *buf, unsigned len);
long semihost_flen(int handle);
int  semihost_seek(int handle, long pos);
int  semihost_close(int handle);
int  semihost_cmdline(char *buf, unsigned cap);
int  semihost_available(void);
void semihost_set_available(int on);

#endif
