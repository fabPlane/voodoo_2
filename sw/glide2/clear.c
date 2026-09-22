/*
 * One frame through glide2x.dll: open SST-1 and clear to a solid colour.
 * The DLL is the trace-capture Glide2 runtime, so this writes a Voodoo
 * trace instead of touching a card. Wine runs the exe; trace_test replays it.
 */
#include <stdio.h>
#include <glide.h>

int main(void)
{
    GrHwConfiguration hwconfig;

    fprintf(stderr, "glide2-clear: start\n");
    fflush(stderr);
    grGlideInit();
    fprintf(stderr, "glide2-clear: init done\n");
    fflush(stderr);
    if (!grSstQueryHardware(&hwconfig)) {
        fprintf(stderr, "glide2-clear: grSstQueryHardware failed\n");
        return 1;
    }
    grSstSelect(0);
    if (!grSstWinOpen(0,
                      GR_RESOLUTION_640x480,
                      GR_REFRESH_60Hz,
                      GR_COLORFORMAT_ABGR,
                      GR_ORIGIN_UPPER_LEFT,
                      2, 1)) {
        fprintf(stderr, "glide2-clear: grSstWinOpen failed\n");
        return 1;
    }

    /* Same colour as the Glide SDK test00 clear (blue in this format). */
    grBufferClear(0x00ff0000, 0, GR_WDEPTHVALUE_FARTHEST);
    grBufferSwap(1);

    grSstWinClose();
    grGlideShutdown();
    fprintf(stderr, "glide2-clear: ok\n");
    return 0;
}
