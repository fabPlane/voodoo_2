/*
 * hb04_pinwheel - a rotating fan of coloured triangles.
 * Exercises: many triangles/frame + per-frame animation (colour cycling).
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

#define BLADES 12

/* Flat-shaded blades keep the captured command stream compact (per-blade
 * colour comes from the vertex colour, uniform across the triangle). */
int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 2);
    int f = 0;
    const float cx = 0.5f * HB_W, cy = 0.5f * HB_H, radius = 0.42f * HB_H;
    if (!hb_open()) return 1;
#ifdef HB_LIVE
    hb_live_begin();
#endif

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_CONSTANT, GR_COMBINE_OTHER_NONE, FXFALSE);

#ifdef HB_LIVE
    while (frames-- > 0 && !hb_stop) {
#else
    while (frames-- > 0) {
#endif
        int k;
        float phase = (float)f * 0.35f;
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        for (k = 0; k < BLADES; k++) {
            float a0 = phase + (float)k * (2.0f * 3.14159265f / BLADES);
            float a1 = a0 + (2.0f * 3.14159265f / BLADES) * 0.5f;
            float hue = (float)k / BLADES;
            GrVertex c, p0, p1;
            grConstantColorValue(hb_abgr(0xff, 128,
                                         (int)(255.0f * (1.0f - hue)),
                                         (int)(255.0f * hue)));
            hb_vtx(&c,  cx, cy, 0, 0, 0, 0);
            hb_vtx(&p0, cx + radius * cosf(a0), cy + radius * sinf(a0), 0, 0, 0, 0);
            hb_vtx(&p1, cx + radius * cosf(a1), cy + radius * sinf(a1), 0, 0, 0, 0);
            grDrawTriangle(&c, &p0, &p1);
        }
        grBufferSwap(1);
#ifdef HB_LIVE
        hb_fps_tick();
#endif
        f++;
    }
    hb_close();
    return 0;
}
