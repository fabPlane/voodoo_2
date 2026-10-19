# own_pcie: replacing Gowin's encrypted PCIe soft blocks

The PCIe endpoint is hard silicon: `GTR12_PMAC` (a PLDA-style MAC, TL
interface), `GTR12_QUAD` (SerDes) and `GTR12_UPAR` (their register bus).
Gowin's generated `ip/PCIE_Controller_Top/serdes.v` (plain Verilog)
instantiates those plus two encrypted soft blocks:

| file | what it is | replacement |
|---|---|---|
| `pcie_controller.v` (`PCIE_Controller_Top`) | glue between user TL ports and the PMAC: reset, credits split, LTSSM/link status, constant PMAC inputs, DRP to the arbiter; also watches PIPE TX/RX data | `rtl/own_pcie_ctrl.v` |
| `upar_arbiter.v` (`upar_arbiter_wrap`) | 8 DRP ports onto the one UPAR bus, UPAR reset | `rtl/own_upar_arbiter.v` |

The SerDes register settings are not in either blob: they are the plain
`serdes.csr`, applied by the Gowin tools (`set_csr`). The PMAC/QUAD
primitives and `serdes.v` stay.

`tools/gen_serdes.py` rewrites Gowin's `serdes.v`:

- `--own`: the two instances become `own_pcie_ctrl` / `own_upar_arbiter`;
  module name and ports stay, so `board_top.v` is unchanged.
- `--probe`: adds `rtl/own_pcie_probe.v` on a new port
  `own_pcie_probe_o`; `board_top.v` with `` `define OWN_PCIE_PROBE `` puts it
  on JTAG diag pages 8..103 (`src/jtag_diag.v`), read with
  `tools/pcie_probe.py`.

## What is guessed

The hard blocks have empty simulation models, so the sim only shows the
replacement does what I think it should. Marked "unverified" in the RTL:

- `TL_CLOCK_FREQ` value (assumed kHz; the IP was generated for a 100 MHz
  TLP clock, the board runs it at 75 MHz)
- credit order in `TL_TX_CREDITS0` (assumed p, np, cpl from bit 0)
- LTSSM = `FABRIC_TEST_BUS_MON[4:0]`, link up = LTSSM 0x11
- `TL_TX_PROT0`, `TL_BRSW_IN`, `TL_PM_OBFFCONTROL` = 0
- PMAC reset: PERST# + fixed delay
- TL RX/TX straight through, no pipeline stage
- the controller issues no DRP accesses of its own
- UPAR handshake (hold request until ready, read data with rdvld),
  `upar_bus_width` = 0, UPAR reset at power-up only

## Plan

1. Probe build (vendor blocks in charge, probe watching them):
   `fpga/hw/gowin-ownpcie-probe` (B126a + own_ddr3 + probe). Flash, reboot,
   run `pcie_probe.py`, set the guesses from what it shows. The probe reports,
   per output, whether it equals an input delayed 0..3 cycles, every constant,
   the PERST# to PMAC-reset and link-up times, and logs the first 16 UPAR
   accesses and the LTSSM history.
2. Own build: `fpga/hw/gowin-ownpcie` (`--own --probe`), same probe on
   `own_pcie_ctrl`, so the two reports can be compared directly.
3. GLQuake soak on the own build.

## Sim

    cd sim && make SERDES=/path/to/ip/PCIE_Controller_Top/serdes.v

Runs `tb.v` twice (own blocks, then own blocks + probe) against the
behavioural `GTR12_*` models in `sim/models/`: reset release, link up,
credits, RX/TX passthrough under random backpressure, DRP to UPAR and
back, PERST# again; the second run also checks the probe's counters and logs.
