/*
 * hb05_depth - overlapping triangles resolved by the Z-buffer.
 * Exercises: grDepthBufferMode/Function/Mask + per-vertex ooz + Z clear.
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

static void tri(float ox, float oy, float z, int r, int g, int b) {
    GrVertex a, c, d;
    float ooz = 65535.0f / z;
    grConstantColorValue(hb_abgr(0xff, b, g, r));
    hb_vtx(&a, ox + 0.10f * HB_W, oy + 0.10f * HB_H, 0, 0, 0, 0);
    hb_vtx(&c, ox + 0.45f * HB_W, oy + 0.15f * HB_H, 0, 0, 0, 0);
    hb_vtx(&d, ox + 0.25f * HB_W, oy + 0.50f * HB_H, 0, 0, 0, 0);
    a.ooz = c.ooz = d.ooz = ooz;
    grDrawTriangle(&a, &c, &d);
}

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 1);
    if (!hb_open()) return 1;

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_CONSTANT, GR_COMBINE_OTHER_NONE, FXFALSE);
    grDepthBufferMode(GR_DEPTHBUFFER_ZBUFFER);
    grDepthBufferFunction(GR_CMP_GREATER);
    grDepthMask(FXTRUE);

    while (frames-- > 0) {
        grBufferClear(0x00000000, 0, GR_ZDEPTHVALUE_FARTHEST);
        tri(0.10f * HB_W, 0.10f * HB_H, 3.0f, 255, 40,  40);   /* far  */
        tri(0.22f * HB_W, 0.22f * HB_H, 2.0f, 40,  255, 40);   /* mid  */
        tri(0.34f * HB_W, 0.34f * HB_H, 1.2f, 40,  40,  255);  /* near */
        grBufferSwap(1);
    }
    hb_close();
    return 0;
}
