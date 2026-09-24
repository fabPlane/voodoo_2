/*
 * hb03_quad - a gouraud gradient quad built from two triangles.
 * Exercises: multi-triangle draw + shared-edge interpolation.
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 1);
    if (!hb_open()) return 1;

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_ITERATED, GR_COMBINE_OTHER_NONE, FXFALSE);

    while (frames-- > 0) {
        GrVertex tl, tr, bl, br;
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        hb_vtx(&tl, 0.15f * HB_W, 0.15f * HB_H, 255, 0,   0,   255);
        hb_vtx(&tr, 0.85f * HB_W, 0.15f * HB_H, 0,   255, 0,   255);
        hb_vtx(&bl, 0.15f * HB_W, 0.85f * HB_H, 0,   0,   255, 255);
        hb_vtx(&br, 0.85f * HB_W, 0.85f * HB_H, 255, 255, 0,   255);
        grDrawTriangle(&tl, &tr, &bl);
        grDrawTriangle(&tr, &br, &bl);
        grBufferSwap(1);
    }
    hb_close();
    return 0;
}
