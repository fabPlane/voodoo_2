# Voodoo 2 card, modular version: carrier plus FPGA module

Status: proposal, 2026-09-16. Companion to `voodoo2-tang-mega-138k-carrier.md`
(the Sipeed module on a carrier) and `voodoo2-artix-card-plan.html` (the
monolithic Artix card, the baseline). Prices are JLCPCB library prices where the
part is in the library, LCSC otherwise, September 2026.

## 1. The idea

Split the card in two:

- **The module** carries everything expensive and hard to route: the FPGA on
  its 484-ball BGA, the DDR3, the four HyperRAM chips, the Linux companion, the
  regulators, configuration flash and oscillator. Eight layers, small.
- **The carrier** carries everything bus- and connector-specific: the PCI edge
  and its 5 V bus switches, the VGA relay and DAC, the connectors, expansion
  headers, one 3.3 V buck. Four layers, no BGA, cheap to respin.

The module's connector layout is **Sipeed's Tang Mega 138K SoM layout**: three
Hirose DF40 connectors, 100 + 100 + 80 pins, at one end of a 35 mm wide board.
Sipeed publishes the pin map. Our own Artix module puts the same functions on
the same pins. One carrier then accepts either the $69 Gowin module or our
Artix module, and the two hedge each other's supply risk.

What modularity buys:

- One BGA and DDR3 layout, done once, reused by every carrier: PCI now, ISA,
  X68000, AGP and PCIe later.
- Carriers are 4-layer boards a week apart, not 6-layer BGA boards a month apart.
- Rework means swapping a module, not reflowing a BGA.
- The Gowin module works on the same carrier.

What it costs: about $70 more per card at qty 100 than the monolithic board,
and about $110 more at qty 5, for the second PCB, its assembly setup, 280 pins
of connectors and the duplicated passives. Section 6 has the numbers. This is
the honest trade: modularity is a bet that more than one carrier will exist.

## 2. The module standard

Adopted from the Sipeed SoM schematic, revision 30354, with the function groups
fixed so that any conforming module works on any conforming carrier.

### Mechanical

| Item | Value |
|---|---|
| Width | 35 mm, fixed |
| Length | 45 mm (Sipeed) up to 70 mm (ours). Connectors at the same end; the carrier keeps a 70 mm keep-out |
| Connectors, module side | 2x DF40C-100DP-0.4V(51), 1x DF40C-80DP-0.4V(51), positions as on the Sipeed SoM |
| Connectors, carrier side | 2x DF40HC(3.0)-100DS-0.4V(51), 1x DF40HC(3.0)-80DS-0.4V(51) |
| Stack height | 3.0 mm |
| Max component height on module | 6 mm top, 1.5 mm bottom, so module plus carrier stays inside PCI's 14.5 mm |
| Mounting | four holes matching Sipeed's M1 to M4; ours adds two more at the far end of a 70 mm module |

### Electrical

| Group | Pins | Direction and rule |
|---|---:|---|
| 5V_IN | 8 | Carrier to module. The module makes all its own rails. Budget 10 W |
| GND | 60 | |
| VCCIO_A (Sipeed VCCIO2) | 2 | Carrier to module. Sets the voltage of the PCI bank. 3.3 V on the PCI carrier |
| VCCIO_B (Sipeed VCCIO67) | 2 | Carrier to module. Second selectable bank, 1.8 V or 3.3 V |
| PCI bank | 52 | The 50 PCI signals plus two spares, all on the VCCIO_A bank. CLK on a clock-capable pin |
| Video bank | 36 | 24 DAC data, DAC clock, blank, sync, 2 relay drives, HS/VS sense, LEDs, 5 spare. 3.3 V fixed on the module |
| TMDS | 8 | Four differential pairs on an LVDS-capable bank |
| HyperRAM bank | 49 | Only used by modules that have no HyperRAM on board (the Gowin module). Our module leaves these unconnected, and the carrier keeps four HyperRAM footprints for the Gowin case |
| Capture ADC | 30 | 24 data, clock, HS, VS, DE, I2C. Reserved, unpopulated on v1 |
| Companion I/O | 22 | UART console (2), RMII to a carrier PHY (9), PHY MDIO and reset (3), SD (6), USB D+/D- (2) |
| Expansion | 40 | Serial, MIDI, Pmods, as the carrier provides |
| Config and debug | 10 | JTAG (4), DONE, PROGRAM/RECONFIG, INIT/READY, MODE strap sense, 2 spare |
| SerDes | 18 | Quad Q0: 4 TX pairs, 4 RX pairs, refclk pair. For the PCIe carrier |
| Total | 337 signals requested against 280 pins | |

The request exceeds the connector by about 57 pins, so groups are prioritised:
the PCI, video, TMDS, HyperRAM, config, companion and SerDes groups are fixed
(217 pins plus 72 power and ground, 289). The ADC and expansion groups share
what remains, about 30 pins on the Gowin module and about 80 on ours, because
our module does not use the HyperRAM group. The exact allocation is done from
a pin spreadsheet built from the Sipeed schematic, which is the first task of
the layout.

### Rules that keep modules interchangeable

- Every PCI signal is on the VCCIO_A bank on every module.
- JTAG, DONE and PROGRAM are on the same connector pins on every module so the
  carrier's JTAG header works for both.
- The SerDes quad uses the same pin positions; AC coupling capacitors on the
  module TX side per PCIe.
- Companion UART and RMII are on the same pins so one carrier PHY and console
  header serve both.
- A module may leave a group unconnected but may never repurpose it.

## 3. The PCI carrier

The carrier is the current rev A/B card with the FPGA half cut out.

| Block | Parts |
|---|---|
| PCI | edge fingers, 3x SN74CBTD16211 bus switches (CBT16245 fallback), OE from a carrier strap |
| Module | 3x DF40HC receptacles, 5 V filter, 3.3 V buck (TPS54318) also feeding VCCIO_A |
| Video | ADV7123 DAC, 3x Omron G6K relays for fail-safe passthrough, 2x Kinghelm KH-HDR15P VGA, HDMI connector with ESD and 5 V protection |
| HyperRAM sites | 4x ISSI IS66WVH8M8BLL, populated only when a Gowin module is fitted |
| Companion peripherals | RTL8201F PHY and magjack, USB-A, SD slot, UART header |
| Capture | TVP7002 footprint, unpopulated |
| Expansion | 2x5 serial headers, 2x13 parallel, 2x8 game/MIDI, 2x Pmod, with transceivers |
| Board | 4-layer, 175 x 107 mm, hard gold fingers, 30 degree bevel |

Carrier cost: about $67 at qty 100, about $95 at qty 5, without HyperRAM.
Add $24 if the HyperRAM sites are populated for a Gowin module.

## 4. Our module: VM-A7, Artix-7 200T or 100T

One layout, two BOMs. The XC7A100T-2FGG484 and XC7A200T-2FBG484 are
ball-for-ball identical on the 484 package (AMD DS180 note 2, confirmed by
diffing AMD's package files), so the module is populated with the 100T for a
Voodoo 1 product and the 200T for Voodoo 2.

### Contents

| Block | Part | Notes |
|---|---|---|
| FPGA | XC7A100T-2FGG484I or XC7A200T-2FBG484I | 100T: 63.4K LUT6, 4.9 Mbit BRAM, Voodoo 1 with depth in HyperRAM. 200T: 134.6K LUT6, 13.1 Mbit BRAM, Voodoo 2 with depth on chip |
| DDR3 | 2x Winbond W632GU6NB-11, 2 Gbit x16, 32-bit, 512 MB | MIG, 800 to 1066 MT/s. Samsung 4 Gbit alternate on the same footprint, route A14 |
| HyperRAM | 4x ISSI IS66WVH8M8BLL-100B1LI | colour buffers, and depth on the 100T. MJoergen controller. 3.0 V parts on a 3.3 V bank |
| Companion | Allwinner T113-S3, GD5F1GQ5 SPI NAND, 24 MHz crystal, its rails | Linux, host-less self-test, updates. UART, RMII, USB and SD go to the connector |
| FPGA to T113 link | SPI plus the 24-bit LCD-port pipe | on-module, no connector pins |
| Configuration | W25Q128 QSPI, MultiBoot golden image | no microcontroller anywhere. JTAG on the connector |
| Clock | 50 MHz oscillator | MIG's 200 MHz from the MMCM |
| Power | TPS62480 1.0 V core, TPS54318 x3 for 1.8 V, 1.5 V and 3.3 V, TPS51200 VTT, T113 regulators, sequencing | from the carrier's 5 V |
| Connectors | 2x DF40C-100DP, 1x DF40C-80DP | Sipeed positions |
| Board | 8-layer, 35 x 70 mm | BGA fanout, DDR3 length matching, HyperRAM within 25 mm of the FPGA |

### Bank plan

| Artix bank | Voltage | Connector group |
|---|---|---|
| Bank 14 (HR, 50 I/O) | VCCIO_A from carrier, 3.3 V | PCI |
| Bank 15 (HR, 50 I/O) | 3.3 V fixed | HyperRAM channels 0 and 1 (on-module) |
| Bank 16 (HR, 50 I/O) | 3.3 V fixed | HyperRAM channels 2 and 3 (on-module) |
| Bank 13 (HR, 50 I/O) | 3.3 V fixed | video bank, TMDS_33 pairs, relay, LEDs |
| Banks 34 and 35 (HP-like, 50 each) | 1.5 V | DDR3 (on-module) |
| Bank 0 | 3.3 V | configuration, JTAG to connector |
| GTP quad 216 | | SerDes Q0 to connector |
| Remaining HR pins | VCCIO_B from carrier | capture ADC, expansion, companion I/O |

The 484 package exposes 285 I/O. On-module use is about 130 (DDR3 70,
HyperRAM 49, QSPI 6, misc 5). About 155 reach the connector, plus the GTP quad,
which covers the fixed groups and most of the expansion set.

### Module cost

| Item | Qty 5, each | Qty 100, each |
|---|---:|---:|
| XC7A100T-2FGG484I (JLC C410276) | $64 | $60 |
| or XC7A200T-2FBG484I (JLC C494680) | $136 | $110 |
| 2x DDR3L W632GU6NB | $14 | $12 |
| 4x HyperRAM ISSI at $6.59 | $26 | $24 |
| T113-S3, SPI NAND, crystal, rails | $38 | $35 |
| Regulators and sequencing | $12 | $10 |
| QSPI, oscillator | $4 | $4 |
| 3x DF40C plugs | $9 | $8 |
| Passives | $15 | $12 |
| PCB, 8-layer, 35 x 70 mm | $40 | $12 |
| Assembly, BGA | $45 | $15 |
| Module with 100T | about $267 | about $192 |
| Module with 200T | about $339 | about $242 |

### Card cost, module plus PCI carrier

| | Qty 5 | Qty 100 | Monolithic card, same FPGA |
|---|---:|---:|---:|
| 100T module + carrier | about $360 | about $260 | $220 to $230 / $190 |
| 200T module + carrier | about $435 | about $310 | $275 to $285 / $245 |
| Gowin module + carrier with HyperRAM | about $205 | about $160 | n/a |

## 5. When the modular version is the right one

- More than one carrier is planned. Each extra carrier costs a 4-layer board
  and a week, against a full 6-layer BGA design per bus. Two carriers roughly
  pay back the module premium; three clearly do.
- The Gowin pairing is wanted. The Sipeed module on the same carrier is the
  cheapest complete card and hedges Xilinx grey-channel supply.
- Prototyping. Reworking a $95 carrier beats reworking a $280 board.

When it is not: a single PCI product at the lowest price, which is the
monolithic card.

## 6. Where the $70 goes

| Item | Per card at qty 100 |
|---|---:|
| Second PCB and its assembly setup | $27 |
| 280 pins of DF40 connectors, both sides | $16 |
| Duplicated passives, filtering, a second 3.3 V rail | $12 |
| 8-layer premium on the module against 6-layer monolithic | $10 |
| Test and handling of two boards | $5 |

## 7. Risks and open questions

- **Pin budget.** 337 requested against 280. Resolved by the priority list in
  section 2, but the exact split waits on the pin spreadsheet.
- **TMDS on a 3.3 V HR bank.** Artix-7 supports TMDS_33 on HR banks; confirm
  drive with the HDMI connector's ESD network, or add a redriver footprint.
- **VCCIO_A at 3.3 V for PCI on both modules.** Gowin bank 2 at 3.3 V is
  standard; confirm Artix bank 14 has enough clock-capable pins for PCI CLK.
- **Module heat.** A 200T at 90 MHz with 70K LUTs busy is 3 to 4 W on a 35 mm
  board. Plan a heatsink on the FPGA and check it clears the carrier's DAC.
- **Sipeed pinout changes.** Sipeed revises the SoM (30353 to 30354 in 2026).
  Pin the standard to a revision and keep the schematic in the repo.
- **DDR3 on 8 layers at 35 mm wide.** Two x16 chips beside a 23 mm BGA on a
  35 mm board is tight; the 70 mm length is what makes it fit. Confirm with a
  placement study before schematic capture.

## 8. Recommendation

Do the Gowin synthesis trial first, as the other documents say. Then:

- If Gowin fits, build the carrier and use Sipeed's module for the first
  hardware. Design the Artix module only when a second carrier or a Voodoo 2
  BRAM budget demands it.
- If Gowin does not fit, the choice is the monolithic 200T card at $245 or the
  Artix module at $310 with the reuse benefits. That is a product decision:
  one card, or a family.
