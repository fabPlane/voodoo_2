/* C stubs for the FPU-control-word helpers that only exist as x86 assembly
 * (sys_dosa.s) in the id Quake tree. The portable C build (id386=0) never needs
 * the real rounding-mode tweaks, so no-ops are sufficient. */
void Sys_SetFPCW(void) {}
void Sys_PushFPCW_SetHigh(void) {}
void Sys_PopFPCW(void) {}
void MaskExceptions(void) {}
