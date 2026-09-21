#include "tang_sw.h"

#include <quakembd.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Same 320x200 8-bit FB as vid_sw.c / HDMI scanout. WinQuake draws into its
 * own hunk buffer; qembd_fillrect copies dirty rects here. */
#define DISPLAY_WIDTH 320
#define DISPLAY_HEIGHT 200

static unsigned char sw_fb[DISPLAY_WIDTH * DISPLAY_HEIGHT];
static uint32_t last_clut[256];
static int dumped;

int qembd_get_width(void) { return DISPLAY_WIDTH; }

int qembd_get_height(void) { return DISPLAY_HEIGHT; }

unsigned char *VID_SwFramebuffer(void) { return sw_fb; }

static void dump_ppm(void) {
  const char *path;
  FILE *f;
  int i;

  if (dumped) {
    return;
  }
  path = getenv("QUAKE_SW_PPM");
  if (!path || !path[0]) {
    return;
  }
  f = fopen(path, "wb");
  if (!f) {
    return;
  }
  dumped = 1;
  fprintf(f, "P6\n%d %d\n255\n", DISPLAY_WIDTH, DISPLAY_HEIGHT);
  for (i = 0; i < DISPLAY_WIDTH * DISPLAY_HEIGHT; i++) {
    uint32_t c = last_clut[sw_fb[i]];
    unsigned char rgb[3];
    rgb[0] = (unsigned char)((c >> 16) & 0xff);
    rgb[1] = (unsigned char)((c >> 8) & 0xff);
    rgb[2] = (unsigned char)(c & 0xff);
    fwrite(rgb, 1, 3, f);
  }
  fclose(f);
}

void qembd_vidinit(void) {
  memset(sw_fb, 0, sizeof(sw_fb));
  atexit(dump_ppm);
}

void qembd_fillrect(uint8_t *src, uint32_t *clut, uint16_t x, uint16_t y,
                    uint16_t xsize, uint16_t ysize) {
  int py;

  if (clut) {
    memcpy(last_clut, clut, sizeof(last_clut));
  }
  for (py = 0; py < ysize; py++) {
    int row = y + py;
    int offset;
    if (row < 0 || row >= DISPLAY_HEIGHT) {
      continue;
    }
    offset = row * DISPLAY_WIDTH + x;
    if (x + xsize > DISPLAY_WIDTH) {
      continue;
    }
    memcpy(sw_fb + offset, src + offset, xsize);
  }
}

void qembd_refresh(void) {
  /* FPGA: HDMI samples sw_fb. Host PPM is written at exit (QUAKE_SW_PPM). */
}
