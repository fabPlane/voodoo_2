/*
 * hb00_clear - cycle the framebuffer clear colour.
 * Exercises: fastfill / grBufferClear + grBufferSwap (colour buffer path).
 * SPDX-License-Identifier: MIT
 */
#include "hbcommon.h"

int main(int argc, char **argv) {
    int frames = hb_frames(argc, argv, 6);
    int i = 0;
    static const GrColor_t palette[6] = {
        0x000000ff, 0x0000ff00, 0x00ff0000,
        0x0000ffff, 0x00ffff00, 0x00ff00ff
    };
    if (!hb_open()) return 1;
    while (frames-- > 0) {
        grBufferClear(palette[i % 6], 0, GR_WDEPTHVALUE_FARTHEST);
        grBufferSwap(1);
        i++;
    }
    hb_close();
    return 0;
}
