# Getting the PCIe design onto the open flow

Branch `open/pcie-apicula`. Written 10 Oct 2026 after mapping the vendor
design (`fpga/hw/gowin`), the open flow (`fpga/hw/gowin-open`), and the
fabPlane apicula and nextpnr forks.

## Where things stand

The vendor PCIe design (`fpga/hw/gowin/src/board_top.v`, Spinal
`TangMega138kPro`) depends on four pieces that the open flow does not have:

| Block | Vendor form | Hard primitives | Open-flow status |
| --- | --- | --- | --- |
| PCIe Gen2 x4 | `ip/PCIE_Controller_Top/serdes.v` (plain) + `pcie_controller.v`, `upar_arbiter.v` (encrypted) | `GTR12_QUAD`, `GTR12_UPAR`, `GTR12_PMAC`; config via `serdes.csr` UPAR register writes | none. apicula `dat_parser.py` only reads the `Gtrl12*` tables |
| DDR3 | Sipeed prebuilt, encrypted | `IDES8_MEM`, `OSER8_MEM`, `IODELAY`, `DQS`, `DDRDLL`, SSTL15 IO | none for GW5AST |
| DVI TX | encrypted | `OSER10`, TLVDS | OSER10, PLLA, TLVDS/ELVDS already work on the Console |
| Core fabric | Spinal Verilog | LUT, DFF, BSRAM, DSP | supported (BSRAM, DSP as `D5A`) |

Second blocker: the Pro Dock is the FCPBGA676A package. apicula's only
GW5AST database was built from Gowin 1.9.10.03 data, where GW5AST-138C
exists only as PBGA484A (the Console). The Pro's chip reports JTAG idcode
`0x1081b`, the same value apicula's 138C bitstream header carries, so it is
the same GW5AST-138 die. Gowin 1.9.12.04 lists GW5AST-138C in FCPBGA676A,
so the package can be added by rebuilding the 138C database against that
tree instead of starting a 138B database (70 device-name checks in apicula
are keyed on `GW5AST-138C`).

Nothing from the open flow has been confirmed on hardware yet. The Console
demos build; no `-open.fs` has a recorded board test.

## Steps, in order

1. **Pro Dock package in the open flow.** Rebuild `GW5AST-138C.msgpack.xz`
   with `GOWINHOME=/home/hyper/gowin/standard-1.9.12.04` (apicula branch
   `gw5ast-138c-fpg676`), regenerate the nextpnr chipdb from it with
   `gowin_arch_gen.py` + `bbasm`, then `make BOARD=pro CHIPDB=... blinky_pro`
   here. Flash and watch the four LEDs. This is the first hardware proof
   for the open flow on this board.
2. **Core without PCIe or DDR.** Build `TangConsole138k`-style fabric
   (demo AXI-Lite player, no DDR, HDMI out) for the Pro Dock. Exercises
   BSRAM, DSP, PLLA, OSER10 at the Voodoo core's size. Expect routing and
   timing work in nextpnr; the vendor build already needed the shrunk
   `ext/SpinalVoodoo`.
3. **DDR3.** Hardest block to replace. Options: (a) soft PHY on plain
   IOLOGIC (OSER8/IDES8 already exist for GW5A in apicula; `DQS` and
   `DDRDLL` would need fusing) using LiteDRAM; (b) fuzz the Gowin DDR3
   primitives against `ddr3_test/` builds, which are small (vendor PnR in
   1m26s) and already pass BIST on hardware. (b) is the realistic path.
4. **PCIe.** The controller is a hard macro configured through UPAR
   register writes (`serdes.csr`), so the encrypted `pcie_controller.v` is
   likely a thin wrapper. Needed: `GTR12_QUAD/UPAR/PMAC` placement and
   fuses (fuzz with vendor builds of `serdes.v` alone), the TLP user
   interface port map (from `serdes.v`, which is plain), and the CSR
   write sequence (already in the tree). The decompiled `libgowin.so`
   fusemap writer and the `Gtrl12*` tables in the `.dat` are the inputs for
   the fuzzing harness.

Steps 3 and 4 are reverse-engineering projects measured in weeks each, not
build fixes. Step 1 is hours, step 2 is days.

## Build times, vendor vs open

Vendor Gowin EDA (1.9.11.03 Education, this repo's logs):

| Design | Synthesis | Place | Route | Total PnR |
| --- | --- | --- | --- | --- |
| Full `board_top` (shrunk) | 3m52s | 5m26s | 15m04s | 20m34s |
| `ddr3_test` | 12s | 32s | 49s | 1m26s |
| `vendor-jtag-mailbox` | 0.1s | | | 5s |
| Congested earlier runs | | | 2h22m, or gave up at ~2.5h | |

Open flow (yosys + nextpnr-himbaechel + gowin_pack, hyper-ai, 8 threads):

| Design | End to end |
| --- | --- |
| Console `blinky` | 14s |

No Voodoo-sized design has been through nextpnr yet, so there is no
like-for-like number. Expect nextpnr placement and routing for a design
that fills 45% of LUTs and 81% of CLS to take longer than the vendor's
20 minutes, not shorter; nextpnr's himbaechel router is single-threaded
for the routing phase and the GW5AST database is large (64 MB).

## Working notes

- Vendor PnR and bitstream are single-threaded by construction:
  `libgowin.so` does not link libpthread. Only synthesis (`libgwsyn.so`,
  ABC/Verific) uses threads. Run independent builds concurrently instead.
- `make BOARD=pro` writes to `build-pro/` so Console artifacts in `build/`
  are untouched.
- `gowin_pack` must run with the fork's apycula on `PYTHONPATH`, not the
  oss-cad-suite copy, or the Console-only database is used.
