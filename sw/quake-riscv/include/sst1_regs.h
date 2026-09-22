#ifndef SST1_REGS_H
#define SST1_REGS_H

/* Hardware SST-1 offsets (10-bit FBI/TMU, 16 MiB BAR). Same numbers as
 * 86Box vid_voodoo_regs.h and SpinalVoodoo RegisterBank. */

enum {
  SST_status = 0x000,
  SST_intrCtrl = 0x004,

  SST_vertexAx = 0x008,
  SST_vertexAy = 0x00c,
  SST_vertexBx = 0x010,
  SST_vertexBy = 0x014,
  SST_vertexCx = 0x018,
  SST_vertexCy = 0x01c,

  SST_triangleCMD = 0x080,

  SST_fvertexAx = 0x088,
  SST_fvertexAy = 0x08c,
  SST_fvertexBx = 0x090,
  SST_fvertexBy = 0x094,
  SST_fvertexCx = 0x098,
  SST_fvertexCy = 0x09c,
  SST_fstartR = 0x0a0,
  SST_fstartG = 0x0a4,
  SST_fstartB = 0x0a8,
  SST_fdRdX = 0x0c0,
  SST_fdGdX = 0x0c4,
  SST_fdBdX = 0x0c8,
  SST_fdRdY = 0x0e0,
  SST_fdGdY = 0x0e4,
  SST_fdBdY = 0x0e8,
  SST_ftriangleCMD = 0x100,

  SST_fbzColorPath = 0x104,
  SST_fogMode = 0x108,
  SST_alphaMode = 0x10c,
  SST_fbzMode = 0x110,
  SST_lfbMode = 0x114,
  SST_clipLeftRight = 0x118,
  SST_clipLowYHighY = 0x11c,

  SST_nopCMD = 0x120,
  SST_fastfillCMD = 0x124,
  SST_swapbufferCMD = 0x128,

  SST_fogColor = 0x12c,
  SST_zaColor = 0x130,
  SST_chromaKey = 0x134,
  SST_stipple = 0x140,
  SST_color0 = 0x144,
  SST_color1 = 0x148,

  SST_fbiInit4 = 0x200,
  SST_vRetrace = 0x204,
  SST_backPorch = 0x208,
  SST_videoDimensions = 0x20c,
  SST_fbiInit0 = 0x210,
  SST_fbiInit1 = 0x214,
  SST_fbiInit2 = 0x218,
  SST_fbiInit3 = 0x21c,
  SST_hSync = 0x220,
  SST_vSync = 0x224,

  SST_trexInit0 = 0x31c,
  SST_trexInit1 = 0x320,
  SST_textureMode = 0x300,
  SST_tLOD = 0x304,
  SST_tDetail = 0x308,
  SST_texBaseAddr = 0x30c
};

/* status: RegisterBank pciFifoFree[5:0], vRetrace@6, fbiBusy@7, trexBusy@8,
 * sstBusy@9. Glide2x sst.h uses the same busy bits. */
#define SST_FBI_BUSY (1u << 7)
#define SST_TREX_BUSY (1u << 8)
#define SST_BUSY (1u << 9)
#define SST_ENGINE_BUSY (SST_FBI_BUSY | SST_TREX_BUSY | SST_BUSY)

#define FBIINIT0_GRAPHICS_RESET (1u << 1)
#define FBIINIT1_VIDEO_RESET (1u << 8)
#define FBIINIT3_REMAP 1u

#define SST_FBZ_RGB_WRITE (1u << 9)
#define SST_FBZ_AUX_WRITE (1u << 10)
#define SST_FBZ_CLIPPING (1u << 0)

#endif
