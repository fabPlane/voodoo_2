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
#ifdef HB_LIVE
#include <signal.h>
#include <time.h>
#endif

#include <glide.h>
#include <glideutl.h>

/* Screen size in pixels. Defaults to 640x480 (what the console ROMs capture);
 * set HB_RES=320x200 (or 320x240) to render smaller/faster screenshots. All
 * demos place geometry as fractions of HB_W/HB_H, so they scale automatically. */
static float HB_W = 640.0f;
static float HB_H = 480.0f;

static GrHwConfiguration hb_hw;

/* Open a double-buffered, ABGR window on the first SST. */
static int hb_open(void) {
    GrScreenResolution_t res = GR_RESOLUTION_640x480;
    const char *want = getenv("HB_RES");
    if (want && !strcmp(want, "320x200")) { res = GR_RESOLUTION_320x200; HB_W = 320; HB_H = 200; }
    else if (want && !strcmp(want, "320x240")) { res = GR_RESOLUTION_320x240; HB_W = 320; HB_H = 240; }
    else if (want && !strcmp(want, "512x384")) { res = GR_RESOLUTION_512x384; HB_W = 512; HB_H = 384; }

#ifdef HB_LIVE
    /* Live on the board over PCIe: the dock's HDMI port is the display, so
     * don't mirror every frame into an X11 window (that reads the whole
     * framebuffer back over BAR0 on each swap). */
    setenv("GLIDE_X11_ENABLE", "0", 0);
#endif
    grGlideInit();
    if (!grSstQueryHardware(&hb_hw)) {
        fprintf(stderr, "hb: no Voodoo hardware detected\n");
        return 0;
    }
    grSstSelect(0);
    if (!grSstWinOpen(0, res, GR_REFRESH_60Hz,
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
#ifdef HB_LIVE
        if (!strcmp(argv[1], "0")) return 0x7fffffff;   /* until Ctrl-C */
#endif
    }
    return def;
}

#ifdef HB_LIVE
/* Ctrl-C ends the frame loop cleanly so grGlideShutdown still runs. */
static volatile sig_atomic_t hb_stop;
static void hb_on_sigint(int sig) { (void)sig; hb_stop = 1; }
static void hb_live_begin(void) { signal(SIGINT, hb_on_sigint); }

/* Call once per frame; prints frames/s to stderr about once a second. */
static void hb_fps_tick(void) {
    static struct timespec t0;
    static int n;
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    if (!n++) { t0 = t; return; }
    double dt = (t.tv_sec - t0.tv_sec) + (t.tv_nsec - t0.tv_nsec) * 1e-9;
    if (dt >= 1.0) {
        fprintf(stderr, "hb: %.1f frames/s\n", (n - 1) / dt);
        t0 = t;
        n = 1;
    }
}
#endif

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
