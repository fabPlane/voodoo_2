# Voodoo 2 Artix card — next-pass design notes

This first board is a mechanical and placement feasibility study only. It is
useful for fabrication quoting and for exposing floorplanning pressure, but it
has no complete schematic, verified pinout, power design, fan-out, or routing.
Do not order it as an assembled board.

## Rev A pricing population — 2026-09-16

The new populated quote model is enough to begin the real schematic and
floorplanning pass, but it is not yet a production BOM or a routed board.
JLCPCB parsed all **45 BOM groups and 326 top-side placements** without BOM/CPL
mapping errors.

- JLC reports **33 groups matched/confirmed**. One is an arbitrary red-LED
  suggestion that remains deselected; exact intended in-stock selections cover
  **32 groups / 281 placements**.
- **3 groups / 5 placements are exact-part matches with inventory shortages:**
  the two EDAC `634-015-363-543` VGA connectors and three TI
  `SN74CB3T16210DGGR` PCI bus switches. Both are replaced with stocked parts in
  the BOM revision below.
- **9 unmatched groups / 35 placements remain intentionally provisional:** input filter
  L1; feedback/strap resistors R121-R144; HDMI ESD U31/U32; VGA ESD U33;
  auxiliary inductors L22-L24; fuse F1; input load switch Q10; JTAG J8; and
  manufacturing header J9. A tenth unresolved group contains five 0603 status
  LEDs, which are not selected because colour and current have not been assigned.
- Exact LCSC-number coverage is **286/326 placements (87.7%)**. Current JLC
  inventory coverage is **281/326 placements (86.2%)**.

The live partial quantity-5 quote is **$1,360.84 total / $272.17 per card**
before shipping and taxes: $66.04 fabrication plus $1,294.80 Standard PCBA.
This excludes all shortage and provisional groups, so the finished-board price
will rise. The FPGA is the dominant line item at $596.73 for five, followed by
DDR3L, ISSI HyperRAM, and the T113-S3.

For the next pass, resolve the two shortage families first because they affect
mechanical layout and the PCI interface. Then calculate each power rail so the
three auxiliary inductors, input filter, load switch, fuse, and 24 feedback or
strap resistors can be converted from placement allowances into real values.
Only after that should the protection arrays and headers be frozen.

## BOM revision — 2026-09-16, rev A on the XC7A100T with stocked cheaper parts

The $272 partial quote was reconciled line by line against live JLCPCB
catalogue prices. Fabrication ($13.20 per card) and assembly ($21.60 per card)
were cheaper than planned. The overrun was four component lines whose plan
prices were stale: the FPGA, HyperRAM (planned at the retired $1.92 figure;
the ISSI part at $6.59 is already the cheapest HyperRAM JLC stocks), the EDAC
VGA connectors ($10.32 each, zero stock) and DDR3L (the availability workbook
carried $2.65; JLC charges $15.80). Four BOM lines change:

| Function | Was | Now | JLC | Qty-5 price | Stock | Per card |
|---|---|---|---|---|---|---|
| FPGA (rev A) | XC7A200T-2FBG484I `C494680`, $119.35 | XC7A100T-2FGG484I | `C410276` | $63.75 | 1,405 | −$55.60 |
| DDR3L, 32-bit bus | 2× K4B4G1646E-BYMA `C500275`, $15.80 | 2× Winbond W632GU6NB-11, 2 Gbit x16 | `C3034991` | $11.10 | 1,418 | −$9.39 |
| PCI bus switches | 3× SN74CB3T16210DGGR `C2674635`, $4.51, 1 in stock | 3× SN74CBTD16211DGGR | `C2674790` | $3.00 | 15 | −$4.52 |
| VGA connectors | 2× EDAC 634-015-363-543 `C3099918`, $10.32, 0 in stock | 2× Kinghelm KH-HDR15P-F3.08 | `C3038673` | $0.28 | 310 | −$20.08 |

Rules attached to each swap:

- **FPGA.** The XC7A200T-2FBG484I stays in the plan as the Voodoo 2 part; rev A
  boards carry the 100T for bring-up and Voodoo 1. AMD DS180 Table 3 note 2
  states that devices in FGG484 and FBG484 are footprint compatible, and a
  ball-by-ball diff of AMD's `xc7a100tfgg484pkg.txt` and
  `xc7a200tfbg484pkg.txt` files on 2026-09-16 shows all 484 balls identical in
  pin name, bank, memory byte group and I/O type: 285 HR I/O in banks 13-16,
  34 and 35; 4 GTP in bank 216; 21 configuration pins. The same PCB, XDC and
  MIG pinout take either device, so this is a BOM line, not a board revision.
  Design the 1.0 V rail, decoupling (UG483) and heatsink clearance for the
  200T; keep the 128 Mbit QSPI because the 200T bitstream is 77 Mbit. The 100T
  exposes six clock tiles to the bonded banks against ten on the 200T, so
  budget MMCMs on the 100T. What the 100T loses is inside the chip: 63.4K
  LUT6 and 4.9 Mbit BRAM, so no Voodoo 2 and no on-chip depth buffer.
- **DDR3L.** All x16 DDR3 in the 96-ball FBGA share the JEDEC ballout; the
  4 Gbit part adds A14, which is no-connect on 2 Gbit. Route A14 and draw the
  courtyard for the largest body (8 × 14 mm) so the footprint accepts 1, 2 or
  4 Gbit from Winbond, Samsung, ISSI or Micron with only a MIG parameter
  change. Two devices keep the 32-bit bus the fill-rate plan assumes; 512 MB is
  far beyond any Voodoo 2 need. `K4B4G1646E-BYMA` remains the 1 GB alternate.
  1 Gbit parts are no cheaper at JLC (Micron MT41K64M16TW-107:J is $9.49).
  Populating only U8 is also possible on the same layout: it gives a 16-bit
  bus, and the fly-by address/command/clock routing must run past both sites
  to the VTT termination so an empty site is trace, not a stub.
- **Bus switches.** SN74CBTD16211 is the 24-bit part the plan originally named,
  with the internal clamp diode that keeps 5 V PCI levels inside the Artix-7
  HR-bank limits. It is TSSOP-56, not the TSSOP-48 of the CB3T16210. JLC stock
  is exactly 15, which may not clear attrition for five boards;
  SN74CBT16245CDLR (`C2674625`, $1.09, 562 in stock, 16-bit) is the fallback
  only with a reduced VCC or external clamp, since the plain CBT has no diode.
  The 5 V PCI audit in item 4 applies to whichever is used.
- **VGA connectors.** Kinghelm KH-HDR15P-F3.08 is a standard HD15 female
  right-angle, -55 to +105 °C, 5 A. HOAUC HYC06-HDR15B-060 (`C711364`, $0.21,
  2,525 in stock) is the alternate. Take the board-edge datum and pin offset
  from the Kinghelm drawing before freezing the bracket.

Estimated effect at quantity 5 from live JLC prices: the quoted 30 groups drop
from $237 to about $172 of components per card, and the four groups that were
missing from the quote are now in stock and add about $10. With the quoted
fabrication and assembly charges unchanged, the complete card is about $220
with the 100T or about $275 with the 200T. Re-upload the regenerated BOM and
CPL to confirm; the $272 quote is for the previous BOM.

## Highest-priority changes before a real schematic/layout pass

1. **Verify the PCI edge geometry from the governing PCI mechanical drawing.**
   The rough board uses an explicitly preliminary 1.27 mm pitch finger bank and
   key. Confirm the datum, finger count/length, 5 V key position, bracket datum,
   retention dimensions, keep-outs, 30° bevel depth, and finished 1.6 mm edge
   thickness before ordering even a mechanical sample.

2. **Move from “parts named” to “parts selected.”** Lock manufacturer part
   numbers and datasheets for the two DDR3L devices, HyperRAM, regulators, VGA
   relay, HDMI connector, USB connector, Ethernet PHY/magnetics, SPI NAND, QSPI,
   oscillators, and bracket. Several package assumptions in the placement study
   are generic placeholders.

3. **Prove the FPGA bank plan before routing.** Assign PCI, four HyperRAM channels,
   DDR3, DAC RGB, TMDS, T113 link, ADC-reserved pins, clocks, configuration, and
   debug by bank. Check I/O standards, VCCO compatibility, clock-capable pins,
   DCI/reference needs, simultaneous switching limits, and configuration-bank
   voltage. This can invalidate the placement if delayed.

4. **Audit the selected bus-switch approach for 5 V PCI.** Confirm the exact
   SN74CB3T16210 topology against PCI clamp-current, input threshold, output high,
   turn-around, hot-plug/power-off behavior, and 33 MHz timing. A purpose-built
   5 V-tolerant PCI bridge or a CPLD/FPGA with a formally supported interface may
   reduce electrical risk, even if it raises BOM cost.

5. **Start with a power tree and sequencing simulation.** Derive current budgets
   from Artix Power Estimator and the T113 reference design. Confirm the slot's
   +5 V limit, inrush, reset behavior, rail sequencing, soft-start, power-good,
   discharge, and thermal margins. Put switchers near the PCI +5 V entry but keep
   their hot loops away from VGA, HDMI, oscillators, and FPGA clock inputs.

6. **Copy the T113-S3 subsystem from a known-good open reference design.** Match
   the PMIC/regulator scheme, boot straps, reset, crystal, SPI NAND, Ethernet
   PHY, USB protection, DDR-in-package requirements, and Linux-supported pinmux.
   Verify that the intended reference design is legally reusable.

7. **Treat DDR3 as a dedicated placement exercise.** Place the two x16 memories
   as a symmetric 32-bit interface close to one FPGA bank edge. Obtain the chosen
   JLCPCB six-layer impedance stack-up first, then set byte-lane topology,
   fly-by/address routing, termination, via strategy, length/skew constraints,
   reference planes, and VTT/VREF decoupling from MIG guidance.

8. **Validate whether six layers are enough.** With a 484-ball BGA, 32-bit DDR3,
   four HyperRAM buses, PCI, HDMI, and mixed-signal VGA, eight layers may materially
   lower fan-out and return-path risk. Quote both 6-layer and 8-layer builds
   before investing in routing; the cost delta may be small relative to the FPGA.

9. **Separate analog video from digital and switch-mode power.** Keep the DAC,
   relay, VGA connectors, terminations, and optional ADC at the bracket side with
   a clean reference plane. Add explicit ESD, 75 Ω source/load networks, input
   protection, relay fail-safe state, and controlled return paths. Verify that
   the unpopulated ADC network presents negligible stub capacitance.

10. **Budget HDMI implementation details now.** Confirm Artix-7 TMDS electrical
    compatibility, connector ESD, DDC/HPD level shifting, 5 V source protection,
    and whether external redrivers are needed. Route the four differential pairs
    on a single layer over an uninterrupted plane.

11. **Add bring-up hooks.** Include JTAG, UART, FPGA DONE/INIT/PROGRAM, T113 boot
    recovery, rail current measurement links, test points for every supply,
    clock test pads, relay override, safe PCI reset isolation, and a manufacturing
    test header. Make the default state passive VGA passthrough with all active
    devices unconfigured.

12. **Plan thermal and mechanical validation.** Estimate FPGA junction
    temperature at the stated 7–9 W board budget inside a poorly ventilated ATX
    case. Reserve heatsink keep-out and mounting, check component height against
    the adjacent PCI slot, and model bracket/connector alignment before ordering.

## Suggested next deliverables

- A fully annotated block-level schematic with chosen manufacturer part numbers.
- FPGA bank/pin spreadsheet and rail-current budget reviewed before PCB work.
- Two placement variants: six-layer cost target and eight-layer risk-reduction.
- JLCPCB-approved stack-up and impedance table attached to the KiCad project.
- A PCI mechanical test coupon/card before the expensive FPGA assembly run.
- A staged assembly plan: power-only board, FPGA configuration board, PCI target,
  VGA passthrough/DAC, memory, then T113 and optional capture ADC.

## JLCPCB feasibility/quote check — 2026-09-16

JLCPCB accepted `voodoo2_artix_rough_gerbers.zip` and automatically detected a
**6-layer, 175 × 107 mm** board. This is useful confirmation that the outline,
layer naming, and manufacturing archive are readable. It is not an electrical
or assembly approval.

Quote configuration used:

- Quantity: 5 bare boards
- FR-4, green solder mask, white silkscreen
- Nominal thickness: 1.6 mm
- Outer copper: 1 oz; inner copper: 0.5 oz
- Gold fingers: yes
- Edge bevel: 30° (JLC displayed 1.13 mm bevel depth and 0.53 mm residual edge)
- Selected stack-up: `JLC06161H-3313`
- JLC states the selected stack-up's actual thickness is 1.5384 mm, ±10%
- Standard lead time shown: 8–9 days

Live website pricing shown before tax, VAT, duty, and possible engineering
adjustments:

| Configuration | Fabrication | Shipping estimate | Breakdown |
|---|---:|---:|---|
| Six layer, stack-up selected, no controlled impedance | $45.00 | $9.49 | $33 engineering + $12 boards |
| Same, with ±10% impedance control | $78.88 | $9.49 | adds $32.84 impedance control + $1.04 production-file confirmation |

The controlled-impedance option requires a table identifying signal layer,
reference layer, impedance type, width/spacing, target impedance, and trace
location. JLC asks for that table as JPG or Excel inside the Gerber archive and
will adjust widths during engineering. Add this only after the FPGA bank plan,
DDR topology, HDMI routing, and actual JLC stack-up are frozen.

### Assembly feasibility check

The same job was also taken through JLCPCB's **Standard PCBA**, top-side-only
workflow at quantity 5. JLC added two 5 mm assembly rails, changing the process
panel from 175 × 107 mm to **175 × 117 mm**. With the impedance option retained,
the PCB portion changed slightly to **$80.08 total / $16.02 per board** before
shipping, tax, VAT, duty, and engineering adjustments.

Preliminary BOM and CPL files were generated from the placement study and
uploaded. JLC successfully parsed **26 BOM line groups** and all corresponding
top-side positions, which confirms that the upload format and one-side assembly
workflow are accepted. It selected Standard PCBA as expected for this job.

All 26 line groups remain **unmatched** because the board intentionally uses
placeholder footprints and does not yet contain locked manufacturer/LCSC part
numbers. JLC will not advance to an exact assembly quote until at least one real
catalog part is selected. Selecting superficially similar parts just to produce
a number would make the result misleading, especially for the FPGA, BGA memory,
power tree, connectors, and through-hole work. No parts were selected and the
job was not added to the cart.

JLC's published Standard PCBA fee schedule on 2026-09-16 indicates a $25.56
single-side setup fee, $8.21 stencil, $1.53 feeder-loading charge per selected
part type, joint-based SMT/manual labor, BGA/QFN X-ray charges, and a $16.42
two-fixture charge for 1–29 rigid boards. For this 26-line prototype, those
known fixed fees are about **$90/order ($18/board)** before joint counts, X-ray,
manual connector work, packing, handling, and components. A reasonable
feasibility allowance remains **$25–45 per board for assembly service**.

Using the handoff document's approximately **$213/card qty-5 component budget**,
the live $16.02 PCB price, and that assembly allowance gives a provisional
**$254–274/card** direct build cost. Carry **$260–300/card**, or **$1,300–1,500
for five**, until the BOM and footprints are locked; shipping, VAT, duty,
consigned/grey-channel FPGA logistics, and engineering changes are additional.

Before requesting the next quote, replace every `Rough:*` footprint, add exact
MPNs and LCSC numbers, split regulator blocks into controllers/inductors/passives,
include the roughly 150 decoupling and termination parts omitted from this
placement-only BOM, and decide which connectors require wave/manual soldering.

## JLC/LCSC parts availability — 2026-09-16

The original JLC upload parsed 26 placeholder groups. After removing two
PCB-only test-point entries and adding the two-phase core inductors as one
purchased group, the current denominator is **25 purchased BOM groups**. This
is not yet a complete production BOM: the passive network, protection,
termination, regulator support components, heatsink, and bracket are still
absent.

- **18 of 25 purchased groups (72.0%)** now have a concrete LCSC part number
  with stock observed during this check. PCB-only test pads are no longer
  counted as purchased parts.
- **3 additional groups** have concrete replacement candidates, bringing
  potential coverage to **21 of 25 (84.0%)**, but they still need electrical or
  mechanical verification.
- The four groups outside those totals are the three auxiliary buck designs and
  the PCI input filter. Their controller part exists, but the rail-current,
  magnetics, inrush and sequencing work must be completed before release.

JLC's own assembly library explicitly lists the selected FPGA, T113-S3, Samsung
DDR3L, and TPS51200 as PCBA parts. The FPGA is an Extended/pre-order item; the
T113-S3 and DDR3L entries support Standard PCBA, and the DDR3L entry requires
X-ray inspection. This verifies that the major BGA/LQFP choices are serviceable
in principle, although real-time allocation still needs to be made when the
footprints and BOM are final.

Concrete stocked selections or replacements identified:

| Function | Proposed part | LCSC | Availability note |
|---|---|---|---|
| FPGA, rev A | XC7A100T-2FGG484I | C410276 | In stock; pin-identical to XC7A200T-2FBG484I (C494680), the Voodoo 2 alternate |
| Companion CPU | T113-S3 | C5197687 | In stock |
| SPI NAND | GD5F1GQ5UEYIGR | C2829047 | In stock; verify U-Boot/Linux ECC support |
| Ethernet PHY | RTL8201F-VB-CG | C45044 | In stock; use RMII and check clock/strap plan |
| Video DAC | ADV7123KSTZ50 | C514372 | In stock, but lower stock than the other major ICs |
| DDR3L x16 | 2× W632GU6NB-11 (2 Gbit) | C3034991 | In stock; K4B4G1646E-BYMA (C500275) is the 1 GB alternate on the same footprint; route A14 |
| QSPI configuration flash | W25Q128JVSIQ | C97521 | In stock |
| HyperRAM | IS66WVH8M8BLL-100B1LI | C1349096 | Locked primary; 64 Mbit, 3.0 V, 100 MHz, TFBGA-24 |
| DDR VTT | TPS51200DRCR | C34771 | In stock; confirm sink/source-current budget |
| FPGA clock | ASE-50.000MHZ-LC-T | C596955 | In stock; confirm jitter and clock-bank voltage |
| T113 crystal | X1E0000210129 | C255917 | In stock; calculate external load capacitors |
| FPGA core buck | TPS62480RNCR | C2071434 | In stock; active 6 A dual-phase production candidate |
| Core-buck inductors | 2× DFE252012F-R47M=P2 | C703140 | In stock; validate ripple and thermal margin |
| UART connector | SM04B-SRSS-TB(LF)(SN) | C160404 | In stock; SMT JST SH service connector |

### Replacement work started

**HyperRAM:** U11–U14 are locked to ISSI `IS66WVH8M8BLL-100B1LI`
(`C1349096`). Each device is 64 Mbit, 3.0 V, 100 MHz HyperBus in a 6 × 8 mm
TFBGA-24 package. Four devices provide 32 MB and four independent colour-buffer
channels. Put them on a 3.3 V FPGA bank and use the device's single-ended clock
mode. The AP Memory APS12808L candidates are OPI Xccela parts and are no longer
part of the board plan.

**PCI bus switches:** `SN74CBTD16211DGGR` (`C2674790`, $3.00) is the current
candidate: 24-bit with the internal clamp diode, so three devices provide 72
channels for the 46 PCI signals. `SN74CB3T16210DGGR` (`C2674635`) was the
earlier candidate but JLC holds one unit. Before locking either, enumerate every
PCI signal, partition the OE groups per package, verify power-off
isolation/clamp behavior, and close 33 MHz timing.

**DDR3L:** the populated part is Winbond `W632GU6NB-11` (`C3034991`, 2 Gbit
x16, $11.10, 1,418 in stock), two devices for a 32-bit bus and 512 MB. Samsung
`K4B4G1646E-BYMA` (`C500275`, 4 Gbit, $15.80) is the 1 GB alternate on the same
96-ball footprint. Confirm the physical ball map, route A14, and use the exact
device timing in the Vivado MIG configuration before routing.

The three lower-current rails now have a common stocked regulator candidate:
`TPS54318RTER` (`C57128`), a 3 A adjustable synchronous buck from the 5 V slot
rail. Use it provisionally for 1.8 V auxiliary, 1.35 V DDR3L, and 3.3 V I/O plus
HyperRAM. Each rail still needs its own current estimate, output filter,
compensation, sequencing, and thermal check. For the 1.0 V FPGA core, use
`TPS62480RNCR` (`C2071434`) as the preferred production candidate. It is an
active TI 6 A dual-phase converter with 575 units observed at LCSC. Pair it with
two Murata `DFE252012F-R47M=P2` 0.47 uH inductors (`C703140`; 2,830 observed).
This removes the low-stock integrated-module dependency, but it still needs an
FPGA power estimate, transient simulation, thermal review, and reference-layout
implementation before release. Retain `TPSM82816PVCAR` only as a prototype
fallback, not as the production plan.

## Parts firmed up for the next schematic pass — 2026-09-16

These choices are now concrete enough to build footprints and bracket drawings
around. “Candidate” means the exact part is purchasable and assembly-compatible,
but an electrical or mechanical calculation still controls final release.

| Function | Selection | LCSC/JLC | State | Remaining check |
|---|---|---|---|---|
| FPGA, rev A | AMD `XC7A100T-2FGG484I` | `C410276` | selected | pin-identical to the `XC7A200T-2FBG484I` Voodoo 2 alternate; design power and heatsink for the 200T |
| DDR3L | 2× Winbond `W632GU6NB-11` | `C3034991` | candidate | route A14 so the footprint also takes the 4 Gbit alternate; MIG timing model |
| PCI translation | 3× TI `SN74CBTD16211DGGR` | `C2674790` | candidate | stock is 15; enumerate PCI signals and OE groups; close 33 MHz timing |
| VGA switching | 3× Omron `G6K-2F-Y DC5 BY OMZ/C` | `C231389` | selected | normally-closed RGB/H/V passthrough; coil drivers and flyback paths |
| VGA input/output | 2× Kinghelm `KH-HDR15P-F3.08` | `C3038673` | candidate | freeze bracket and board-edge datum from the Kinghelm drawing |
| HDMI output | SOFNG `HDMI-019S` | `C111617` | selected | shell stakes, bracket height, ESD and HPD/DDC circuits |
| USB-A host | `902-131A1021D10100` | `C2344` | selected | wave-solder fixture and bracket opening |
| Ethernet magjack | HANRUN `HR911105A` | `C12074` | selected | verify RTL8201F center taps, LED polarity and wave soldering |
| 1.8 V / 1.35 V / 3.3 V bucks | TI `TPS54318RTER` | `C57128` | candidate | rail budgets, magnetics, compensation and sequencing |
| 1.0 V core buck | TI `TPS62480RNCR` + 2× Murata `DFE252012F-R47M=P2` | `C2071434`, `C703140` | preferred candidate | Xilinx power estimate, transient/thermal checks and reference layout |
| UART service connector | JST `SM04B-SRSS-TB(LF)(SN)` | `C160404` | selected | assign GND, 3.3 V reference, TX and RX; do not back-power the card |
| Rail test access | 1.0 mm plated soldermask-open pads | no BOM part | selected | one pad per rail plus nearby ground pads |

The three DPDT relays provide six normally-closed changeover poles: five are used
for RGB, HSync, and VSync, with one spare. This is intentionally fail-safe: when
the card is unpowered, VGA input connects directly to VGA output. It also means
the rough placement must grow from one relay placeholder to three.

The bracket connectors are intentionally through-hole or shell-staked where that
improves mechanical strength. The JLC quote must therefore include wave soldering
or a manual secondary-operation line; an SMT-only quote would be misleading.

### Revised JLC upload status

The revised BOM and CPL now contain the concrete catalog selections above,
remove the PCB-only test pads from the assembly files, and add the two core-buck
inductor placements. A re-upload was attempted on 2026-09-16, but the saved JLC
session redirected to account login before processing the files. Re-run the
upload after login to measure JLC's actual automatic match rate and five-board
component/assembly price; do not reuse the earlier 26-unmatched-parts result.

## HyperRAM decision — 2026-09-16

Use four ISSI `IS66WVH8M8BLL-100B1LI` devices for U11–U14. The open MJoergen
HyperRAM controller targets this device family and already has Artix-7/Vivado
constraints, avoiding a new Xccela controller and PHY. At 100 MHz, the four
channels provide about 560 MB/s effective aggregate bandwidth for the colour
path. Keep colour-in-DDR3 behind a BRAM tile cache as a firmware fallback.

Next-pass actions:

- Replace the rough placeholders with the exact ISSI 24-ball footprint and
  verify every ball against the manufacturer datasheet before routing.
- Reserve a 3.3 V FPGA bank for U11–U14 and confirm the complete pin count,
  clock-capable pins, RWDS capture, RESET# sharing, and VCCO current.
- Route single-ended CK as specified for 3.0 V operation; do not copy a 1.8 V
  differential-clock layout.
- Put local decoupling at every device and model simultaneous switching across
  the four independent x8 buses.
- Bring up at 100 MHz first. Qualify Infineon `S27KL0642` only as a later,
  same-voltage performance alternate after a ball-map and timing review.

Current spot pricing is roughly $6.27–$6.65 per device at quantity one, so the
old $1.92 estimate is retired. Recheck LCSC/JLC stock and assembly-library status
when the exact schematic BOM is uploaded.

## No microcontroller — 2026-09-16

The two-RP2350 bring-up card and the RP2350B on the basic card are dropped.
The Artix is the PCI target (PCI33_3 on one HR bank, bus clock on a
clock-capable pin through an MMCM, about 1-2K LUT6, one BRAM, 47 pins), boots
itself from the QSPI (MultiBoot with a golden image), is programmed over JTAG,
takes field updates over PCI or from the T113, and drives the relay MOSFETs.
Consequences for the next pass:

- JTAG J8 is a first-class connector, not a debug afterthought: pick the
  connector (2 mm 2x7 Xilinx header or 6-pin 0.1 in) and route TCK/TMS/TDI/TDO
  short. The T113 should reach the same pins so it can be the on-board
  programmer under OpenOCD later.
- Add a bench 5 V input (4-pin Molex or barrel jack) diode-ORed or switched
  against the slot rail through the load switch Q10, so the card runs outside
  a PC for JTAG-to-AXI and T113 Linux work.
- Bus-switch enables pulled inactive and released by DONE, so an unconfigured
  card looks empty to the slot.
- The 5 V PCI audit in item 4 becomes an ordering gate: the first card carries
  the PCI risk that the bring-up card was meant to absorb. Host fallbacks while
  PCI is debugged: Vivado JTAG-to-AXI on J8, then the T113 link.
- Pin budget: PCI, 32-bit DDR3, four HyperRAM channels, DAC, HDMI, T113 link,
  relays and configuration take roughly 240 of 285 I/O before any expansion
  header, on either FPGA.
