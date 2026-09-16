# Voodoo 2 Artix Rev A pricing board

This directory contains the first populated board model intended to improve
fabrication and assembly pricing accuracy.

## What this pass includes

- The current selected or candidate FPGA, processor, memories, power ICs,
  switches, relays and connectors.
- A top-side-only population model.
- 148 capacitor and 120 resistor placements for bypassing, rail support,
  termination, straps and feedback networks.
- Allowances for relay drivers, protection arrays, input protection, auxiliary
  buck inductors, JTAG, manufacturing test and status LEDs.
- A six-layer 174.63 × 106.68 mm PCI 2.x full-height, 5 V-keyed board
  envelope. The connector uses the standard 1.27 mm pitch and 60 physical
  contacts per side, numbered A/B1–62 with A/B50–51 occupied by the key.

## Important limitation

This board is **unrouted and not safe to fabricate or assemble**. The small-part
counts and support circuits are pricing allowances. Exact values and footprints
must be derived from the final power tree, FPGA pin/bank plan, memory topology,
video termination, protection design and manufacturer reference schematics.

The generated Gerbers are for quote ingestion only. They must remain clearly
named `pricing` and must never be treated as a release package.

## Live JLCPCB assembly check — 2026-09-16

JLCPCB accepted the previous Gerber, BOM, and CPL files as a **6-layer 175 × 107 mm**
board with top-side Standard PCBA at quantity 5. JLC added two 5 mm assembly
rails, making the process panel 175 × 117 mm. The quote uses 1.6 mm FR-4, ENIG,
gold fingers, and a 30° bevel.

That saved quote predates the PCI-fit correction. The current generated board
is 174.63 × 106.68 mm and needs a new upload before its fabrication price is
treated as current.

- 45 BOM groups / 326 placements were parsed successfully.
- JLC reports 33 groups as matched/confirmed. One is an arbitrary red-LED
  suggestion that remains deselected; the exact intended in-stock selections
  cover 32 groups / 281 placements.
- 3 groups have exact parts but insufficient JLC inventory (5 placements):
  J2/J3 `634-015-363-543` VGA connectors and U15-U17
  `SN74CB3T16210DGGR` bus switches.
- 9 groups have no part selected (35 placements): F1, J8, J9, L1, L22-L24,
  Q10, R121-R144, U31-U32, and U33. The five status LEDs form a tenth unresolved
  group after JLC suggested an arbitrary red LED.
- Exact-part coverage is 286 of 326 placements (87.7%); currently available
  coverage is 281 of 326 placements (86.2%).

The live partial quote is **$1,360.84 total / $272.17 per board** before
shipping, tax, VAT, duty, and engineering adjustments. It comprises $66.04
PCB fabrication and $1,294.80 Standard PCBA. The latter includes $1,186.62 of
components for 30 priced/in-stock BOM groups plus setup, stencil, feeder,
assembly, manual-solder, X-ray, and packaging charges. It excludes the 3
shortage groups and the 9 unresolved groups, so it is a useful feasibility
baseline rather than a complete build price. No order was placed.

The saved JLCPCB project identifier is
`9c24412ea1ea4ed59fbc6de89d5dcaed`.

Regenerate the board with:

```sh
python3 hardware/rev-a-pricing/generate_pricing_board.py
```

## BOM revision — 2026-09-16, rev A on the XC7A100T with stocked cheaper parts

The quote above was reconciled line by line against live JLCPCB catalogue
prices. Fabrication and assembly came in below plan; the overrun was four
component lines with stale plan prices. The BOM, CPL and board silkscreen now
carry these selections. The $272.17 quote is for the previous BOM and must be
re-uploaded.

| Function | Was | Now | JLC | Qty-5 price | Stock | Per card |
|---|---|---|---|---|---|---|
| FPGA (rev A) | XC7A200T-2FBG484I `C494680`, $119.35 | XC7A100T-2FGG484I | `C410276` | $63.75 | 1,405 | −$55.60 |
| DDR3L, 32-bit bus | 2× K4B4G1646E-BYMA `C500275`, $15.80 | 2× Winbond W632GU6NB-11, 2 Gbit x16 | `C3034991` | $11.10 | 1,418 | −$9.39 |
| PCI bus switches | 3× SN74CB3T16210DGGR `C2674635`, $4.51, 1 in stock | 3× SN74CBTD16211DGGR | `C2674790` | $3.00 | 15 | −$4.52 |
| VGA connector | 2× EDAC 634-015-363-543 `C3099918`, $10.32, 0 in stock | 1× bidirectional Kinghelm KH-HDR15P-F3.08 | `C3038673` | $0.28 | 310 | −$20.36 |

The XC7A200T-2FBG484I stays as the Voodoo 2 alternate: AMD DS180 lists FGG484
and FBG484 as footprint compatible, and a ball-by-ball diff of AMD's
`xc7a100tfgg484pkg.txt` and `xc7a200tfbg484pkg.txt` shows all 484 balls
identical in name, bank, byte group and I/O type. The K4B4G1646E-BYMA stays as
the 1 GB DDR3 alternate on the same 96-ball footprint (route A14).

J8 is the JXTCONN `IDC2.0-2*7PZ` 2 mm 2x7 shrouded JTAG header
(`C53207571`, $0.25, Xilinx cable pinout). The optional J10 bench-power barrel
jack was removed on 2026-09-17: the card is powered through the PCI slot, and
out-of-system work should use a powered PCI riser or fixture rather than adding
a consumer power connector to the production card.

An RP2350B block was added the same day as a peripheral, not a boot or PCI
dependency: U40 `RP2350B` (`C42415655`, $1.28) with a 24-pin registered link
to a 3.3 V FPGA bank plus JTAG and configuration lines, Y3 12 MHz crystal
(`C9002`), U41 `W25Q128JVSIQ` flash (`C97521`, the same reel as the FPGA
configuration flash; the 32 Mbit part has 3 in stock at JLC), L25 3.3 uH core-regulator
inductor (`C354572`), J11 USB-C `TYPE-C-31-M-12` (`C165948`) on the right
edge with U35 ESD, CC and series resistors R145-R148, SW1 BOOTSEL
(`C318884`), and ten decoupling capacitors. It is the on-board USB JTAG
programmer and console, and the host for PIO experiments behind the FPGA's
registered crossbar. About $3 in parts. The BOM is now 56 groups and 347
placements.

Estimated complete card at quantity 5 from live JLC prices, with the quoted
fabrication and assembly charges unchanged: about $220 with the 100T, about
$275 with the 200T. The SN74CBTD16211 stock of 15 may not clear JLC attrition
for five boards; see `NEXT_PASS_SUGGESTIONS.md` for the fallback.

## Depth SRAM footprint and SLI header — 2026-09-17

Two additions for the Voodoo 2 phase, both placed in the FPGA cluster:

- **U50, depth-buffer SRAM, footprint only.** ISSI `IS61NLP51236B-200B3LI`,
  512K x 36 ZBT synchronous SRAM, TFBGA-165 13 x 15 mm at 1.0 mm pitch, left of
  the FPGA at (99, 53) with eight DNP 0402 bypass capacitors C173-C180 below
  it. One part holds a 1024 x 768 16-bit depth buffer (1.5 MB of 2.25 MB) with
  single-cycle random read-modify-write, which is what the depth stage needs
  and what HyperRAM and DDR3 cannot give. It lets a 200T build move depth off
  block RAM for 800 x 600 and above and frees all 13 Mbit for texture caches.
  Live prices 2026-09-17: LCSC $12.04 at qty 1 / $9.65 at 100 (`C1349330`,
  zero stock, pre-order), DigiKey $25.73 with 163 in stock. Larger ZBT parts
  jump to $46-119 (36 Mbit) and $80-207 (72 Mbit), so one 18 Mbit part for
  depth alone is the sensible cut. Not populated on rev A: the footprint carries
  KiCad's DNP, exclude-from-BOM and exclude-from-position attributes, so it
  does not appear in the workbooks or the quote, like the U7 capture ADC. About
  62 FPGA pins on a 3.3 V bank when it is used. The stock 165-ball STEP body
  is the 11.9 x 16 mm Analog Devices envelope, 0.5 mm off in each axis; the
  silkscreen outline is the correct 13 x 15 mm.
- **J12, SLI link header, populated.** BOOMELE `2.54-2*17P` (`C9042`, $0.49,
  3,366 in stock), the original Voodoo 2 form: a 2 x 17 shrouded IDC box
  header at 2.54 mm on the top edge at (124, 27.5), 48 x 9 mm, so a period
  34-way SLI ribbon or any floppy-style IDC cable fits. It carries the slave
  card's scanlines to the master for scanline interleave, which is how two
  cards reach 1024 x 768 with the unmodified 3dfx drivers. The link protocol is
  ours, not 3dfx's, so it pairs two of these cards and not a period Voodoo 2;
  budget 16 data, clock, three syncs, a swap handshake and grounds.
  Through-hole, so it is a hand-solder line at JLC like J8. The C1-C32 bypass
  grid moved 1 mm down (y 33) to clear the shroud; the header clears U20 by
  0.35 mm and the grid by 0.8 mm, which is placement-model spacing, not a
  routed-board clearance.

The board is now 360 footprints and the workbooks 56 groups / 347 placements
(J3 was dropped when the bracket went to one bidirectional VGA; J12 is the
one new populated line).

Regenerate everything with:

```sh
python3 hardware/rev-a-pricing/generate_pricing_board.py
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli pcb export gerbers --layers F.Cu,In1.Cu,In2.Cu,In3.Cu,In4.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts --output hardware/rev-a-pricing/fabrication/ hardware/rev-a-pricing/voodoo2_artix_rev_a_pricing.kicad_pcb
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli pcb export drill --output hardware/rev-a-pricing/fabrication/ hardware/rev-a-pricing/voodoo2_artix_rev_a_pricing.kicad_pcb
(cd hardware/rev-a-pricing && node build_quote_workbooks.mjs)
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli pcb render --output hardware/rev-a-pricing/rev_a_top_3d.png --width 1568 --height 984 --side top --quality high --background opaque --floor --perspective --rotate '20,0,-15' hardware/rev-a-pricing/voodoo2_artix_rev_a_pricing.kicad_pcb
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli pcb render --output hardware/rev-a-pricing/rev_a_bottom_3d.png --width 1568 --height 984 --side bottom --quality high --background opaque --floor --perspective --rotate '-20,0,15' hardware/rev-a-pricing/voodoo2_artix_rev_a_pricing.kicad_pcb
```

The top and bottom images are required outputs for every subsequent board pass.
Render both from the same generated board at the same resolution and quality so
mechanical changes can be compared directly between revisions.

Bracket and edge connectors are now post-processed into real KiCad footprints
by `fit_connector_footprints.py`; `validate_connector_clearance.py` then checks
the bracket gaps, usable board edge and connector/component collisions.  The
single-slot bracket carries one bidirectional VGA, HDMI-A, USB-A and Ethernet,
all facing outward. Their four courtyards plus 2 mm gaps occupy 89.86 mm of the
approximately 99.44 mm usable edge. Detailed measurements are in
`CONNECTOR_CLEARANCE.md`.

The pricing board now carries 358 package-level STEP models across 360
footprints.  The only intentional no-model items are the PCI edge fingers and
the provisional L1 input-filter allowance.  Major IC package bodies match the
selected BOM package and dimensions.  KiCad-library equivalents are used for
the bracket connectors and the RP2350B body, so these renders are suitable for
placement and envelope review but do not replace manufacturer STEP models or
datasheet-derived courtyard checks.
