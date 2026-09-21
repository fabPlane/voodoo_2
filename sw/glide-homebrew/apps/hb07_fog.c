/*
 * hb07_fog - table fog applied to receding triangles.
 * Exercises: grFogMode/grFogTable/grFogColorValue + per-vertex oow (W fog).
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 1);
    GrFog_t fogtable[GR_FOG_TABLE_SIZE];
    int f = 0;
    if (!hb_open()) return 1;

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_CONSTANT, GR_COMBINE_OTHER_NONE, FXFALSE);
    grConstantColorValue(hb_abgr(0xff, 230, 230, 230));
    grFogMode(GR_FOG_WITH_TABLE);
    grFogColorValue(hb_abgr(0x00, 0x60, 0x20, 0x20));
    guFogGenerateExp(fogtable, 0.02f);
    grFogTable(fogtable);

    while (frames-- > 0) {
        int i;
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        for (i = 0; i < 5; i++) {
            float w = 1.0f + (float)(i + f % 3) * 2.0f;
            float oow = 1.0f / w;
            float y = 0.15f * HB_H + i * 0.13f * HB_H;
            GrVertex a, b, c;
            hb_vtx(&a, 0.20f * HB_W, y,               0, 0, 0, 0);
            hb_vtx(&b, 0.80f * HB_W, y,               0, 0, 0, 0);
            hb_vtx(&c, 0.50f * HB_W, y + 0.10f * HB_H, 0, 0, 0, 0);
            a.oow = b.oow = c.oow = oow;
            grDrawTriangle(&a, &b, &c);
        }
        grBufferSwap(1);
        f++;
    }
    hb_close();
    return 0;
}
