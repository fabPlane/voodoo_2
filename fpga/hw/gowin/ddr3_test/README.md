# DDR3 bring-up over USB-JTAG (Tang Mega 138K Pro Dock)

Small standalone bitstream that validates the DDR3 path of `../src/board_top.v`
(same `Gowin_PLL_Sys` + `DDR3_Memory_Interface_Top` wiring, `pll_stop` →
PLL enable, same instance names / `INS_LOC`s) and lets the host push data into
DDR3 through the on-board USB debugger. No UART or PCIe needed.

- `ddr3_jtag_top.v`: DDR3 IP + command executor + GW_JTAG ER1 (IR `0x42`)
  64-bit command register + hardware BIST. Protocol in the file header.
- `ddr3_jtag.py`: host tool (starts `sudo openocd` and talks to its Tcl port).
- `sim/tb.v`: iverilog testbench with behavioural DDR3 / JTAG models
  (random ready stalls, injected bit fault).
- `build.tcl`, `ddr3_test.cst` (subset of the main CST), `ddr3_test.sdc`.

## Build, load, use

```sh
cd fpga/hw/gowin/ddr3_test
. ../env.sh && gw_sh build.tcl          # ~2 min; impl/pnr/ddr3_test.fs
sudo openFPGALoader -c ft2232 --freq 6000000 -m impl/pnr/ddr3_test.fs   # SRAM only
python3 ddr3_jtag.py --khz 6000 status
python3 ddr3_jtag.py --khz 6000 bist --bursts 0x2000000 --gap 15     # all 1 GiB
python3 ddr3_jtag.py --khz 6000 write FILE --addr 0x1000000
python3 ddr3_jtag.py --khz 6000 verify FILE --addr 0x1000000
python3 ddr3_jtag.py --khz 6000 read --addr 0x1000000 --len 256
```

Sim: `iverilog -g2012 -o /tmp/tb sim/tb.v ddr3_jtag_top.v && vvp -n /tmp/tb`.

LEDs (active low): 0 UI-clock heartbeat, 1 PLL lock, 2 calibration done,
3 BIST pass (solid) / fail (blink).

## Results (2026-10-09)

- Calibration completes; UI clock measures 100.0 MHz. Timing met (105 MHz).
- **All 1 GiB** passes the BIST (address-unique pattern, seeds 0x1234 and
  0xffff) at `--gap 15`; 512 MiB passes at `--gap 8`.
- 64 KiB file written at 0x1000000 over JTAG (~10 KiB/s), verified
  byte-exact, still intact after 2.5 min idle (refresh works when idle).

### Two load-dependent failures (matter for the Voodoo design)

`--gap N` = idle UI cycles forced between native commands.

1. **gap ≤ 2 (≥ ~1 GB/s of back-to-back writes) for > ~1 ms: the board's
   user JTAG dies.** Afterwards IR 0x42 behaves as 1-bit BYPASS until the
   bitstream is reloaded; the config status register is unchanged. 2 MiB at
   gap 0 passes (too short to trigger). Suspect supply droop / SSO under
   sustained DQ switching; worth checking how the dock is powered (USB only
   vs. PCIe slot / DC jack).
2. **gap 4, ≥ 512 MiB: sparse errors (~0.1 % of bursts, all DQ bits,
   first bad at 0x0).** Not aliasing (that would hit ~50 %). gap 8 / 15 are
   clean over far longer runs (512 MiB at gap 4 takes 1.7 s and fails;
   at gap 15 it takes 5.4 s and passes), so it is load, not elapsed time.
   It is *not* refresh starvation: the IP's `ref_ack` count (BISTRES 6)
   tracks tREFI = 7.8 us (~128k/s) straight through the BIST. Pulsing the
   user `ref_req` (op B, `--refresh N`) hung the controller, so leave it
   at 0. Both failures scale with sustained DQ activity, which points at
   power / signal integrity rather than the controller.

The production `Axi4ToGowinDdrNative` keeps one transaction in flight and
collects 8 AXI beats per native write, so it naturally stays in the
gap ≥ 8 regime. Keep that in mind before adding read prefetch / bursting.

### Gotcha: GW_JTAG outputs must be sampled on the falling edge

GW_JTAG launches `tdi_o` (and its state outputs) on the TCK rising edge.
Sampling them on the same rising edge raced on this board: every received
bit came in as `tdi[i] | tdi[i+1]` (0xA → 0xF). `ddr3_jtag_top.v` retimes
them on `negedge` first. The console mailbox design samples on `posedge`
and happens to work there; anything reusing it on the Pro needs the fix.
