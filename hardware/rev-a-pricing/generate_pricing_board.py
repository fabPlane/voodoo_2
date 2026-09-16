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
import subprocess
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


DNP_ATTR = " dnp exclude_from_pos_files exclude_from_bom"


def smd2(ref: str, value: str, x: float, y: float, package: str, w: float, h: float,
         dnp: bool = False) -> str:
    """Small two-terminal pricing footprint with nominal package geometry."""
    pad_w = 0.55 if w <= 1.1 else 0.80
    pad_h = max(0.45, min(h, 1.25))
    pad_x = max(0.30, w / 2 - pad_w / 2)
    model_paths = {
        "R_0402": "Resistor_SMD.3dshapes/R_0402_1005Metric.step",
        "C_0402": "Capacitor_SMD.3dshapes/C_0402_1005Metric.step",
        "C_0603": "Capacitor_SMD.3dshapes/C_0603_1608Metric.step",
        "C_0805": "Capacitor_SMD.3dshapes/C_0805_2012Metric.step",
        "SOD-123": "Diode_SMD.3dshapes/D_SOD-123.step",
        "SMA": "Diode_SMD.3dshapes/D_SMA.step",
        "Crystal-3225": "Crystal.3dshapes/Crystal_SMD_3225-4Pin_3.2x2.5mm.step",
        "LED_0603": "LED_SMD.3dshapes/LED_0603_1608Metric.step",
    }
    model_text = rough.model(model_paths[package]) if package in model_paths else ""
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
		(attr smd{DNP_ATTR if dnp else ""})
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
{model_text}
	)'''


def grid(refs: list[str], value: str, package: str, start_x: float, start_y: float,
         columns: int, dx: float, dy: float, w: float, h: float, dnp: bool = False) -> list[str]:
    items = []
    for index, ref in enumerate(refs):
        x = start_x + (index % columns) * dx
        y = start_y + (index // columns) * dy
        items.append(smd2(ref, value, x, y, package, w, h, dnp))
    return items


def option_placeholder(ref: str, value: str, x: float, y: float, w: float, h: float,
                       model_path: str, rotate_z: float = 0, offset_x: float = 0,
                       offset_y: float = 0, dnp: bool = False,
                       ref_y: float | None = None) -> str:
    """Quote placeholder for a part the rough board does not know about.

    Adds a stock STEP body and, for footprint-only options, the DNP attributes so
    the part stays out of the BOM and CPL like the unpopulated ADC.
    """
    text = rough.placeholder(ref, value, x, y, w, h)
    if ref_y is not None:
        # Move the reference label off the default above-the-outline spot.
        text = text.replace(f"(at 0 {-h/2-1.4:.3f} 0)", f"(at 0 {ref_y:.3f} 0)", 1)
    if dnp:
        text = text.replace("(attr smd)", f"(attr smd{DNP_ATTR})")
    assert text.endswith("\n\t)")
    return text[:-3] + rough.model(model_path, rotate_z, offset_x, offset_y) + "\n\t)"


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
        rough.text("TOP-SIDE POPULATION MODEL — SUPPORT VALUES PROVISIONAL", 107.5, 119.9, 0.85, "Cmts.User"),
    ]

    # 148 capacitor placements: dense local bypassing plus regulator input/output
    # allowance.  Counts are intended for assembly pricing, not final values.
    additions += grid(numbered("C", 1, 32), "100nF", "C_0402", 92.0, 33.0, 8, 1.75, 1.65, 1.0, 0.5)
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
    additions += grid(numbered("R", 145, 4), "USB", "R_0402", 166.0, 114.0, 4, 2.0, 1.7, 1.0, 0.5)
    additions += grid(numbered("C", 163, 8), "100nF", "C_0402", 168.0, 92.0, 4, 1.75, 1.65, 1.0, 0.5)

    # Explicit support-device allowances.  Exact protection-array choices and
    # the auxiliary-buck magnetics are still engineering candidates.
    additions += [
        rough.placeholder("Q1", "2N7002 RELAY", 49.5, 84.0, 3.0, 2.0),
        rough.placeholder("Q2", "2N7002 RELAY", 49.5, 88.0, 3.0, 2.0),
        rough.placeholder("Q3", "2N7002 RELAY", 60.0, 98.0, 3.0, 2.0),
        smd2("D1", "1N4148W", 51.5, 84.0, "SOD-123", 3.7, 1.8),
        smd2("D2", "1N4148W", 51.5, 88.0, "SOD-123", 3.7, 1.8),
        smd2("D3", "1N4148W", 63.0, 98.0, "SOD-123", 3.7, 1.8),
        rough.placeholder("U30", "USB ESD", 43.5, 86.0, 3.0, 3.0),
        rough.placeholder("U31", "HDMI ESD A", 43.5, 70.0, 3.0, 3.0),
        rough.placeholder("U32", "HDMI ESD B", 43.5, 74.0, 3.0, 3.0),
        rough.placeholder("U33", "VGA ESD", 43.5, 47.0, 3.0, 3.0),
        rough.placeholder("U34", "ETH ESD", 60.0, 103.0, 3.0, 3.0),
        rough.placeholder("L22", "1uH AUX", 158.0, 57.5, 4.0, 4.0),
        rough.placeholder("L23", "1uH AUX", 176.0, 57.5, 4.0, 4.0),
        rough.placeholder("L24", "1uH AUX", 158.0, 73.0, 4.0, 4.0),
        rough.placeholder("F1", "PCI 5V FUSE", 184.0, 72.0, 5.0, 3.0),
        smd2("D10", "5V TVS", 188.0, 72.0, "SMA", 4.6, 2.6),
        rough.placeholder("Q10", "5V LOAD SWITCH", 184.0, 77.0, 4.0, 4.0),
        rough.placeholder("J8", "JTAG 2x7 2.0mm", 86.0, 92.0, 16.0, 7.0),
        rough.placeholder("J9", "MFG TEST", 73.0, 96.0, 16.0, 4.0),
        rough.placeholder("U40", "RP2350B", 172.0, 100.0, 10.0, 10.0),
        smd2("Y3", "12MHz", 165.0, 108.0, "Crystal-3225", 3.2, 2.5),
        rough.placeholder("U41", "W25Q128 MCU FLASH", 184.0, 92.0, 5.0, 6.0),
        rough.placeholder("L25", "3.3uH MCU", 172.0, 110.0, 4.0, 4.0),
        rough.placeholder("J11", "USB-C MCU", 190.0, 100.0, 9.0, 7.5),
        rough.placeholder("U35", "USB ESD", 186.0, 108.0, 3.0, 3.0),
        rough.placeholder("SW1", "BOOTSEL", 180.0, 110.0, 3.9, 3.9),
        smd2("C171", "10uF", 178.0, 86.0, "C_0805", 2.0, 1.25),
        smd2("C172", "10uF", 181.0, 86.0, "C_0805", 2.0, 1.25),
        smd2("D20", "POWER LED", 188.0, 83.0, "LED_0603", 1.6, 0.8),
        smd2("D21", "FPGA DONE", 188.0, 85.0, "LED_0603", 1.6, 0.8),
        smd2("D22", "CPU STATUS", 188.0, 87.0, "LED_0603", 1.6, 0.8),
        smd2("D23", "ETH LINK", 188.0, 89.0, "LED_0603", 1.6, 0.8),
        smd2("D24", "FAULT", 188.0, 91.0, "LED_0603", 1.6, 0.8),
    ]

    # Footprint-only depth-buffer SRAM option, left of the FPGA. One 512K x 36
    # ZBT SRAM (ISSI IS61NLP51236B-200B3LI, TFBGA-165 13 x 15 mm, 1.0 mm pitch)
    # holds a 1024 x 768 16-bit depth buffer with single-cycle random access,
    # which lets a 200T build move depth off block RAM. Not populated on rev A;
    # the DNP attributes keep U50 and its bypass out of the BOM and CPL. The
    # stock 165-ball STEP body is 11.9 x 16 mm, close enough for the quote view.
    additions += [
        option_placeholder(
            "U50", "ZBT SRAM 512Kx36 DNP", 99.0, 53.0, 13.0, 15.0,
            "Package_BGA.3dshapes/Analog_BGA-165_11.9x16mm_Layout11x15_P1.0mm.step", dnp=True,
        ),
        rough.text("SRAM OPTION — DNP V1", 99.0, 41.0, 1.0),
    ]
    additions += grid(numbered("C", 173, 8), "100nF DNP", "C_0402", 92.0, 62.5, 4, 1.75, 1.65, 1.0, 0.5, dnp=True)

    # SLI link header on the top edge: the original Voodoo 2 form, a 2 x 17
    # shrouded IDC box header at 2.54 mm for a 34-way ribbon between two cards.
    # Carries the slave card's scanlines to the master for scanline interleave.
    # Populated: BOOMELE 2.54-2*17P, LCSC C9042. The C1 bypass grid sits 1 mm
    # lower than before to clear the 48 x 9 mm shroud.
    additions += [
        option_placeholder(
            "J12", "SLI 2x17 2.54mm", 124.0, 27.5, 48.3, 8.9,
            "Connector_IDC.3dshapes/IDC-Header_2x17_P2.54mm_Vertical.step",
            rotate_z=90, offset_x=20.32, offset_y=1.27, ref_y=6.4,
        ),
    ]

    return board + "".join(additions).replace("\n+", "\n") + "\n)\n"


if __name__ == "__main__":
    OUT.write_text(build())
    kicad_python = Path("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3")
    fitter = HERE / "fit_connector_footprints.py"
    validator = HERE / "validate_connector_clearance.py"
    if kicad_python.exists():
        subprocess.run([str(kicad_python), str(fitter), str(OUT)], check=True)
        subprocess.run([str(kicad_python), str(validator), str(OUT)], check=True)
    else:
        print("warning: KiCad Python not found; connector placeholders were not replaced")
    print(OUT)
