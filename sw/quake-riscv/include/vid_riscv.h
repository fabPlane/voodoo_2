#ifndef VID_RISCV_H
#define VID_RISCV_H

/* WinQuake-shaped video hook. One engine, two backends. */
typedef enum { VID_SW = 0, VID_GLIDE = 1 } VidBackend;

int VID_Init(VidBackend backend);
void VID_Shutdown(void);
void VID_Update(void);
VidBackend VID_Backend(void);
unsigned char *VID_SwFramebuffer(void);
void VID_GlideBeginFrame(void);
void VID_GlideSwap(void);

#endif
