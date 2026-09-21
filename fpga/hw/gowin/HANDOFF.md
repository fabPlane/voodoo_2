# SpinalVoodoo → Gowin port: handoff brief

Written 2026-09-17. The board in hand is the **Tang Retro Console 138K**
(GW5AST-LV138PG484A **version C**), not the 676-ball Pro Dock. Take this from
"generates Verilog" to "HDMI menu on the Console". Everything below was
checked on the machine that wrote it; anything not checked is marked as such.

## 1. What this is

SpinalVoodoo is a 3dfx Voodoo 1 implemented in SpinalHDL
(`ext/SpinalVoodoo`, git submodule pinned at `28d1435`). Upstream targets the
DE10-Nano (`src/voodoo/de10/CoreDe10.scala`, Avalon + HPS mmap). This repo adds
board ports under `fpga/` without modifying upstream:

| Package | Board | Host bus | Memory | Video |
|---|---|---|---|---|
| `voodoo.artix` | Artix-7 card (own PCB) | AXI4-Lite | 2× AXI4 → MIG | parallel RGB |
| `voodoo.gowin` | Tang Mega 138K Pro Dock | Gowin PCIe TLP → AXI4-Lite | AXI4 → Gowin DDR3 native UI | parallel RGB → `DVI_TX_Top` |
| `voodoo.gowin` | **Tang Retro Console 138K (live)** | USB-pad menu, RISC-V Quake SW/Glide | AXI4 → Gowin DDR3 native UI | parallel RGB → `DVI_TX_Top` |

The Gowin port reuses the Artix bridges (`AxiLite4ToBmbBridge`,
`BmbToAxi4Mem`) and adds only what Gowin's vendor IP needs. The Console top
(`TangConsole138k`) has no PCIe.

## 2. State on 2026-09-17 (verified)

From `fpga/` on this Mac (no `gw_sh`):

| Step | Command | Result |
|---|---|---|
| Type-check | `make compile` | passes |
| Console Verilog | `make rtl-console` | `rtl/gowin/TangConsole138k.v` (two-item Quake menu) |
| Dock Verilog | `make rtl-gowin` | `rtl/gowin/TangMega138kPro.v` (PCIe path; not the live board) |
| SpinalSim tests | `make test` | TLP BAR + AXI-Lite tests pass |
| Console bitstream | `make gowin-console` | **not run**: no Gowin EDA here, `hw/gowin/ip/` is empty |
| Board bring-up | — | **blocked on bitstream** |

Live SD card (`/Volumes/NO NAME/`, factory TangCore FAT32):

- Stock `cores/console138k/{nes,snes,gba,sms,md,pc,monitor}tang.bin` **untouched**.
- Staging copy at `voodoo/` (`README.txt`, CoreSim/86Box stills, packed traces). **Not** a TangCore core.
- TangCore only lists `cores/console138k/*.bin`. Do **not** drop a dummy `voodootang.bin`; the Console would fail to configure.
- There is no `cores/console138k/voodootang.bin` until another machine runs Gowin.

On a machine with Gowin EDA 1.9.10+:

1. Generate PLL / DDR3 native / DVI TX IP for **GW5AST-LV138PG484AC1/I0, device version C**. Skip PCIe. See `ip.md`.
2. `cd fpga && make gowin-console`
3. Copy `spinalvoodoo_tang_console_138k.bin` to `cores/console138k/voodootang.bin` on the card.

HDMI 640×480. USB pad: D-pad move, A run, B back. See `hardware/tang-console-138k/README.md`.

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
  Makefile                            compile / rtl-console / gowin-console / …
  src/voodoo/gowin/
    TangConsole138k.scala             Console top: DDR wait → HDMI menu (Quake SW / Glide)
    Bl616Link.scala                   TangCore UART HID (command 0x09)
    CoreGowin.scala                   Core + BMB→AXI4 + texture relocate (shared with dock)
    Axi4ToGowinDdrNative.scala        one-outstanding AXI4 → Gowin DDR3 native UI
    GowinAddressMap.scala             fb at 0, 4 MiB; textures at 4 MiB
  hw/gowin/
    ip.md                             PLL / DDR3 native / DVI TX; skip PCIe for Console
    ip/                               EMPTY — generated IP Verilog goes here as ip/<name>/*.v
    console/
      board_top.v                     PLLs, DDR3, DVI TX around TangConsole138k; no PCIe
      build_console.tcl               device GW5AST-LV138PG484AC1/I0 -device_version C
      tang_console_138k.cst/.sdc
      (packed traces unused by the two-item Quake menu)
```

Pro Dock (676-ball, PCIe) files stay under `hw/gowin/` (`build.tcl`,
`src/board_top.v`, `tang_mega_138k_pro.cst`). Do not use those for this card.

## 4. The work, in order

### 4.1 Generate the vendor IP (Gowin EDA GUI, IP Core Generator)

Device **GW5AST-LV138PG484AC1/I0**, version **C**. Full parameters in `ip.md`.
**Do not generate the PCIe core** for the Console.

| IP | Module name | Key settings |
|---|---|---|
| PLL | `Gowin_PLL_Sys` | 50 MHz in → `clkout0` 100 MHz, `clkout1` 400 MHz, `lock` |
| PLL | `Gowin_PLL_Hdmi` | 50 MHz in → `clkout0` 126 MHz (5× 25.2 MHz), `lock` |
| DDR3 | `DDR3_Memory_Interface_Top` | **native** UI (not AXI), 32-bit DDR3, data 256, addr 29, `memory_clk` 400 MHz, `clk` 50 MHz |
| DVI | `DVI_TX_Top` | RGB888 in, `I_serial_clk` = 5× pixel |

Port names must match the instantiations in `hw/gowin/console/board_top.v`.
If Gowin's generator emits different names, write a thin Verilog shim in
`ip/<name>/` rather than editing `board_top.v`.

Use the same Gowin EDA version for IP generation and `gw_sh`. The DDR3 IP
changed after 1.9.10.

### 4.2 Build the bitstream

```
make rtl-console
make gowin-console           # cd hw/gowin/console && gw_sh build_console.tcl
```

Copy `hw/gowin/console/impl/pnr/spinalvoodoo_tang_console_138k.bin`
(exact impl path may vary with EDA version) to the SD card as
`cores/console138k/voodootang.bin`. Leave the stock NES/SNES/… bins alone.

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

### 4.3 Bring-up on the Console

Program via TangCore (the `.bin` on the SD card) or `openFPGALoader` once the
BL616 is in GAO-bridge / MCU USB JTAG mode. TangCore’s Select core list only
includes files already under `cores/console138k/`.

LEDs (`led_n`, active low): `[0]` heartbeat, `[1]` info page open.
Order of checks:

1. Heartbeat blinks → PLL locked, fabric clock alive.
2. HDMI 640×480 text menu after DDR3 trains. If the monitor stays dark, swap
   TMDS locations in `console/tang_console_138k.cst` (same LiteX DVI TX/RX
   gotcha as the dock). If it never leaves a blank screen, DDR calib failed —
   regenerate DDR3 IP for the chips actually on the Console module.
3. USB pad (SNES layout from BL616 command 0x09): D-pad moves, A/Start opens
   the highlighted info page, B/Select returns. Onboard USER cycles, RESET
   selects. Live play waits for A25 (or a soft RV32) on the AXI-lite BAR.
4. Menu: Quake RISC-V SW, Quake RISC-V Glide. Same engine
   (`sw/quake-riscv`), two vid backends. A25 is not in this fabric yet.

Packed Glide traces on the SD card (`voodoo/traces/`) are leftover from the
earlier splash work. The Console menu does not replay them.

### 4.4 Pro Dock / PCIe (not the live board)

The 676-ball Tang Mega 138K Pro path is `make gowin` / `hw/gowin/build.tcl`,
device `GW5AST-LV138FPG676AES`. That needs the PCIe IP as well. Host tools are
still DE10 `/dev/mem`; a `pci-trace-replay` wrapping BAR0+0x250 does not exist
yet. Skip this until the Console menu works.

## 5. Known risks (real, not hypothetical)

- **Wrong package.** Console is PG484 version **C**. Pro Dock is FPG676
  version B. Mixing CST/IP between them will fail DDR calib or not fit.
- **Heavy traces wedge the sim.** With this SpinalVoodoo tree's HDMI scanout
  in `Core`, CoreSim on the Screamer 2 / Quake / Valley of Ra traces stalls: a
  PCI write to a remapped triangle-setup register (`0x2000e0` / `0x20008c`)
  never completes after 5M cycles. The logo, `test04`, `test16`, and
  `test_alphabet` finish. That is why the Console menu is RISC-V Quake
  (software + Glide MMIO), not a packed game-trace replay.
- **`Axi4ToGowinDdrNative` is one-outstanding and serialises fb/tex.** It's
  correct and simple; every AXI burst is 256-byte aligned/extended
  (`axiBurstLog2`). Expect low fill rate. Pipelining it (multiple outstanding
  native commands, separate read/write paths) is the follow-up once pixels
  are correct — not before.
- **CST pin table** for the Console HDMI/clock/LED pins was taken from
  working TangCore cores. DDR3 pins must still match the module. A swapped
  DQS pair fails calibration silently.

## 6. Acceptance

The Console port is done when:

1. `cores/console138k/voodootang.bin` appears in TangCore Select core (or
   the bitstream is programmed over MCU JTAG) and LED0 heartbeats.
2. HDMI shows the text menu after DDR3 trains.
3. USB pad walks the two Quake entries and A opens the matching info page.
4. LED1 lights on an info page. Live SW/Glide play is `sw/quake-riscv` once
   A25 (or a soft RV32) is on the BAR.

Then record the Gowin EDA version in `fpga/README.md`. Commit IP shims, not
generated vendor files if the licence forbids it.

## 7. Quick reference

```bash
cd fpga
make compile              # type-check
make rtl-console          # TangConsole138k.v
make test
make gowin-console        # needs gw_sh + hw/gowin/ip/* (PLL, DDR3, DVI; no PCIe)
# copy spinalvoodoo_tang_console_138k.bin → cores/console138k/voodootang.bin
```

Device: `GW5AST-LV138PG484AC1/I0 -device_version C`.
