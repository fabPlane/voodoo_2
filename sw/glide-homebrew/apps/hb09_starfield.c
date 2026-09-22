/*
 * hb09_starfield - many small triangles streaming outward.
 * Exercises: high primitive count / command throughput to the core.
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

#define STARS 24

/* Each star is a small flat triangle (uniform vertex colour) so a large
 * primitive count still packs into a compact console ROM. */
int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 2);
    int f = 0;
    unsigned seed = 1u;
    if (!hb_open()) return 1;

    grColorCombine(GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                   GR_COMBINE_LOCAL_CONSTANT, GR_COMBINE_OTHER_NONE, FXFALSE);

    while (frames-- > 0) {
        int i;
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        seed = 1u; /* deterministic layout, animated by frame f */
        for (i = 0; i < STARS; i++) {
            float bx, by, s;
            int r, g;
            GrVertex v0, v1, v2;
            seed = seed * 1103515245u + 12345u;
            bx = (float)((seed >> 16) & 0x1ff) / 512.0f * HB_W;
            seed = seed * 1103515245u + 12345u;
            by = (float)((seed >> 16) & 0x1ff) / 512.0f * HB_H;
            bx = fmodf(bx + (float)f * 7.0f, HB_W);
            s  = 3.0f + (float)((seed >> 8) & 7);
            r  = 120 + (int)((seed >> 4) & 0x7f);
            g  = 120 + (int)((seed >> 11) & 0x7f);
            grConstantColorValue(hb_abgr(0xff, 200, g, r));
            hb_vtx(&v0, bx,     by,     0, 0, 0, 0);
            hb_vtx(&v1, bx + s, by,     0, 0, 0, 0);
            hb_vtx(&v2, bx,     by + s, 0, 0, 0, 0);
            grDrawTriangle(&v0, &v1, &v2);
        }
        grBufferSwap(1);
        f++;
    }
    hb_close();
    return 0;
}
