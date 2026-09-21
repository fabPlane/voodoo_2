/*
 * hbcommon.h - shared boilerplate for the SpinalVoodoo homebrew Glide demos.
 *
 * These demos are original works written to exercise the Voodoo/Glide2x path
 * of the SpinalVoodoo core (as run on the Sipeed Tang Retro Console 138K).
 * They use the 3dfx Glide2x SDK API but contain no 3dfx sample code.
 *
 * SPDX-License-Identifier: MIT
 */
#ifndef HB_COMMON_H
#define HB_COMMON_H

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

#include <glide.h>
#include <glideutl.h>

#define HB_W 640.0f
#define HB_H 480.0f

static GrHwConfiguration hb_hw;

/* Open a 640x480, double-buffered, ABGR window on the first SST. */
static int hb_open(void) {
    grGlideInit();
    if (!grSstQueryHardware(&hb_hw)) {
        fprintf(stderr, "hb: no Voodoo hardware detected\n");
        return 0;
    }
    grSstSelect(0);
    if (!grSstWinOpen(0, GR_RESOLUTION_640x480, GR_REFRESH_60Hz,
                      GR_COLORFORMAT_ABGR, GR_ORIGIN_UPPER_LEFT, 2, 1)) {
        fprintf(stderr, "hb: grSstWinOpen failed\n");
        return 0;
    }
    return 1;
}

static void hb_close(void) {
    grGlideShutdown();
}

/* Frame count: optional first CLI arg, else default. Kept small so the
 * captured command stream fits comfortably in the on-chip player ROM. */
static int hb_frames(int argc, char **argv, int def) {
    if (argc > 1) {
        int n = atoi(argv[1]);
        if (n > 0) return n;
    }
    return def;
}

/* Fill a gouraud/solid screen-space vertex. */
static void hb_vtx(GrVertex *v, float x, float y,
                   float r, float g, float b, float a) {
    memset(v, 0, sizeof(*v));
    v->x = x;  v->y = y;
    v->r = r;  v->g = g;  v->b = b;  v->a = a;
    v->ooz = 0.0f;
    v->oow = 1.0f;
}

/* Pack an ABGR constant colour (matches GR_COLORFORMAT_ABGR). */
static GrColor_t hb_abgr(int a, int b, int g, int r) {
    return ((GrColor_t)(a & 0xff) << 24) | ((GrColor_t)(b & 0xff) << 16) |
           ((GrColor_t)(g & 0xff) << 8) | (GrColor_t)(r & 0xff);
}

#endif /* HB_COMMON_H */
