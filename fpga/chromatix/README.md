# SpinalVoodoo × ChromatiX (ModRetro Chromatic)

Port of the SpinalVoodoo core onto
[enjoy-digital/chromatix](https://github.com/enjoy-digital/chromatix) — the ModRetro
Chromatic (Gowin GW5A-25) rebuilt with LiteX.

## Why this is a stretch target

| Resource | Chromatic (GW5A-25) | Voodoo needs (DE10 / Tang 138K) |
|---|---|---|
| LUT4 | ~25K | full Core sized for ~100K+ class parts |
| External RAM | 8 MiB OPI PSRAM | 4 MiB FB + 8 MiB tex (12 MiB) on DDR |
| Display | 160×144 ST7785 (GB frame) | 640×480 HDMI scanout |

So this branch wires the **integration path** (Spinal fabric → LiteX Instance →
ChromatiX video/USB), with PSRAM-sized address map and a 160×144 downscale into
the existing GB LCD stream. A full Core will almost certainly not close timing
or fit the GW5A-25; the next hardware step is either a reduced Core config or a
larger Gowin (Tang 138K) still speaking the ChromatiX LiteX services.

## Layout

| Path | Role |
|---|---|
| `ext/chromatix` | Upstream ChromatiX submodule |
| `fpga/src/voodoo/chromatix/` | SpinalHDL `CoreChromatix` + `ModRetroChromatic` |
| `fpga/rtl/chromatix/` | Generated Verilog (`make rtl-chromatix`) |
| `fpga/chromatix/voodoo_chromatix.py` | LiteX SoC entry (`--with-voodoo`) |
| `fpga/chromatix/gateware/voodoo_core.py` | Instance + Wishbone/AXI helpers |
| `docs/chromatix-port.md` | Architecture notes |

## Build

```bash
# 1. Spinal HDL → Verilog (needs scala-cli + JDK 17)
git submodule update --init ext/SpinalVoodoo ext/chromatix
make -C fpga rtl-chromatix

# 2. LiteX elaborate (no Gowin EDA)
pip install -e ext/chromatix   # pulls LiteX / Amaranth / LUNA
./fpga/chromatix/voodoo_chromatix.py --build --no-compile
```

Bitstream (workstation with Gowin V1.9.12.04):

```bash
./fpga/chromatix/voodoo_chromatix.py --gowin-path ~/tools/gowin_1.9.12.04/IDE --build --flash
```

## Host map

- Voodoo PCI BAR (AXI4-Lite) at Wishbone `0x5000_0000` (16 MiB window)
- FB AXI → stub for now (replace with PSRAM arbiter port); tex relocated to
  `ChromatixAddressMap.texMemBase` (2 MiB)
- GB LCD stream → ChromatiX `VideoPipeline` → ST7785 + UVC

## Status

- [x] Spinal `CoreChromatix` / `ModRetroChromatic` generate Verilog
- [x] LiteX SoC Instantiates the core and feeds the ChromatiX video pipeline
- [ ] AXI masters → real PSRAM ports (stubs ACK today)
- [ ] Packed Glide demo player (reuse `DemoAxiLitePlayer` once ROMs are staged)
- [ ] GW5A-25 fit / timing (expect fail until Core is reduced)
