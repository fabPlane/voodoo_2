# Patches to the `ext/SpinalVoodoo` submodule

`ext/SpinalVoodoo` tracks upstream (`fayalalebrun/SpinalVoodoo`), which this repo can't push to,
so changes made to the core for the Gowin port live here as a patch against the pinned
submodule commit (`git -C ext/SpinalVoodoo rev-parse HEAD`).

    git -C ext/SpinalVoodoo apply ../../fpga/patches/spinalvoodoo-gowin-port.patch

`spinalvoodoo-gowin-port.patch` includes (see fpga/hw/gowin/STATUS.md for details):

- Area: serialised triangle setup (shared multiply-add, sequential edge dividers); rasterizer
  span queue carries per-span fields + a triangle tag instead of the full triangle payload.
- Correctness: Sync=Yes hazard (pipelineBusy covers setup / command pipe / span stage);
  FramebufferPlaneReader lane credits + 5-way fb arbiter shared params (Quake/Tomb/Tower deadlock);
  framebuffer write-buffer drain holds the BMB burst address, and runs never cross a 4 KiB page
  (on hardware the BMB->AXI path wrapped page-crossing runs).
- Timing (50 MHz core): pipeline stages in FramebufferAccess, FramebufferMemSubsystem,
  PixelPipeline, Tmu, SpanWalker, PciFifo; constant-table TexLayoutTables.
- Earlier uncommitted work that was already in the working tree (Lfb, Fog, SpanWalker scanX,
  TmuTextureCache, sim harness / trace_test tweaks).

Regenerate after editing the submodule: `git -C ext/SpinalVoodoo diff > fpga/patches/spinalvoodoo-gowin-port.patch`.
