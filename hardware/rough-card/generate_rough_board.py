#!/usr/bin/env python3
"""Generate the Rev-B mechanical/placement feasibility board.

This intentionally produces a quote-grade, unrouted placement study.  It is not
an electrically complete design and must not be ordered as an assembled board.
"""

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


OUT = Path(__file__).with_name("voodoo2_artix_rough.kicad_pcb")


def uid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"voodoo2-artix-rough/{name}"))


def text(value: str, x: float, y: float, size: float = 1.5, layer: str = "F.SilkS", justify: str = "") -> str:
    j = f" (justify {justify})" if justify else ""
    return f'''\n\t(gr_text "{value}"\n\t\t(at {x:.3f} {y:.3f})\n\t\t(layer "{layer}")\n\t\t(uuid "{uid('text-'+value+str(x)+str(y))}")\n\t\t(effects (font (size {size:.3f} {size:.3f}) (thickness 0.25)){j})\n\t)'''


def line(x1: float, y1: float, x2: float, y2: float, layer: str, width: float = 0.25, name: str = "") -> str:
    return f'''\n\t(gr_line\n\t\t(start {x1:.3f} {y1:.3f})\n\t\t(end {x2:.3f} {y2:.3f})\n\t\t(stroke (width {width:.3f}) (type default))\n\t\t(layer "{layer}")\n\t\t(uuid "{uid('line-'+name+str(x1)+str(y1)+str(x2)+str(y2))}")\n\t)'''


def rect(x1: float, y1: float, x2: float, y2: float, layer: str = "F.SilkS", width: float = 0.3, name: str = "") -> str:
    return f'''\n\t(gr_rect\n\t\t(start {x1:.3f} {y1:.3f})\n\t\t(end {x2:.3f} {y2:.3f})\n\t\t(stroke (width {width:.3f}) (type default))\n\t\t(fill none)\n\t\t(layer "{layer}")\n\t\t(uuid "{uid('rect-'+name) }")\n\t)'''


def zone_box(x1: float, y1: float, x2: float, y2: float, label: str) -> str:
    return rect(x1, y1, x2, y2, "Dwgs.User", 0.35, label) + text(label, (x1+x2)/2, y1+2.4, 1.1, "Dwgs.User")


def placeholder(ref: str, value: str, x: float, y: float, w: float, h: float, pad_style: str = "smd") -> str:
    # Four corner pads make the package visible in fabrication and 3D views.
    pads = []
    for i, (px, py) in enumerate(((-w/2+1, -h/2+1), (w/2-1, -h/2+1),
                                  (w/2-1, h/2-1), (-w/2+1, h/2-1)), 1):
        shape = "roundrect" if pad_style == "smd" else "circle"
        extra = " (roundrect_rratio 0.2)" if pad_style == "smd" else ""
        pads.append(
            f'''\n\t\t(pad "{i}" smd {shape}\n\t\t\t(at {px:.3f} {py:.3f})\n\t\t\t(size 1.200 1.200)\n\t\t\t(layers "F.Cu" "F.Paste" "F.Mask"){extra}\n\t\t\t(uuid "{uid(ref+'-pad-'+str(i))}")\n\t\t)'''
        )
    return f'''\n\t(footprint "Rough:{ref}"\n\t\t(layer "F.Cu")\n\t\t(uuid "{uid(ref)}")\n\t\t(at {x:.3f} {y:.3f})\n\t\t(property "Reference" "{ref}"\n\t\t\t(at 0 {-h/2-1.4:.3f} 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid(ref+'-reference')}")\n\t\t\t(effects (font (size 1.0 1.0) (thickness 0.18)))\n\t\t)\n\t\t(property "Value" "{value}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid(ref+'-value')}")\n\t\t\t(effects (font (size 0.9 0.9) (thickness 0.16)))\n\t\t)\n\t\t(attr smd)\n\t\t(fp_rect\n\t\t\t(start {-w/2:.3f} {-h/2:.3f})\n\t\t\t(end {w/2:.3f} {h/2:.3f})\n\t\t\t(stroke (width 0.3) (type default))\n\t\t\t(fill none)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid(ref+'-outline')}")\n\t\t)\n+{''.join(pads)}\n\t)'''


def finger_footprint() -> str:
    # Approximate 32-bit PCI finger bank.  Geometry is intentionally marked
    # preliminary pending verification against the PCI mechanical drawing.
    pads = []
    pitch = 1.27
    x0 = -38.1
    key_after = 49
    for side, layers, y in (
        ("A", '"F.Cu" "F.Mask"', 0.0),
        ("B", '"B.Cu" "B.Mask"', 0.0),
    ):
        for n in range(1, 61):
            gap = 3.81 if n > key_after else 0.0
            x = x0 + (n - 1) * pitch + gap
            pads.append(
                f'''\n\t\t(pad "{side}{n}" smd roundrect\n\t\t\t(at {x:.3f} {y:.3f})\n\t\t\t(size 0.800 6.000)\n\t\t\t(layers {layers})\n\t\t\t(roundrect_rratio 0.12)\n\t\t\t(uuid "{uid('PCI-'+side+str(n))}")\n\t\t)'''
            )
    return f'''\n\t(footprint "Rough:PCI_32bit_5V_PRELIMINARY"\n\t\t(layer "F.Cu")\n\t\t(uuid "{uid('PCI-edge')}")\n\t\t(at 84.000 123.850)\n\t\t(property "Reference" "J1"\n\t\t\t(at 0 -5.2 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid('PCI-ref')}")\n\t\t\t(effects (font (size 1.0 1.0) (thickness 0.18)))\n\t\t)\n\t\t(property "Value" "PCI 32-bit 5V — VERIFY FINGERS"\n\t\t\t(at 0 -3.6 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid('PCI-value')}")\n\t\t\t(effects (font (size 0.9 0.9) (thickness 0.16)))\n\t\t)\n\t\t(attr board_only exclude_from_pos_files exclude_from_bom)\n+{''.join(pads)}\n\t)'''


HEADER = '''(kicad_pcb
\t(version 20240108)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general (thickness 1.6) (legacy_teardrops no))
\t(paper "A4")
\t(title_block
\t\t(title "Voodoo 2 Artix Card — Rough Placement / Quote Board")
\t\t(date "2026-09-16")
\t\t(rev "0.1-ROUGH")
\t\t(company "Feasibility study — NOT FOR ASSEMBLY")
\t\t(comment 1 "175 x 107 mm, 6 layer, 1.6 mm FR-4")
\t\t(comment 2 "Hard gold edge fingers and 30 degree bevel requested at quote")
\t\t(comment 3 "PCI finger geometry and all footprints are preliminary")
\t)
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(1 "In1.Cu" power)
\t\t(2 "In2.Cu" signal)
\t\t(3 "In3.Cu" signal)
\t\t(4 "In4.Cu" power)
\t\t(31 "B.Cu" signal)
\t\t(32 "B.Adhes" user "B.Adhesive")
\t\t(33 "F.Adhes" user "F.Adhesive")
\t\t(34 "B.Paste" user)
\t\t(35 "F.Paste" user)
\t\t(36 "B.SilkS" user "B.Silkscreen")
\t\t(37 "F.SilkS" user "F.Silkscreen")
\t\t(38 "B.Mask" user)
\t\t(39 "F.Mask" user)
\t\t(44 "Edge.Cuts" user)
\t\t(46 "B.CrtYd" user "B.Courtyard")
\t\t(47 "F.CrtYd" user "F.Courtyard")
\t\t(48 "B.Fab" user)
\t\t(49 "F.Fab" user)
\t\t(40 "Dwgs.User" user "User.Drawings")
\t\t(41 "Cmts.User" user "User.Comments")
\t\t(42 "Eco1.User" user "User.Eco1")
\t\t(43 "Eco2.User" user "User.Eco2")
\t\t(45 "Margin" user)
\t)
\t(setup
\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "prepreg") (thickness 0.18) (material "FR4") (epsilon_r 4.2) (loss_tangent 0.02))
\t\t\t(layer "In1.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 2" (type "core") (thickness 0.43) (material "FR4") (epsilon_r 4.2) (loss_tangent 0.02))
\t\t\t(layer "In2.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 3" (type "prepreg") (thickness 0.34) (material "FR4") (epsilon_r 4.2) (loss_tangent 0.02))
\t\t\t(layer "In3.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 4" (type "core") (thickness 0.43) (material "FR4") (epsilon_r 4.2) (loss_tangent 0.02))
\t\t\t(layer "In4.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 5" (type "prepreg") (thickness 0.18) (material "FR4") (epsilon_r 4.2) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "Green") (thickness 0.01))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen"))
\t\t\t(copper_finish "Hard gold")
\t\t\t(dielectric_constraints yes)
\t\t)
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t)
\t(net 0 "")
'''


def build() -> str:
    out = [HEADER]

    # 175 x 107 mm full-height card envelope, coordinates 20..195 / 20..127.
    out += [
        line(20, 20, 195, 20, "Edge.Cuts", 0.1, "top"),
        line(195, 20, 195, 127, "Edge.Cuts", 0.1, "right"),
        line(195, 127, 149.23, 127, "Edge.Cuts", 0.1, "bottom-right"),
        line(145.42, 127, 20, 127, "Edge.Cuts", 0.1, "bottom-left"),
        line(149.23, 127, 149.23, 124.2, "Edge.Cuts", 0.1, "key-r"),
        line(149.23, 124.2, 145.42, 124.2, "Edge.Cuts", 0.1, "key-top"),
        line(145.42, 124.2, 145.42, 127, "Edge.Cuts", 0.1, "key-l"),
        line(20, 127, 20, 20, "Edge.Cuts", 0.1, "left"),
    ]

    # Functional placement regions.
    out += [
        zone_box(23, 23, 46, 117, "BRACKET I/O"),
        zone_box(50, 23, 88, 79, "T113 SERVICE DOMAIN"),
        zone_box(91, 23, 149, 96, "FPGA + VIDEO MEMORY"),
        zone_box(152, 23, 191, 117, "POWER + DDR3"),
        zone_box(50, 99, 145, 117, "PCI FRONT END"),
        text("VOODOO 2 ARTIX — REV 0.1 ROUGH / NOT FOR ASSEMBLY", 107.5, 21.9, 1.45),
        text("6 LAYERS • 175 x 107 mm • 1.6 mm FR-4", 107.5, 125.0, 1.05),
        text("HARD GOLD + 30° BEVEL AT PCI EDGE", 107.5, 122.9, 0.95),
        text("DNP V1", 61, 79.8, 1.0),
    ]

    # Bracket-facing connectors and their nearby video circuitry.
    out += [
        placeholder("J2", "VGA IN", 31.5, 36, 18, 11),
        placeholder("J3", "VGA OUT", 31.5, 55, 18, 11),
        placeholder("J4", "HDMI OUT", 31.5, 72, 17, 6),
        placeholder("J5", "USB HOST", 31.5, 87, 15, 8),
        placeholder("J6", "100M ETHERNET", 31.5, 105, 17, 14),
        placeholder("U7", "TVP7002 ADC DNP", 61, 69, 16, 16),
        placeholder("U6", "ADV7123 DAC", 82, 87, 14, 14),
        placeholder("K1", "G6K VGA", 54, 88, 6.5, 10),
        placeholder("K2", "G6K VGA", 62, 88, 6.5, 10),
        placeholder("K3", "G6K VGA", 70, 88, 6.5, 10),
    ]

    # Linux companion domain.
    out += [
        placeholder("U2", "ALLWINNER T113-S3", 69, 47, 18, 18),
        placeholder("U3", "SPI NAND", 56, 31, 10, 8),
        placeholder("U4", "ETH PHY", 79, 31, 11, 9),
        placeholder("Y2", "24MHz XTAL", 55, 58, 7, 5),
        placeholder("J7", "UART DEBUG", 81, 58, 10, 6),
    ]

    # FPGA and memory cluster.
    out += [
        placeholder("U1", "XC7A200T-FBG484", 119, 54, 23, 23),
        placeholder("U8", "DDR3L x16", 142, 46, 14, 9),
        placeholder("U9", "DDR3L x16", 142, 61, 14, 9),
        placeholder("U10", "QSPI 128M", 104, 76, 9, 7),
        placeholder("Y1", "50MHz OSC", 118, 76, 7, 5),
        placeholder("U11", "ISSI HYPERRAM", 99, 89, 9, 7),
        placeholder("U12", "ISSI HYPERRAM", 111, 89, 9, 7),
        placeholder("U13", "ISSI HYPERRAM", 123, 89, 9, 7),
        placeholder("U14", "ISSI HYPERRAM", 135, 89, 9, 7),
    ]

    # Power and PCI bridge cluster.
    out += [
        placeholder("U20", "TPS62480 1.0V", 164, 35, 12, 9),
        placeholder("L20", "0.47uH", 160, 43, 4, 4),
        placeholder("L21", "0.47uH", 168, 43, 4, 4),
        placeholder("U21", "1.8V BUCK", 180, 35, 12, 9),
        placeholder("U22", "1.35V BUCK", 164, 51, 12, 9),
        placeholder("U23", "3.3V BUCK", 180, 51, 12, 9),
        placeholder("U24", "DDR VTT", 164, 67, 12, 9),
        placeholder("L1", "POWER FILTER", 180, 67, 12, 9),
        placeholder("U15", "SN74CB3T16210", 74, 108, 18, 7),
        placeholder("U16", "SN74CB3T16210", 98, 108, 18, 7),
        placeholder("U17", "SN74CB3T16210", 122, 108, 18, 7),
        placeholder("TP1", "+5V SLOT TEST", 160, 108, 10, 6),
        placeholder("TP2", "RAIL TEST", 178, 108, 12, 6),
        finger_footprint(),
    ]

    # Placement arrows/intent only, on user drawings so they do not enter fab copper.
    for i, (x1, y1, x2, y2) in enumerate((
        (46, 36, 53, 60), (46, 55, 52, 82), (88, 47, 106, 51),
        (130, 54, 135, 54), (119, 66, 119, 84), (98, 99, 98, 113),
        (152, 51, 149, 54),
    )):
        out.append(line(x1, y1, x2, y2, "Dwgs.User", 0.45, f"flow-{i}"))

    out.append("\n)\n")
    # The footprint templates use a leading '+' as a readable splice marker;
    # strip it from the generated S-expression.
    return "".join(out).replace("\n+", "\n")


if __name__ == "__main__":
    OUT.write_text(build())
    print(OUT)
