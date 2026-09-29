# Chromatix port notes

## Goal

Run SpinalVoodoo on the ModRetro Chromatic by sitting inside
[ChromatiX](https://github.com/enjoy-digital/chromatix) instead of inventing another
board package from scratch. ChromatiX already owns CRG, OPI PSRAM, ST7785, UVC/UAC/CDC,
and the debug bridge — the Voodoo core should look like another alternate core, the
same way `--with-bios` replaces the Game Boy with a VexRiscv SoC.

## Data path

```
 LiteX Wishbone (CPU / UARTBone)
        │  BAR @ 0x5000_0000
        ▼
 AXI4-Lite ──► CoreChromatix / Core (SpinalVoodoo)
                    │              │
              AXI4 memFb     AXI4 memTex (+2 MiB relocate)
                    │              │
                    └──── PSRAM (TODO; stubs today) ────┘
                    │
              HdmiScanoutPort (640×480 RGB)
                    │
              GbLcdFromScanout (160×144 RGB555)
                    │
              ChromatiX VideoPipeline → ST7785 + UVC
```

## Address map (8 MiB APS6408L)

See `fpga/src/voodoo/chromatix/ChromatixAddressMap.scala`:

- `0x000000` · 2 MiB framebuffer (RGB565 double + Z for 640×480)
- `0x200000` · 4 MiB textures (half of the DE10/Artix window)
- ChromatiX video blend still uses `0x10000` for the 160×144 GB frame

## Fit reality

The Chromatic’s GW5A-25 is the part our own FPGA options write-up lists as **too
small** for a full Voodoo (~$45 at LCSC). This port still generates the full Core so
we can measure utilisation once Gowin EDA is available, and so simulation / Verilator
can run the same RTL the handheld would load. Expect either:

1. a reduced `Config` (no TMU / smaller caches) for bring-up on GW5A-25, or
2. keep the ChromatiX LiteX services and retarget the same Spinal wrapper at a
   Tang Mega 138K / larger GW5A when we want real Glide demos on device.

## Next steps

1. Add MultiPortRAMCtrl ports for Voodoo FB/Tex (replace `Axi4MemStub`).
2. Stage a tiny packed Glide trace and drive the BAR with `DemoAxiLitePlayer`.
3. Run Gowin synth for utilisation; trim Config until it fits or move boards.
4. Optionally upstream a `--with-voodoo` flag to ChromatiX once PSRAM + fit are real.
