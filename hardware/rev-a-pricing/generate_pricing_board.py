#!/usr/bin/env python3
"""Generate a populated Rev-A pricing board.

This board is an unrouted assembly-cost model.  The principal ICs and
connectors use the current selected/candidate MPNs.  Small passives and support
circuits are deliberately included as realistic placement allowances so that
assembly quotes include feeder count and joint count.  It is not a fabrication
release.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


HERE = Path(__file__).resolve().parent
ROUGH = HERE.parent / "rough-card" / "generate_rough_board.py"
OUT = HERE / "voodoo2_artix_rev_a_pricing.kicad_pcb"

spec = spec_from_file_location("rough_board", ROUGH)
rough = module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(rough)


def uid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"voodoo2-artix-rev-a-pricing/{name}"))


def smd2(ref: str, value: str, x: float, y: float, package: str, w: float, h: float) -> str:
    """Small two-terminal pricing footprint with nominal package geometry."""
    pad_w = 0.55 if w <= 1.1 else 0.80
    pad_h = max(0.45, min(h, 1.25))
    pad_x = max(0.30, w / 2 - pad_w / 2)
    return f'''
	(footprint "Pricing:{package}"
		(layer "F.Cu")
		(uuid "{uid(ref)}")
		(at {x:.3f} {y:.3f})
		(property "Reference" "{ref}"
			(at 0 {-h/2-0.65:.3f} 0)
			(layer "F.SilkS") hide
			(uuid "{uid(ref+'-reference')}")
			(effects (font (size 0.55 0.55) (thickness 0.10)))
		)
		(property "Value" "{value}"
			(at 0 0 0)
			(layer "F.Fab") hide
			(uuid "{uid(ref+'-value')}")
			(effects (font (size 0.55 0.55) (thickness 0.10)))
		)
		(attr smd)
		(fp_rect
			(start {-w/2:.3f} {-h/2:.3f})
			(end {w/2:.3f} {h/2:.3f})
			(stroke (width 0.12) (type default))
			(fill none)
			(layer "F.SilkS")
			(uuid "{uid(ref+'-outline')}")
		)
		(pad "1" smd roundrect (at {-pad_x:.3f} 0) (size {pad_w:.3f} {pad_h:.3f})
			(layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.2)
			(uuid "{uid(ref+'-pad-1')}")
		)
		(pad "2" smd roundrect (at {pad_x:.3f} 0) (size {pad_w:.3f} {pad_h:.3f})
			(layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.2)
			(uuid "{uid(ref+'-pad-2')}")
		)
	)'''


def grid(refs: list[str], value: str, package: str, start_x: float, start_y: float,
         columns: int, dx: float, dy: float, w: float, h: float) -> list[str]:
    items = []
    for index, ref in enumerate(refs):
        x = start_x + (index % columns) * dx
        y = start_y + (index // columns) * dy
        items.append(smd2(ref, value, x, y, package, w, h))
    return items


def numbered(prefix: str, start: int, count: int) -> list[str]:
    return [f"{prefix}{n}" for n in range(start, start + count)]


def build() -> str:
    board = rough.build().rsplit("\n)", 1)[0]
    board = board.replace(
        "Voodoo 2 Artix Card — Rough Placement / Quote Board",
        "Voodoo 2 Artix Card — Rev A Pricing Population",
    ).replace(
        '(rev "0.1-ROUGH")', '(rev "0.2-PRICING")'
    ).replace(
        '(company "Feasibility study — NOT FOR ASSEMBLY")',
        '(company "Quote model — UNROUTED / NOT FOR FABRICATION")',
    ).replace(
        'VOODOO 2 ARTIX — REV 0.1 ROUGH / NOT FOR ASSEMBLY',
        'VOODOO 2 ARTIX — REV A PRICING / UNROUTED',
    ).replace(
        '"XC7A200T-FBG484"', '"XC7A100T-FGG484"'
    ).replace(
        '"DDR3L x16"', '"DDR3L x16 2Gb"'
    ).replace(
        '"SN74CB3T16210"', '"SN74CBTD16211"'
    )

    additions = [
        rough.text("TOP-SIDE POPULATION MODEL — SUPPORT VALUES PROVISIONAL", 107.5, 119.9, 0.85),
    ]

    # 148 capacitor placements: dense local bypassing plus regulator input/output
    # allowance.  Counts are intended for assembly pricing, not final values.
    additions += grid(numbered("C", 1, 32), "100nF", "C_0402", 92.0, 32.0, 8, 1.75, 1.65, 1.0, 0.5)
    additions += grid(numbered("C", 33, 32), "100nF", "C_0402", 92.0, 68.0, 8, 1.75, 1.65, 1.0, 0.5)
    additions += grid(numbered("C", 65, 16), "100nF", "C_0402", 51.0, 37.0, 4, 1.65, 1.65, 1.0, 0.5)
    additions += grid(numbered("C", 81, 16), "100nF", "C_0402", 137.0, 75.0, 4, 1.65, 1.65, 1.0, 0.5)
    additions += grid(numbered("C", 101, 24), "1uF", "C_0603", 153.5, 78.0, 6, 2.15, 2.0, 1.6, 0.8)
    additions += grid(numbered("C", 131, 16), "10uF", "C_0805", 154.0, 88.0, 4, 3.0, 2.5, 2.0, 1.25)
    additions += grid(numbered("C", 151, 12), "22uF", "C_0805", 174.0, 78.0, 4, 3.0, 2.5, 2.0, 1.25)

    # 120 resistor placements cover straps, pulls, clock/video series damping,
    # VGA terminations, feedback networks, LEDs and bring-up options.
    additions += grid(numbered("R", 1, 24), "0R", "R_0402", 49.5, 25.0, 8, 2.0, 1.7, 1.0, 0.5)
    additions += grid(numbered("R", 31, 32), "10k", "R_0402", 49.5, 64.0, 8, 2.0, 1.7, 1.0, 0.5)
    additions += grid(numbered("R", 71, 40), "33R", "R_0402", 91.5, 100.0, 10, 2.2, 1.8, 1.0, 0.5)
    additions += grid(numbered("R", 121, 24), "VALUE_TBD", "R_0402", 148.0, 100.0, 8, 2.1, 1.8, 1.0, 0.5)

    # Explicit support-device allowances.  Exact protection-array choices and
    # the auxiliary-buck magnetics are still engineering candidates.
    additions += [
        rough.placeholder("Q1", "2N7002 RELAY", 49.5, 84.0, 3.0, 2.0),
        rough.placeholder("Q2", "2N7002 RELAY", 49.5, 88.0, 3.0, 2.0),
        rough.placeholder("Q3", "2N7002 RELAY", 49.5, 92.0, 3.0, 2.0),
        smd2("D1", "1N4148W", 51.5, 84.0, "SOD-123", 3.7, 1.8),
        smd2("D2", "1N4148W", 51.5, 88.0, "SOD-123", 3.7, 1.8),
        smd2("D3", "1N4148W", 51.5, 92.0, "SOD-123", 3.7, 1.8),
        rough.placeholder("U30", "USB ESD", 43.5, 86.0, 3.0, 3.0),
        rough.placeholder("U31", "HDMI ESD A", 43.5, 70.0, 3.0, 3.0),
        rough.placeholder("U32", "HDMI ESD B", 43.5, 74.0, 3.0, 3.0),
        rough.placeholder("U33", "VGA ESD", 43.5, 47.0, 3.0, 3.0),
        rough.placeholder("U34", "ETH ESD", 43.5, 103.0, 3.0, 3.0),
        rough.placeholder("L22", "1uH AUX", 158.0, 57.5, 4.0, 4.0),
        rough.placeholder("L23", "1uH AUX", 176.0, 57.5, 4.0, 4.0),
        rough.placeholder("L24", "1uH AUX", 158.0, 73.0, 4.0, 4.0),
        rough.placeholder("F1", "PCI 5V FUSE", 184.0, 72.0, 5.0, 3.0),
        smd2("D10", "5V TVS", 188.0, 72.0, "SMA", 4.6, 2.6),
        rough.placeholder("Q10", "5V LOAD SWITCH", 184.0, 77.0, 4.0, 4.0),
        rough.placeholder("J8", "JTAG 2x7 2.0mm", 86.0, 92.0, 16.0, 7.0),
        rough.placeholder("J9", "MFG TEST", 73.0, 96.0, 16.0, 4.0),
        rough.placeholder("J10", "BENCH 5V DC JACK", 190.0, 27.0, 9.0, 14.0),
        smd2("D20", "POWER LED", 188.0, 83.0, "LED_0603", 1.6, 0.8),
        smd2("D21", "FPGA DONE", 188.0, 85.0, "LED_0603", 1.6, 0.8),
        smd2("D22", "CPU STATUS", 188.0, 87.0, "LED_0603", 1.6, 0.8),
        smd2("D23", "ETH LINK", 188.0, 89.0, "LED_0603", 1.6, 0.8),
        smd2("D24", "FAULT", 188.0, 91.0, "LED_0603", 1.6, 0.8),
    ]

    return board + "".join(additions).replace("\n+", "\n") + "\n)\n"


if __name__ == "__main__":
    OUT.write_text(build())
    print(OUT)
