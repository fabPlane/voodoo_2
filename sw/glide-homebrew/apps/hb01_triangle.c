/*
 * hb01_triangle - a single flat-shaded triangle.
 * Exercises: constant colour combine + triangle rasterisation.
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 1);
    if (!hb_open()) return 1;

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_CONSTANT, GR_COMBINE_OTHER_NONE, FXFALSE);
    grConstantColorValue(hb_abgr(0xff, 0x20, 0xa0, 0xe0));

    while (frames-- > 0) {
        GrVertex a, b, c;
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        hb_vtx(&a, 0.20f * HB_W, 0.20f * HB_H, 0, 0, 0, 0);
        hb_vtx(&b, 0.80f * HB_W, 0.30f * HB_H, 0, 0, 0, 0);
        hb_vtx(&c, 0.50f * HB_W, 0.85f * HB_H, 0, 0, 0, 0);
        grDrawTriangle(&a, &b, &c);
        grBufferSwap(1);
    }
    hb_close();
    return 0;
}
