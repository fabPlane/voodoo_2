# SpinalVoodoo → Gowin port: handoff brief

Written 2026-09-17 for whoever (human or AI) takes the Gowin port from
"generates Verilog" to "renders on the Sipeed Tang Mega 138K Pro Dock".
Everything below was checked on the machine that wrote it; anything not
checked is marked as such.

Live snapshot of what is on this machine, what landed, and the open
blockers (PnR BSRAM 473 > 340): see `STATUS.md`.

## 1. What this is

SpinalVoodoo is a 3dfx Voodoo 1 implemented in SpinalHDL
(`ext/SpinalVoodoo`, git submodule pinned at `28d1435`). Upstream targets the
DE10-Nano (`src/voodoo/de10/CoreDe10.scala`, Avalon + HPS mmap). This repo adds
board ports under `fpga/` without modifying upstream:

| Package | Board | Host bus | Memory | Video |
|---|---|---|---|---|
| `voodoo.artix` | Artix-7 card (own PCB) | AXI4-Lite | 2× AXI4 → MIG | parallel RGB |
| `voodoo.gowin` | Tang Mega 138K Pro Dock | Gowin PCIe TLP → AXI4-Lite | AXI4 → Gowin DDR3 native UI | parallel RGB → `DVI_TX_Top` |

The Gowin port reuses the Artix bridges (`AxiLite4ToBmbBridge`,
`BmbToAxi4Mem`) and adds only what Gowin's vendor IP needs.

## 2. State on 2026-09-17 (verified)

From `fpga/`:

| Step | Command | Result |
|---|---|---|
| Type-check both ports | `make compile` | passes (6 s warm) |
| Generate Gowin Verilog | `make rtl-gowin` | `rtl/gowin/TangMega138kPro.v`, 9.9 MB, plus 11 `.bin` ROM sidecars |
| Verilator lint | `make lint` | clean (Verilator 5.052) |
| SpinalSim tests | `make test` | 6/6 pass: 3× `AxiLite4ToBmbBridgeTest`, 3× `PcieTlpBarTargetTest` (TLP write, TLP read + CplD, 0x250 window) |
| Gowin bitstream | `make gowin` | **not run**: `gw_sh` is not installed here and `hw/gowin/ip/` is empty |
| Board bring-up | — | **not done** |

Nothing in `fpga/` is committed yet (`git status` shows `?? fpga/`). Commit it
before you start so you have a baseline to diff against.

Toolchain quirks that cost time (also in `fpga/README.md`):

- Upstream needs JDK 17 (`//> using jvm "temurin:17"` is already in
  `fpga/project.scala`; do not run with a newer JDK).
- The `fpga/` project is on SpinalHDL **1.15.0** (upstream is 1.13.0). 1.15 is
  required for Verilator ≥ 5.x. Upstream source compiles unchanged on 1.15.
  Don't bump upstream's own `project.scala`.
- `ext/SpinalVoodoo/tools/TmuTextureCacheBenchmark.scala` is stale and does
  not compile; ignore it.
- Icarus is not installed; `fpga/` tests use `withVerilator`.

## 3. Files you will touch

```
fpga/
  project.scala                       scala-cli project; pulls ../ext/SpinalVoodoo/src as source
  Makefile                            compile / rtl-gowin / lint / test / gowin
  src/voodoo/gowin/
    TangMega138kPro.scala             SpinalHDL top: TLP BAR → CDC → CoreGowin → DDR bridge, RGB out, LEDs
    CoreGowin.scala                   Core + BAR bridge + 5-way fb BMB arbiter + BMB→AXI4 + texture relocate
    PcieTlpBarTarget.scala            Gowin TLP (256-bit, 8 valid bits) BAR0 → AXI4-Lite, 1 MiB window at 0x250
    Axi4ToGowinDdrNative.scala        one-outstanding AXI4 (32-bit) → Gowin DDR3 native UI (256-bit)
    AxiLite4ClockCrossing.scala       StreamFifoCC-based AXI-Lite CDC (100 MHz TLP ↔ DDR UI clock)
    GowinAddressMap.scala             fb at 0, 4 MiB; textures at 4 MiB, 8 MiB; DDR 29-bit addr / 256-bit data
    CoreGowinGen.scala                generators; SYNC active-high reset for all clock domains
  test/voodoo/gowin/PcieTlpBarTargetTest.scala
  hw/gowin/
    build.tcl                         gw_sh script; top = board_top; device GW5AST-138B GW5AST-LV138FPG676AES
    src/board_top.v                   vendor-IP wrapper (PLLs, PCIe, DDR3, DVI TX) around TangMega138kPro
    tang_mega_138k_pro.cst            86 IO_LOC lines: clk50, pcie_rstn, LEDs, TMDS, full DDR3 pinout
    tang_mega_138k_pro.sdc            clk50 / clk100 / clk400 / hdmi serial+pixel, all async groups
    ip.md                             the four IP cores to generate, with required port names
    ip/                               EMPTY — generated IP Verilog goes here as ip/<name>/*.v
```

## 4. The work, in order

### 4.1 Generate the vendor IP (Gowin EDA GUI, IP Core Generator)

Full parameters are in `ip.md`. Summary:

| IP | Module name | Key settings |
|---|---|---|
| PLL | `Gowin_PLL_Sys` | 50 MHz in → `clkout0` 100 MHz, `clkout1` 400 MHz, `lock` |
| PLL | `Gowin_PLL_Hdmi` | 50 MHz in → `clkout0` 126 MHz (5× 25.2 MHz), `lock` |
| PCIe | `PCIE_Controller_Top` | Endpoint, Gen2 x4, TLP clock 100 MHz, data width **256**, BAR0 32-bit memory, largest size (1 MiB) |
| DDR3 | `DDR3_Memory_Interface_Top` | **native** UI (not AXI), 32-bit DDR3, data 256, addr 29, `memory_clk` 400 MHz, `clk` 50 MHz |
| DVI | `DVI_TX_Top` | RGB888 in, `I_serial_clk` = 5× pixel |

Port names must match the instantiations in `board_top.v`. If Gowin's
generator emits different names (it has done so across EDA versions; the PCIe
IP may come out wrapped as `SerDes_Top`), write a thin Verilog shim in
`ip/<name>/` rather than editing `board_top.v` — keeps the wrapper stable.

Use the same Gowin EDA version for IP generation and `gw_sh`. The DDR3 IP
changed after 1.9.10.

### 4.2 Build the bitstream

```
make rtl-gowin
make gowin           # cd hw/gowin && gw_sh build.tcl
```

Expect the first synthesis to surface things Verilator does not:

- **ROM sidecars.** SpinalHDL emits `$readmemb` of the `*.bin` files next to
  `TangMega138kPro.v` (dither tables, TMU log/recip tables). Gowin synthesis
  must be able to find them: the paths in the `.v` are relative, so either run
  from `rtl/gowin/` or check `build.tcl` resolves them. If Gowin refuses
  `$readmemb`, the fallback is to regenerate with the ROMs inlined:
  `SpinalConfig(..., inlineRom = true)` in `GowinGenSupport.verilog`
  (`CoreGowinGen.scala`). Do this before spending time on the tool.
- **Block RAM inference.** The core has several dual-port memories
  (texture cache, fb write buffer of 256 words/line, read cache, HDMI CDC
  FIFO). Check the synthesis report for what landed in BSRAM vs. what got
  dropped to LUT RAM; capacity is not the concern on a 138K part, but
  read-during-write semantics differ. `set_option -rw_check_on_ram 1` is
  already set.
- **Timing.** The fabric clock is the DDR3 UI clock (`clk_out` of the DDR
  IP; Gowin's DDR3 IP divides `memory_clk` by 4, so 100 MHz for 400 MHz —
  confirm in the generated IP's report). The SDC only names the PLL nets, so
  add `create_clock` for `ddr_clk_out` if the tool doesn't derive it. If
  timing fails, the first candidates are the BMB arbiter → `BmbToAxi4Mem`
  path and the TMU; upstream runs the same core on a Cyclone V, so this
  should close on GW5A.
- **Resource fit.** Not measured here for either target. Read the Gowin
  report and record LUT/BSRAM/DSP in `fpga/README.md`; that number is also
  what the Artix-100T decision depends on.

### 4.3 Bring-up on the dock

**Verified 2026-10-08 on this machine.** The dock in the slot is
`GW5AST-LV138FPG676AES` (JTAG idcode `0x1081b`, `GW5AST-138`). Sipeed USB
Debugger (`0403:6010`, serial `2025071717`) is on the JTAG header. The
onboard Gowin EDA Education 1.9.11.03 **cannot build** for this 676-pin
part (Sipeed documents **Standard** Edition + a license, not Education).
The first programmed image was Sipeed's prebuilt `led` example,
SRAM only. Six LEDs chase (~125 ms/step). Reset is active-high with a
pull-up: hold the reset button if they stay off. A power cycle drops SRAM
and restores flash. That image does **not** enable the PCIe endpoint;
`lspci` stays empty and a bus rescan does not help.

**Standard 1.9.12.04 is now installed** at
`$HOME/gowin/standard-1.9.12.04` (IDE + Programmer; LicenseServer tarball
left packed — not needed). `fpga/hw/gowin/env.sh` prefers that tree.
`IDE/bin/gwlicense.ini` is Sipeed float `106.55.34.119:10559`
(`gowinlic.sipeed.com` also answers). `gw_sh` accepts
`set_device -name GW5AST-138B GW5AST-LV138FPG676AES`. Sys/HDMI PLLs are
in `hw/gowin/ip/`. First `gw_sh` synthesis of `board_top` completed
2026-10-08 using Sipeed's pre-generated DDR3 / DVI / `SerDes_Top` (IDs
`22c2:1100`). PnR stopped: **473 BSRAM vs 340** on GW5AST-138, plus PLL
VCO out of 650–1300 MHz (sys/hdmi generators now use mdiv 24/25).
Biggest inferred BRAMs: rasterizer `i_fifo` 54, spanWalker fifo 37, TMU
path ~51, dither ROMs 27, DDR3 IP 24. Those raster FIFOs are already
depth 2 and 4 — Gowin is exploding the wide payloads into one 18K tile
per ~36-bit slice. Next: remap shallow/wide FIFOs off BSRAM and rebuild
(see `STATUS.md`). Sipeed float
license drops seats often; retry `gw_sh`. Do not put Programmer/bin on
`LD_LIBRARY_PATH`; `env.sh` preloads distro freetype.

Program with the Gowin programmer or `openFPGALoader -c ft2232`.
**Cold-plug** the PCIe finger; Gowin's IP does not hot-plug, and the Sipeed
demo assumes the FPGA is configured before the host enumerates.

LEDs (`led_n`, active low): `[0]` heartbeat, `[1]` PCIe link, `[2]` DDR3
calibration done, `[3]` host bridge wedged. Order of checks:

1. Heartbeat blinks → PLL locked, fabric clock alive.
2. Calib LED on → DDR3 trained. If not, revisit DDR3 IP timing vs. the actual
   chips on the module (ip.md assumes H5TQ4G63EFR).
3. `lspci` shows the endpoint (bring-up IDs 10ee:0007). If the link LED is on
   but nothing enumerates, retry a cold boot — enumeration happens before the
   bitstream loads from flash unless you have flashed it.
4. Host access: map BAR0 (1 MiB) via `/sys/bus/pci/devices/<bdf>/resource0`.
   Write the megabyte index to **BAR0+0x250**, then the 1 MiB window maps
   `[index<<20, +1 MiB)` of the 16 MiB Voodoo register/LFB/texture space.
   **BAR0+0x254** reads the bridge status word (bit 0 wedged, bit 1 request
   timeout, bit 2 response timeout, bit 4 in flight); it never hangs, so poll
   it first. Semantics are in `src/voodoo/artix/CoreArtix.scala:20-40`.
5. Video: DVI TX pins per Sipeed's dock schematic. LiteX found DVI TX dead
   on some docks and used the DVI RX pins as output instead; if the monitor
   stays dark with a valid HDMI pixel clock, swap the TMDS locations in the
   CST. With DDR3 up, the scanout reads the front buffer at fb base 0 and
   displays 640×480 regardless of whether the host has done anything, so a
   seeded framebuffer (see next step) is the video smoke test.

### 4.4 Host-side tool

`sw/pcie/` is the PCIe port of `ext/SpinalVoodoo/tools/de10-trace-replay.cpp`.
It mmaps `resource0`, writes the megabyte index to BAR0+0x250 when the target
crosses a 1 MiB boundary, and treats BAR0+0x254 as the never-hang status word.

```
make -C sw/pcie
sudo ./sw/pcie/pci-bar status
sudo ./sw/pcie/pci-trace-replay ext/SpinalVoodoo/traces/logo
```

Register / LFB / texture accesses go through the 16 MiB Voodoo map (LFB at
4 MiB, textures at 8 MiB). The Gowin fabric relocates textures to DDR at
`GowinAddressMap.texMemBase` (4 MiB). DE10 `kDefaultFbBase` / `/dev/mem` do
not apply.

Start with the 3dfx logo trace (CoreSim matches 86Box on 0/307,200 pixels)
and read the framebuffer back over the same window for a pixel diff.

## 5. Known risks (real, not hypothetical)

- **Heavy traces wedge the sim.** With this SpinalVoodoo tree's HDMI scanout
  in `Core`, CoreSim on the Screamer 2 / Quake / Valley of Ra traces stalls: a
  PCI write to a remapped triangle-setup register (`0x2000e0` / `0x20008c`)
  never completes after 5M cycles. The logo and `test_alphabet` finish. This
  is upstream `Core`, not the Gowin bridge, but it will show up on the board
  as the wedge LED lighting mid-frame, and the 0x254 status word will report
  a request timeout. Treat "wedge LED on with the logo trace" as a bridge bug
  and "wedge LED on with Quake" as this known upstream issue.
- **`Axi4ToGowinDdrNative` is one-outstanding and serialises fb/tex.** It's
  correct and simple; every AXI burst is 256-byte aligned/extended
  (`axiBurstLog2`). Expect low fill rate. Pipelining it (multiple outstanding
  native commands, separate read/write paths) is the obvious performance
  follow-up once pixels are correct — not before.
- **Gowin BAR0 is 1 MiB max**, hence the window register. Any host software
  that assumes a flat 16 MiB BAR (the Artix path) must go through the window.
- **TLP framing assumptions** in `PcieTlpBarTarget`: 256-bit native data with
  header dword 0 in bits 255:224; 32-bit MRd/MWr only, one DW; no 64-bit
  addressing; completions sized to one DW. Verified in sim against the
  encoding in IPUG1020, not against the real IP. If the first host read
  returns garbage or the host gets a completion timeout, dump `tl_rx_data`
  with an ILA (Gowin Analyzer Oscilloscope) before touching the FSM.
- **CST pin table** was transcribed from the Sipeed reference project. It has
  not been cross-checked against a board. Do the DDR3 pins first — a single
  swapped DQS pair fails calibration silently.

## 6. Acceptance

The port is done when, on the dock:

1. All four LEDs report the right thing after cold plug (heartbeat, link,
   calib, not wedged).
2. `pci-trace-replay traces/logo.bin` runs to completion with 0x254 bit 0 clear.
3. The framebuffer read back over BAR0 matches `ext/SpinalVoodoo/test-output/logo_sim.png`
   pixel-for-pixel (that's the 86Box-verified reference).
4. The monitor on the DVI port shows the same image.

Then commit `fpga/hw/gowin/ip/` shims (not the generated IP if the licence
forbids it — Gowin IP is fine to commit, the PCIe core needs the free licence
for GW5AST but the generated Verilog is redistributable) and update
`fpga/README.md` with the EDA version used.

## 7. Quick reference

```bash
cd fpga
make compile          # 6 s
make rtl-gowin        # ~1 min, writes rtl/gowin/TangMega138kPro.v
make lint             # Verilator, ~6 s
make test             # 6 SpinalSim tests, ~15 s
make gowin            # needs gw_sh + hw/gowin/ip/*
```

Upstream reference flows (in `ext/SpinalVoodoo`, need `scala-cli --power` and
JDK 17): `make native/sim/check/logo` is the 86Box-vs-CoreSim pixel check the
acceptance criterion above mirrors.
