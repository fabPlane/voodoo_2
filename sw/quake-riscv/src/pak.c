#include "pak.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
  char name[56];
  uint32_t ofs;
  uint32_t len;
} PakEntry;

static FILE *g_f;
static PakEntry *g_ents;
static int g_n;

static uint32_t read_u32(FILE *f) {
  unsigned char b[4];
  if (fread(b, 1, 4, f) != 4) {
    return 0;
  }
  return (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) |
         ((uint32_t)b[3] << 24);
}

void pak_close(void) {
  if (g_f) {
    fclose(g_f);
    g_f = 0;
  }
  free(g_ents);
  g_ents = 0;
  g_n = 0;
}

int pak_open(const char *path) {
  char magic[4];
  uint32_t dir_ofs;
  uint32_t dir_len;
  int i;

  pak_close();
  g_f = fopen(path, "rb");
  if (!g_f) {
    return -1;
  }
  if (fread(magic, 1, 4, g_f) != 4 || memcmp(magic, "PACK", 4) != 0) {
    pak_close();
    return -1;
  }
  dir_ofs = read_u32(g_f);
  dir_len = read_u32(g_f);
  if (dir_len % 64u != 0 || dir_len / 64u > 4096u) {
    pak_close();
    return -1;
  }
  g_n = (int)(dir_len / 64u);
  g_ents = (PakEntry *)calloc((size_t)g_n, sizeof(PakEntry));
  if (!g_ents || fseek(g_f, (long)dir_ofs, SEEK_SET) != 0) {
    pak_close();
    return -1;
  }
  for (i = 0; i < g_n; i++) {
    if (fread(g_ents[i].name, 1, 56, g_f) != 56) {
      pak_close();
      return -1;
    }
    g_ents[i].name[55] = 0;
    g_ents[i].ofs = read_u32(g_f);
    g_ents[i].len = read_u32(g_f);
  }
  return 0;
}

int pak_count(void) { return g_n; }

int pak_has(const char *name) {
  int i;
  for (i = 0; i < g_n; i++) {
    if (strcmp(g_ents[i].name, name) == 0) {
      return 1;
    }
  }
  return 0;
}

int pak_extract(const char *name, const char *out_path) {
  int i;
  FILE *out;
  uint32_t left;
  char buf[4096];

  for (i = 0; i < g_n; i++) {
    if (strcmp(g_ents[i].name, name) != 0) {
      continue;
    }
    if (fseek(g_f, (long)g_ents[i].ofs, SEEK_SET) != 0) {
      return -1;
    }
    out = fopen(out_path, "wb");
    if (!out) {
      return -1;
    }
    left = g_ents[i].len;
    while (left) {
      size_t n = left > sizeof(buf) ? sizeof(buf) : (size_t)left;
      if (fread(buf, 1, n, g_f) != n || fwrite(buf, 1, n, out) != n) {
        fclose(out);
        return -1;
      }
      left -= (uint32_t)n;
    }
    fclose(out);
    return 0;
  }
  return -1;
}
