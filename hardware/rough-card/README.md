# Voodoo 2 Artix rough card

This directory contains a mechanical and component-placement feasibility pass
derived from `docs/voodoo2-artix-card-plan.html`.

## Deliverables

- `voodoo2_artix_rough.kicad_pcb` — six-layer KiCad placement study
- `voodoo2_artix_rough_gerbers.zip` — JLCPCB-readable bare-board archive
- `renders/top.png` — KiCad top-side render
- `NEXT_PASS_SUGGESTIONS.md` — technical recommendations and quote notes
- `generate_rough_board.py` — deterministic source for the rough board

## Scope warning

This board is intentionally unrouted and uses labeled placeholder footprints.
The PCI finger geometry is preliminary. It is suitable for board-house quoting
and floorplanning discussion only, not fabrication for installation and not
assembly.

Regenerate the board with:

```sh
python3 hardware/rough-card/generate_rough_board.py
```

The quote archive contains all six copper layers, front/back mask and
silkscreen, edge cuts, and an Excellon drill file.
