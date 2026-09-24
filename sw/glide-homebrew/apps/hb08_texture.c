/*
 * hb08_texture - a procedural checkerboard texture on a quad.
 * Exercises: grTexDownloadMipMap/grTexSource/grTexCombine + textured combine.
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

#define TSZ 32

static FxU16 checker[TSZ * TSZ];

static FxU16 rgb565(int r, int g, int b) {
    return (FxU16)(((r & 0xf8) << 8) | ((g & 0xfc) << 3) | (b >> 3));
}

static void build_texture(void) {
    int x, y;
    for (y = 0; y < TSZ; y++) {
        for (x = 0; x < TSZ; x++) {
            int cell = ((x >> 2) ^ (y >> 2)) & 1;
            checker[y * TSZ + x] = cell
                ? rgb565(240, 220, 40)
                : rgb565(30, 60, 200);
        }
    }
}

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 1);
    GrTexInfo info;
    if (!hb_open()) return 1;

    build_texture();
    info.smallLod    = GR_LOD_32;
    info.largeLod    = GR_LOD_32;
    info.aspectRatio = GR_ASPECT_1x1;
    info.format      = GR_TEXFMT_RGB_565;
    info.data        = checker;

    grTexDownloadMipMap(GR_TMU0, grTexMinAddress(GR_TMU0),
                        GR_MIPMAPLEVELMASK_BOTH, &info);
    grTexSource(GR_TMU0, grTexMinAddress(GR_TMU0),
                GR_MIPMAPLEVELMASK_BOTH, &info);
    grTexCombine(GR_TMU0,
                 GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                 GR_COMBINE_FUNCTION_LOCAL, GR_COMBINE_FACTOR_NONE,
                 FXFALSE, FXFALSE);
    grTexFilterMode(GR_TMU0, GR_TEXTUREFILTER_BILINEAR,
                    GR_TEXTUREFILTER_BILINEAR);
    grColorCombine(GR_COMBINE_FUNCTION_SCALE_OTHER, GR_COMBINE_FACTOR_ONE,
                   GR_COMBINE_LOCAL_NONE, GR_COMBINE_OTHER_TEXTURE, FXFALSE);

    while (frames-- > 0) {
        GrVertex tl, tr, bl, br;
        grBufferClear(0x00000000, 0, GR_WDEPTHVALUE_FARTHEST);
        hb_vtx(&tl, 0.15f * HB_W, 0.15f * HB_H, 255, 255, 255, 255);
        hb_vtx(&tr, 0.85f * HB_W, 0.15f * HB_H, 255, 255, 255, 255);
        hb_vtx(&bl, 0.15f * HB_W, 0.85f * HB_H, 255, 255, 255, 255);
        hb_vtx(&br, 0.85f * HB_W, 0.85f * HB_H, 255, 255, 255, 255);
        tl.tmuvtx[0].sow = 0.0f;   tl.tmuvtx[0].tow = 0.0f;
        tr.tmuvtx[0].sow = 255.0f; tr.tmuvtx[0].tow = 0.0f;
        bl.tmuvtx[0].sow = 0.0f;   bl.tmuvtx[0].tow = 255.0f;
        br.tmuvtx[0].sow = 255.0f; br.tmuvtx[0].tow = 255.0f;
        tl.tmuvtx[0].oow = tr.tmuvtx[0].oow = 1.0f;
        bl.tmuvtx[0].oow = br.tmuvtx[0].oow = 1.0f;
        grDrawTriangle(&tl, &tr, &bl);
        grDrawTriangle(&tr, &br, &bl);
        grBufferSwap(1);
    }
    hb_close();
    return 0;
}
