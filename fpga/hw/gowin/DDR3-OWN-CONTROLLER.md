# Replacing the Gowin DDR3 IP with our own controller (brief)

Status: proposal, not started. Written 2026-10-18 for a separate agent.

## Why

The Voodoo core on the Tang Mega 138K Pro dock (GW5AST-LV138FPG676A) is close
to full. B119 after place and route uses BSRAM 301/340 (89%) and CLS
58123/69120 (85%). Only about 1 placement in 12 meets timing on every clock,
so every new feature costs hours of builds. The vendor DDR3 IP (`u_ddr3`,
`DDR3_Memory_Interface_Top`) is the largest single block we don't control:

| Vendor DDR3 IP (B119 synthesis, `gowin-q33/impl/gwsynthesis/*_syn_resource.html`) | |
|---|---|
| BSRAM | 24 |
| Registers | 5815 |
| LUT / ALU | 3386 / 189 |
| Hard I/O | IDES8_MEM 32, OSER8_MEM 40, OSER8 25, IODELAY 32, DQS 4, DDRDLL 1 |

A lean controller that drives the same hard DDR primitives directly could
plausibly need 0-8 BSRAM, ~1.5-3k registers and ~1.5-2.5k LUT. That frees about
16-24 BSRAM and 1.5-2.5k CLS (2-4%). This is an estimate; nobody has written or
measured one yet. The hard I/O primitives can't be avoided.

The open-flow branches (`open/ddr3-phy`, `open/pcie-apicula`) contain no DDR3
controller: only plans and an unrun IODELAY fuzzing harness. This work would use
the **vendor toolchain** (gw_sh), not apicula/nextpnr.

## Interface to keep

Keep the Gowin native UI, so the existing adapter
`fpga/src/voodoo/gowin/Axi4ToGowinDdrNative.scala` (bundle `GowinDdrNative`)
works unchanged:

- Commands: `cmd` (3 bits; 0 = write, 1 = read), `cmd_en`, `cmd_ready`,
  `addr` (`GowinAddressMap.ddrNativeAddrWidth`), `burst` (tied 0).
- Writes: `wr_data` (256 bits), `wr_data_en`, `wr_data_end`, `wr_data_mask`
  (32 bits, **active-high inhibit**), `wr_data_rdy`.
- Reads: `rd_data` (256 bits), `rd_data_valid`, `rd_data_end`.
- `init_calib_complete`, `clk_out` (the UI clock, 100 MHz, = memory clock / 4),
  `ddr_rst`, `pll_stop` (gates `memory_clk` during calibration). `sr_req` and
  `ref_req` are tied 0 (the IP refreshes by itself).
- Addressing: `addr` is a **DDR word address**, not a byte address. One 1:4
  BL8 transfer moves 256 bits and consumes 8 addresses (x32 device:
  4 bytes per address). See the comments at the top of `Axi4ToGowinDdrNative`.
- Read data returns **in command order** and can't be stalled. The adapter
  reserves FIFO space for every read it issues (`readsInFlight`, now 16).

The instance and pin wiring are in `fpga/hw/gowin-q2/src/board_top.v` (`u_ddr3`;
note `O_ddr_odt` comes out as `ddr_odt_ip`). Pins and SSTL15 I/O standards are
in `fpga/hw/gowin-q2/tang_mega_138k_pro.cst`.

## Memory configuration (from `ip/DDR3_Memory_Interface_Top/*.ipc`)

DDR3, 32-bit data (DQ_WIDTH=3), 1 rank, CLK_RATIO 1:4, MEMORY_CLK 400 MHz
(DDR3-800), BURST_MODE BL8, tRFC 260 ns, ROW_ADDR / COL_ADDR / RANK_ADDR codes
3 / 0 / 1. The board carries 1 GiB. The 400 MHz memory clock and the 50 MHz
reference come from `Gowin_PLL_Sys` (clkout2 = VCO 1200 / 3; `enclk2` driven by
`pll_stop`).

## Hard-won board facts (read before touching DDR timing)

- **Load sensitivity.** Sustained native commands closer than ~8 UI cycles
  apart once killed JTAG / reloaded the FPGA. The adapter has a run-time
  throttle: BAR window 31 offset 0x64; [7:0] idle cycles after each command,
  [11:8] max reads in flight (0 = all); autoplay now uses 0x000. Recent builds
  run GLQuake for hours at 0x000. A new controller must be re-validated the
  same way.
- **BIST:** `fpga/hw/gowin/ddr3_test` (and `_t1`.._t3) is a standalone DDR3
  tester. Use it first, at several gaps and over the full 1 GiB.
- **Board access:** always go through `fpga/hw/gowin/board-lock -m "<why>" -- ...`.
  A PCIe bitstream must be in SPI flash and the PC rebooted to take effect
  (there is no hot load). Flash with `sudo openFPGALoader -c ft2232 --freq 6000000
  -f --verify <fs>` under the board lock. Post a message before rebooting.
  Standalone DDR tests can load into SRAM over JTAG without a reboot, as long as
  PCIe isn't involved.
- **Timing closure** is the bottleneck. Check timing on every clock
  (`impl/pnr/*.tr`, "Total Negative Slack Summary" all zero) before trusting a
  bitstream. The DDR UI clock (`ddr_clk_out`, 100 MHz) and the PCIe clock
  (`clk100`, 75 MHz) are the ones that usually fail.

## Suggested plan

1. **Read the vendor IP's structure** (the encrypted netlist won't help). Use
   the Gowin DDR3 memory interface user guide and the GW5AST primitive guide
   (IDES8_MEM / OSER8_MEM / DQS / DDRDLL / IODELAY) to pin down the PHY:
   DQS gating, write levelling, read data eye training.
2. **Standalone first.** Build the new PHY + controller in a scratch project
   with the `ddr3_test` BIST top (not the Voodoo core): init sequence (MR0-3,
   ZQCL), refresh, read and write paths, calibration. Load over JTAG to SRAM.
   Pass BIST over all 1 GiB at gap 0 and gap 8, repeatedly, across a warm-up.
3. **Measure** BSRAM / LUT / regs / timing against the vendor IP in the same
   test top. Only continue if it saves at least ~10 BSRAM or ~1.5k CLS.
4. **Integrate** behind the same native interface in a branch off
   `cursor/sipeed-pcie-dock`, e.g. a `useOwnDdr3` switch in the board top so
   the vendor IP stays one flag away. Run `Axi4ToGowinDdrNativeTest` against a
   behavioural model of the new controller.
5. **Board validation:** GLQuake via autoplay (`~/.cache/glqhang/play_forever.sh`)
   for at least an hour, watching `~/.cache/glqhang/pcie_watch.log` (dockCmd
   must stay 0006; anything else is an FPGA reload) and the stream for
   corruption (`~/projects/scratch/kvm/tools/passive_capture.py`,
   `find_glitch2.py`).

## Risks

- Calibration (DQS gate, read levelling) on this board may be marginal. The
  vendor IP hides that, and the board already shows load sensitivity.
- Weeks of effort for 2-4% of the chip. Shrinking our own memories (dither ROMs
  done in 58b05db, -39 BSRAM; wide pipeline FIFOs next) frees more with far
  less risk. Treat this as a parallel, optional track.
- Don't break `cursor/sipeed-pcie-dock`. Work on a separate branch or worktree,
  and never run `git checkout`/`reset` inside `ext/SpinalVoodoo` (see
  `fpga/patches/spinalvoodoo-gowin-port.patch` for how ext changes are kept).
