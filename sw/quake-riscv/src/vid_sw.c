#include "fxhal.h"
#include "vid_riscv.h"

#include <string.h>

/* Software path: 320x200 8-bit palette framebuffer the HDMI scanout will
 * later sample (or a host window). No SST-1 traffic. The live WinQuake
 * engine copies into the same layout from port/tang/display.c. */
static unsigned char sw_fb[320 * 200];
static VidBackend g_backend = VID_SW;
static int g_inited;

int VID_Init(VidBackend backend) {
  g_backend = backend;
  g_inited = 1;
  if (backend == VID_SW) {
    memset(sw_fb, 0, sizeof(sw_fb));
    return 0;
  }
  return fxHalOpen(sst_base());
}

void VID_Shutdown(void) {
  if (g_backend == VID_GLIDE) {
    fxHalClose();
  }
  g_inited = 0;
}

void VID_Update(void) {
  /* SW: engine has already drawn into sw_fb. GLIDE: SST-1 owns the FB. */
  (void)g_inited;
}

VidBackend VID_Backend(void) { return g_backend; }

unsigned char *VID_SwFramebuffer(void) { return sw_fb; }
