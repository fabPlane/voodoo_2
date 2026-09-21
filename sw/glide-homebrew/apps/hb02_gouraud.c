/*
 * hb02_gouraud - RGB gouraud-shaded triangle.
 * Exercises: iterated-colour combine + gouraud interpolation.
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 1);
    if (!hb_open()) return 1;

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_ITERATED, GR_COMBINE_OTHER_NONE, FXFALSE);

    while (frames-- > 0) {
        GrVertex a, b, c;
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        hb_vtx(&a, 0.25f * HB_W, 0.25f * HB_H, 255, 0,   0,   255);
        hb_vtx(&b, 0.80f * HB_W, 0.35f * HB_H, 0,   255, 0,   255);
        hb_vtx(&c, 0.50f * HB_W, 0.85f * HB_H, 0,   0,   255, 255);
        grDrawTriangle(&a, &b, &c);
        grBufferSwap(1);
    }
    hb_close();
    return 0;
}
