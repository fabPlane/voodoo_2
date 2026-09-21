#ifndef PAK_H
#define PAK_H

#include <stdio.h>

/* id1/pak0.pak (PACK). Used to prove the WinQuake shareware demo is present. */
int pak_open(const char *path);
void pak_close(void);
int pak_has(const char *name);
int pak_extract(const char *name, const char *out_path);
int pak_count(void);

#endif
