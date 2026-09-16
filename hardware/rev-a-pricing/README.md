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
- A six-layer 175 × 107 mm board outline matching the accepted rough quote.

## Important limitation

This board is **unrouted and not safe to fabricate or assemble**. The small-part
counts and support circuits are pricing allowances. Exact values and footprints
must be derived from the final power tree, FPGA pin/bank plan, memory topology,
video termination, protection design and manufacturer reference schematics.

The generated Gerbers are for quote ingestion only. They must remain clearly
named `pricing` and must never be treated as a release package.

## Live JLCPCB assembly check — 2026-09-16

JLCPCB accepted the Gerber, BOM, and CPL files as a **6-layer 175 × 107 mm**
board with top-side Standard PCBA at quantity 5. JLC added two 5 mm assembly
rails, making the process panel 175 × 117 mm. The quote uses 1.6 mm FR-4, ENIG,
gold fingers, and a 30° bevel.

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
| VGA connectors | 2× EDAC 634-015-363-543 `C3099918`, $10.32, 0 in stock | 2× Kinghelm KH-HDR15P-F3.08 | `C3038673` | $0.28 | 310 | −$20.08 |

The XC7A200T-2FBG484I stays as the Voodoo 2 alternate: AMD DS180 lists FGG484
and FBG484 as footprint compatible, and a ball-by-ball diff of AMD's
`xc7a100tfgg484pkg.txt` and `xc7a200tfbg484pkg.txt` shows all 484 balls
identical in name, bank, byte group and I/O type. The K4B4G1646E-BYMA stays as
the 1 GB DDR3 alternate on the same 96-ball footprint (route A14).

Estimated complete card at quantity 5 from live JLC prices, with the quoted
fabrication and assembly charges unchanged: about $220 with the 100T, about
$275 with the 200T. The SN74CBTD16211 stock of 15 may not clear JLC attrition
for five boards; see `NEXT_PASS_SUGGESTIONS.md` for the fallback.

Regenerate everything with:

```sh
python3 hardware/rev-a-pricing/generate_pricing_board.py
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli pcb export gerbers --output hardware/rev-a-pricing/fabrication/ hardware/rev-a-pricing/voodoo2_artix_rev_a_pricing.kicad_pcb
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli pcb export drill --output hardware/rev-a-pricing/fabrication/ hardware/rev-a-pricing/voodoo2_artix_rev_a_pricing.kicad_pcb
(cd hardware/rev-a-pricing && node build_quote_workbooks.mjs)
```
