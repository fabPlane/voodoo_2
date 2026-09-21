/*
 * hb06_alpha - overlapping translucent triangles.
 * Exercises: grAlphaCombine + grAlphaBlendFunction (src-alpha blending).
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

static void tri(float ox, float oy, int r, int g, int b, int alpha) {
    GrVertex a, c, d;
    grConstantColorValue(hb_abgr(alpha, b, g, r));
    hb_vtx(&a, ox + 0.10f * HB_W, oy + 0.10f * HB_H, 0, 0, 0, 0);
    hb_vtx(&c, ox + 0.50f * HB_W, oy + 0.12f * HB_H, 0, 0, 0, 0);
    hb_vtx(&d, ox + 0.28f * HB_W, oy + 0.55f * HB_H, 0, 0, 0, 0);
    grDrawTriangle(&a, &c, &d);
}

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 1);
    if (!hb_open()) return 1;

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_CONSTANT, GR_COMBINE_OTHER_NONE, FXFALSE);
    grAlphaCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_CONSTANT, GR_COMBINE_OTHER_NONE, FXFALSE);
    grAlphaBlendFunction(GR_BLEND_SRC_ALPHA, GR_BLEND_ONE_MINUS_SRC_ALPHA,
                         GR_BLEND_ZERO, GR_BLEND_ZERO);

    while (frames-- > 0) {
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        tri(0.10f * HB_W, 0.15f * HB_H, 255, 40,  40,  160);
        tri(0.22f * HB_W, 0.22f * HB_H, 40,  255, 40,  160);
        tri(0.34f * HB_W, 0.29f * HB_H, 40,  40,  255, 160);
        grBufferSwap(1);
    }
    hb_close();
    return 0;
}
