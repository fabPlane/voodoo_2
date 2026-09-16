#!/usr/bin/env python3
"""Generate the Rev-B mechanical/placement feasibility board.

This intentionally produces a quote-grade, unrouted placement study.  It is not
an electrically complete design and must not be ordered as an assembled board.
"""

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


OUT = Path(__file__).with_name("voodoo2_artix_rough.kicad_pcb")

# PCI Local Bus 2.x full-height 5 V card envelope and connector datum.
# The contact pitch, keyed A/B50-A/B51 gap, and finger geometry follow the PCI
# mechanical drawing and KiCad's Connector_PCBEdge:BUS_PCI footprint.
BOARD_LEFT = 20.0
BOARD_TOP = 20.0
BOARD_WIDTH = 174.63
BOARD_HEIGHT = 106.68
BOARD_RIGHT = BOARD_LEFT + BOARD_WIDTH
BOARD_BOTTOM = BOARD_TOP + BOARD_HEIGHT
BOARD_BODY_BOTTOM = BOARD_BOTTOM - 7.24
PCI_B1_X = BOARD_LEFT + 12.70
PCI_ORIGIN_X = PCI_B1_X + 38.73
PCI_ORIGIN_Y = BOARD_BOTTOM - 3.37
PCI_TAB_LEFT = PCI_ORIGIN_X - 39.62
PCI_TAB_RIGHT = PCI_ORIGIN_X + 39.62
PCI_TAB_CORNER_RADIUS = 0.381
PCI_KEY_LEFT = PCI_ORIGIN_X + 23.24
PCI_KEY_RIGHT = PCI_ORIGIN_X + 25.02
PCI_KEY_CENTER_X = PCI_ORIGIN_X + 24.13
PCI_KEY_SHOULDER_Y = PCI_ORIGIN_Y - 4.00
PCI_KEY_TOP_Y = PCI_ORIGIN_Y - 4.89
PCI_KEY_LEFT_EDGE = PCI_ORIGIN_X + 22.864
PCI_KEY_RIGHT_EDGE = PCI_ORIGIN_X + 25.401
PCI_KEY_CORNER_Y = PCI_ORIGIN_Y + 2.989


def uid(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"voodoo2-artix-rough/{name}"))


def text(value: str, x: float, y: float, size: float = 1.5, layer: str = "F.SilkS", justify: str = "") -> str:
    j = f" (justify {justify})" if justify else ""
    return f'''\n\t(gr_text "{value}"\n\t\t(at {x:.3f} {y:.3f})\n\t\t(layer "{layer}")\n\t\t(uuid "{uid('text-'+value+str(x)+str(y))}")\n\t\t(effects (font (size {size:.3f} {size:.3f}) (thickness 0.25)){j})\n\t)'''


def line(x1: float, y1: float, x2: float, y2: float, layer: str, width: float = 0.25, name: str = "") -> str:
    return f'''\n\t(gr_line\n\t\t(start {x1:.3f} {y1:.3f})\n\t\t(end {x2:.3f} {y2:.3f})\n\t\t(stroke (width {width:.3f}) (type default))\n\t\t(layer "{layer}")\n\t\t(uuid "{uid('line-'+name+str(x1)+str(y1)+str(x2)+str(y2))}")\n\t)'''


def arc(x1: float, y1: float, xm: float, ym: float, x2: float, y2: float,
        layer: str, width: float = 0.25, name: str = "") -> str:
    return f'''\n\t(gr_arc\n\t\t(start {x1:.3f} {y1:.3f})\n\t\t(mid {xm:.3f} {ym:.3f})\n\t\t(end {x2:.3f} {y2:.3f})\n\t\t(stroke (width {width:.3f}) (type default))\n\t\t(layer "{layer}")\n\t\t(uuid "{uid('arc-'+name+str(x1)+str(y1)+str(x2)+str(y2))}")\n\t)'''


def rect(x1: float, y1: float, x2: float, y2: float, layer: str = "F.SilkS", width: float = 0.3, name: str = "") -> str:
    return f'''\n\t(gr_rect\n\t\t(start {x1:.3f} {y1:.3f})\n\t\t(end {x2:.3f} {y2:.3f})\n\t\t(stroke (width {width:.3f}) (type default))\n\t\t(fill none)\n\t\t(layer "{layer}")\n\t\t(uuid "{uid('rect-'+name) }")\n\t)'''


def zone_box(x1: float, y1: float, x2: float, y2: float, label: str) -> str:
    return rect(x1, y1, x2, y2, "Dwgs.User", 0.35, label) + text(label, (x1+x2)/2, y1+2.4, 1.1, "Dwgs.User")


def model(path: str, rotate_z: float = 0, offset_x: float = 0, offset_y: float = 0) -> str:
    """Attach a stock KiCad STEP body to a quote-grade placeholder."""
    return f'''
		(model "${{KICAD10_3DMODEL_DIR}}/{path}"
			(offset (xyz {offset_x:.3f} {offset_y:.3f} 0))
			(scale (xyz 1 1 1))
			(rotate (xyz 0 0 {rotate_z:.3f}))
		)'''


def placeholder_model(ref: str) -> tuple[str, float, float, float] | None:
    """Return package-level 3D art for the currently selected BOM family."""
    by_ref: dict[str, tuple[str, float, float, float]] = {
        "J2": ("Connector_Dsub.3dshapes/DSUB-15-HD_Socket_Horizontal_P2.29x1.90mm_EdgePinOffset3.03mm_Housed_MountingHolesOffset4.94mm.step", 90, 0, 0),
        "J3": ("Connector_Dsub.3dshapes/DSUB-15-HD_Socket_Horizontal_P2.29x1.90mm_EdgePinOffset3.03mm_Housed_MountingHolesOffset4.94mm.step", 90, 0, 0),
        "J4": ("Connector_Video.3dshapes/HDMI_A_Amphenol_10029449-x01xLF_Horizontal.step", 90, 0, 12.0),
        "J5": ("Connector_USB.3dshapes/USB_A_CNCTech_1001-011-01101_Horizontal.step", 0, -5.0, 0),
        "J6": ("Connector_RJ.3dshapes/RJ45_Molex_9346520x_Horizontal.step", 90, 0, -7.5),
        "J7": ("Connector_JST.3dshapes/JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal.step", 0, 0, 0),
        "J8": ("Connector_PinHeader_2.00mm.3dshapes/PinHeader_2x07_P2.00mm_Vertical_SMD.step", 90, 0, 0),
        "J9": ("Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x08_P2.54mm_Vertical.step", 90, 0, 0),
        "J11": ("Connector_USB.3dshapes/USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.step", -90, 0, 0.955),
        "U1": ("Package_BGA.3dshapes/BGA-484_23.0x23.0mm_Layout22x22_P1.0mm.step", 0, 0, 0),
        "U2": ("Package_QFP.3dshapes/LQFP-128_14x14mm_P0.4mm.step", 0, 0, 0),
        "U3": ("Package_SON.3dshapes/WSON-8-1EP_6x5mm_P1.27mm_EP3.4x4.3mm.step", 0, 0, 0),
        "U4": ("Package_DFN_QFN.3dshapes/QFN-32-1EP_5x5mm_P0.5mm_EP3.3x3.3mm.step", 0, 0, 0),
        "U6": ("Package_QFP.3dshapes/LQFP-48_7x7mm_P0.5mm.step", 0, 0, 0),
        "U7": ("Package_QFP.3dshapes/TQFP-100_14x14mm_P0.5mm.step", 0, 0, 0),
        "U8": ("Package_BGA.3dshapes/BGA-96_9.0x13.0mm_Layout2x3x16_P0.8mm.step", 90, 0, 0),
        "U9": ("Package_BGA.3dshapes/BGA-96_9.0x13.0mm_Layout2x3x16_P0.8mm.step", 90, 0, 0),
        "U10": ("Package_SO.3dshapes/SOIC-8_5.3x5.3mm_P1.27mm.step", 0, 0, 0),
        "U11": ("Package_BGA.3dshapes/BGA-24_8x8mm_Layout5x5_P1.0mm.step", 0, 0, 0),
        "U12": ("Package_BGA.3dshapes/BGA-24_8x8mm_Layout5x5_P1.0mm.step", 0, 0, 0),
        "U13": ("Package_BGA.3dshapes/BGA-24_8x8mm_Layout5x5_P1.0mm.step", 0, 0, 0),
        "U14": ("Package_BGA.3dshapes/BGA-24_8x8mm_Layout5x5_P1.0mm.step", 0, 0, 0),
        "U15": ("Package_SO.3dshapes/TSSOP-56_6.1x14mm_P0.5mm.step", 90, 0, 0),
        "U16": ("Package_SO.3dshapes/TSSOP-56_6.1x14mm_P0.5mm.step", 90, 0, 0),
        "U17": ("Package_SO.3dshapes/TSSOP-56_6.1x14mm_P0.5mm.step", 90, 0, 0),
        "U20": ("Package_DFN_QFN.3dshapes/Texas_RGE0024C_VQFN-24-1EP_4x4mm_P0.5mm_EP2.1x2.1mm.step", 0, 0, 0),
        "U21": ("Package_DFN_QFN.3dshapes/WQFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm.step", 0, 0, 0),
        "U22": ("Package_DFN_QFN.3dshapes/WQFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm.step", 0, 0, 0),
        "U23": ("Package_DFN_QFN.3dshapes/WQFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm.step", 0, 0, 0),
        "U24": ("Package_SON.3dshapes/VSON-10-1EP_3x3mm_P0.5mm_EP1.2x2mm.step", 0, 0, 0),
        "L20": ("Inductor_SMD.3dshapes/L_Cenker_CKCS252012.step", 0, 0, 0),
        "L21": ("Inductor_SMD.3dshapes/L_Cenker_CKCS252012.step", 0, 0, 0),
        "Y1": ("Oscillator.3dshapes/Oscillator_SMD_Abracon_ASV-4Pin_7.0x5.1mm.step", 0, 0, 0),
        "Y2": ("Crystal.3dshapes/Crystal_SMD_3225-4Pin_3.2x2.5mm.step", 0, 0, 0),
        "K1": ("Relay_SMD.3dshapes/Relay_DPDT_Omron_G6K-2G-Y.step", 0, 0, 0),
        "K2": ("Relay_SMD.3dshapes/Relay_DPDT_Omron_G6K-2G-Y.step", 0, 0, 0),
        "K3": ("Relay_SMD.3dshapes/Relay_DPDT_Omron_G6K-2G-Y.step", 0, 0, 0),
        "TP1": ("TestPoint.3dshapes/TestPoint_Keystone_5005-5009_Compact.step", 0, 0, 0),
        "TP2": ("TestPoint.3dshapes/TestPoint_Keystone_5005-5009_Compact.step", 0, 0, 0),
        "Q1": ("Package_TO_SOT_SMD.3dshapes/SOT-23.step", 0, 0, 0),
        "Q2": ("Package_TO_SOT_SMD.3dshapes/SOT-23.step", 0, 0, 0),
        "Q3": ("Package_TO_SOT_SMD.3dshapes/SOT-23.step", 0, 0, 0),
        "U30": ("Package_TO_SOT_SMD.3dshapes/SOT-23-6.step", 0, 0, 0),
        "U31": ("Package_TO_SOT_SMD.3dshapes/SOT-23-6.step", 0, 0, 0),
        "U32": ("Package_TO_SOT_SMD.3dshapes/SOT-23-6.step", 0, 0, 0),
        "U33": ("Package_TO_SOT_SMD.3dshapes/SOT-23-6.step", 0, 0, 0),
        "U34": ("Package_TO_SOT_SMD.3dshapes/SOT-23-6.step", 0, 0, 0),
        "U35": ("Package_TO_SOT_SMD.3dshapes/SOT-23-6.step", 0, 0, 0),
        "L22": ("Inductor_SMD.3dshapes/L_Chilisin_BWVS00404018.step", 0, 0, 0),
        "L23": ("Inductor_SMD.3dshapes/L_Chilisin_BWVS00404018.step", 0, 0, 0),
        "L24": ("Inductor_SMD.3dshapes/L_Chilisin_BWVS00404018.step", 0, 0, 0),
        "L25": ("Inductor_SMD.3dshapes/L_Cenker_CKCS4018.step", 0, 0, 0),
        "F1": ("Fuse.3dshapes/Fuse_1210_3225Metric.step", 0, 0, 0),
        "Q10": ("Package_DFN_QFN.3dshapes/QFN-16-1EP_4x4mm_P0.65mm_EP2.5x2.5mm.step", 0, 0, 0),
        "U40": ("Package_DFN_QFN.3dshapes/ArtInChip_QFN-88-1EP_10x10mm_P0.4mm_EP6.74x6.74mm.step", 0, 0, 0),
        "U41": ("Package_SO.3dshapes/SOIC-8_5.3x5.3mm_P1.27mm.step", 0, 0, 0),
        "SW1": ("Button_Switch_SMD.3dshapes/SW_SPST_TL3342.step", 0, 0, 0),
    }
    return by_ref.get(ref)


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
    model_spec = placeholder_model(ref)
    if model_spec:
        pads.append(model(*model_spec))
    return f'''\n\t(footprint "Rough:{ref}"\n\t\t(layer "F.Cu")\n\t\t(uuid "{uid(ref)}")\n\t\t(at {x:.3f} {y:.3f})\n\t\t(property "Reference" "{ref}"\n\t\t\t(at 0 {-h/2-1.4:.3f} 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid(ref+'-reference')}")\n\t\t\t(effects (font (size 1.0 1.0) (thickness 0.18)))\n\t\t)\n\t\t(property "Value" "{value}"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid(ref+'-value')}")\n\t\t\t(effects (font (size 0.9 0.9) (thickness 0.16)))\n\t\t)\n\t\t(attr smd)\n\t\t(fp_rect\n\t\t\t(start {-w/2:.3f} {-h/2:.3f})\n\t\t\t(end {w/2:.3f} {h/2:.3f})\n\t\t\t(stroke (width 0.3) (type default))\n\t\t\t(fill none)\n\t\t\t(layer "F.SilkS")\n\t\t\t(uuid "{uid(ref+'-outline')}")\n\t\t)\n+{''.join(pads)}\n\t)'''


def finger_footprint() -> str:
    # Conventional 32-bit, 5 V PCI is a 2 x 60 contact connector whose
    # positions are numbered A/B1..62; positions 50 and 51 form the 5 V key.
    pads = []
    x_by_pin = {
        pin: -38.73 + (pin - 1) * 1.27
        for pin in range(1, 63)
        if pin not in (50, 51)
    }
    for side, layers in (
        ("A", '"B.Cu" "B.Mask"'),
        ("B", '"F.Cu" "F.Mask"'),
    ):
        for pin, x in x_by_pin.items():
            pads.append(
                f'''\n\t\t(pad "{side}{pin}" connect rect\n\t\t\t(at {x:.3f} -1.330)\n\t\t\t(size 1.020 4.570)\n\t\t\t(layers {layers})\n\t\t\t(uuid "{uid('PCI-'+side+str(pin)+'-main')}")\n\t\t)\n\t\t(pad "{side}{pin}" connect rect\n\t\t\t(at {x:.3f} 1.330)\n\t\t\t(size 0.510 0.760)\n\t\t\t(layers {layers})\n\t\t\t(uuid "{uid('PCI-'+side+str(pin)+'-tail')}")\n\t\t)'''
            )
    return f'''\n\t(footprint "Rough:PCI_32bit_5V_PCI2X"\n\t\t(layer "F.Cu")\n\t\t(uuid "{uid('PCI-edge')}")\n\t\t(at {PCI_ORIGIN_X:.3f} {PCI_ORIGIN_Y:.3f})\n\t\t(property "Reference" "J1"\n\t\t\t(at 0 -5.2 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(hide yes)\n\t\t\t(uuid "{uid('PCI-ref')}")\n\t\t\t(effects (font (size 1.0 1.0) (thickness 0.18)))\n\t\t)\n\t\t(property "Value" "PCI 32-bit 5V — PCI 2.x MECHANICAL"\n\t\t\t(at 0 -3.6 0)\n\t\t\t(layer "F.SilkS")\n\t\t\t(hide yes)\n\t\t\t(uuid "{uid('PCI-value')}")\n\t\t\t(effects (font (size 0.9 0.9) (thickness 0.16)))\n\t\t)\n\t\t(attr board_only exclude_from_pos_files exclude_from_bom)\n+{''.join(pads)}\n\t)'''


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
\t\t(comment 1 "174.63 x 106.68 mm, 6 layer, 1.6 mm FR-4")
\t\t(comment 2 "Hard gold edge fingers and 30 degree bevel requested at quote")
\t\t(comment 3 "PCI 2.x 5 V keyed mechanical; electrical design remains preliminary")
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

    # PCI 2.x full-height 5 V short-card envelope. The single slot is the
    # 5 V key between contact positions 49 and 52.
    out += [
        line(BOARD_LEFT, BOARD_TOP, BOARD_RIGHT, BOARD_TOP, "Edge.Cuts", 0.1, "top"),
        line(BOARD_RIGHT, BOARD_TOP, BOARD_RIGHT, BOARD_BODY_BOTTOM, "Edge.Cuts", 0.1, "right"),
        line(BOARD_RIGHT, BOARD_BODY_BOTTOM, PCI_TAB_RIGHT, BOARD_BODY_BOTTOM,
             "Edge.Cuts", 0.1, "body-bottom-right"),
        line(PCI_TAB_RIGHT, BOARD_BODY_BOTTOM, PCI_TAB_RIGHT,
             BOARD_BOTTOM - PCI_TAB_CORNER_RADIUS, "Edge.Cuts", 0.1, "tab-r"),
        arc(PCI_TAB_RIGHT, BOARD_BOTTOM - PCI_TAB_CORNER_RADIUS,
            PCI_TAB_RIGHT - 0.112, BOARD_BOTTOM - 0.112,
            PCI_TAB_RIGHT - PCI_TAB_CORNER_RADIUS, BOARD_BOTTOM,
            "Edge.Cuts", 0.1, "tab-corner-r"),
        line(PCI_TAB_RIGHT - PCI_TAB_CORNER_RADIUS, BOARD_BOTTOM,
             PCI_KEY_RIGHT_EDGE, BOARD_BOTTOM, "Edge.Cuts", 0.1, "bottom-right"),
        arc(PCI_KEY_RIGHT_EDGE, BOARD_BOTTOM, PCI_ORIGIN_X + 25.132, PCI_ORIGIN_Y + 3.258,
            PCI_KEY_RIGHT, PCI_KEY_CORNER_Y, "Edge.Cuts", 0.1, "key-corner-r"),
        line(PCI_KEY_RIGHT, PCI_KEY_CORNER_Y, PCI_KEY_RIGHT, PCI_KEY_SHOULDER_Y,
             "Edge.Cuts", 0.1, "key-r"),
        arc(PCI_KEY_RIGHT, PCI_KEY_SHOULDER_Y, PCI_KEY_CENTER_X, PCI_KEY_TOP_Y,
            PCI_KEY_LEFT, PCI_KEY_SHOULDER_Y, "Edge.Cuts", 0.1, "key-cap"),
        line(PCI_KEY_LEFT, PCI_KEY_SHOULDER_Y, PCI_KEY_LEFT, PCI_KEY_CORNER_Y,
             "Edge.Cuts", 0.1, "key-l"),
        arc(PCI_KEY_LEFT, PCI_KEY_CORNER_Y, PCI_ORIGIN_X + 23.133, PCI_ORIGIN_Y + 3.258,
            PCI_KEY_LEFT_EDGE, BOARD_BOTTOM, "Edge.Cuts", 0.1, "key-corner-l"),
        line(PCI_KEY_LEFT_EDGE, BOARD_BOTTOM,
             PCI_TAB_LEFT + PCI_TAB_CORNER_RADIUS, BOARD_BOTTOM,
             "Edge.Cuts", 0.1, "bottom-left"),
        arc(PCI_TAB_LEFT + PCI_TAB_CORNER_RADIUS, BOARD_BOTTOM,
            PCI_TAB_LEFT + 0.112, BOARD_BOTTOM - 0.112,
            PCI_TAB_LEFT, BOARD_BOTTOM - PCI_TAB_CORNER_RADIUS,
            "Edge.Cuts", 0.1, "tab-corner-l"),
        line(PCI_TAB_LEFT, BOARD_BOTTOM - PCI_TAB_CORNER_RADIUS,
             PCI_TAB_LEFT, BOARD_BODY_BOTTOM, "Edge.Cuts", 0.1, "tab-l"),
        line(PCI_TAB_LEFT, BOARD_BODY_BOTTOM, BOARD_LEFT, BOARD_BODY_BOTTOM,
             "Edge.Cuts", 0.1, "body-bottom-left"),
        line(BOARD_LEFT, BOARD_BODY_BOTTOM, BOARD_LEFT, BOARD_TOP, "Edge.Cuts", 0.1, "left"),
    ]

    # Functional placement regions.
    out += [
        zone_box(23, 23, 46, 117, "BRACKET I/O"),
        zone_box(50, 23, 88, 79, "T113 SERVICE DOMAIN"),
        zone_box(91, 23, 149, 96, "FPGA + VIDEO MEMORY"),
        zone_box(152, 23, 191, 117, "POWER + DDR3"),
        zone_box(50, 99, 145, 117, "PCI FRONT END"),
        text("VOODOO 2 ARTIX — REV 0.1 ROUGH / NOT FOR ASSEMBLY", 107.5, 21.9, 1.45),
        text("6 LAYERS • 174.63 x 106.68 mm • 1.6 mm FR-4", 107.5, 125.0, 1.05, "Cmts.User"),
        text("HARD GOLD + 30° BEVEL AT PCI EDGE", 107.5, 122.9, 0.95, "Cmts.User"),
        text("DNP V1", 61, 79.8, 1.0),
    ]

    # Bracket-facing connectors and their nearby video circuitry.
    out += [
        placeholder("J2", "VGA IN", 31.5, 32, 18, 11),
        placeholder("J3", "VGA OUT", 31.5, 56, 18, 11),
        placeholder("J4", "HDMI OUT", 31.5, 76, 17, 6),
        placeholder("J5", "USB HOST", 31.5, 91, 15, 8),
        placeholder("J6", "100M ETHERNET", 31.5, 105, 17, 14),
        placeholder("U7", "TVP7002 ADC DNP", 61, 69, 16, 16),
        placeholder("U6", "ADV7123 DAC", 82, 87, 14, 14),
        placeholder("K1", "G6K VGA", 60, 88, 6.5, 10),
        placeholder("K2", "G6K VGA", 68, 88, 6.5, 10),
        placeholder("K3", "G6K VGA", 76, 88, 6.5, 10),
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
        placeholder("U8", "DDR3L x16", 142, 48, 14, 9),
        placeholder("U9", "DDR3L x16", 142, 63, 14, 9),
        placeholder("U10", "QSPI 128M", 104, 76, 9, 7),
        placeholder("Y1", "50MHz OSC", 118, 76, 7, 5),
        placeholder("U11", "ISSI HYPERRAM", 99, 89, 9, 7),
        placeholder("U12", "ISSI HYPERRAM", 111, 89, 9, 7),
        placeholder("U13", "ISSI HYPERRAM", 123, 89, 9, 7),
        placeholder("U14", "ISSI HYPERRAM", 135, 89, 9, 7),
    ]

    # Power and PCI bridge cluster.
    out += [
        placeholder("U20", "TPS62480 1.0V", 154.5, 35, 12, 9),
        placeholder("L20", "0.47uH", 153, 43, 4, 4),
        placeholder("L21", "0.47uH", 158.5, 43, 4, 4),
        placeholder("U21", "1.8V BUCK", 188, 50, 12, 9),
        placeholder("U22", "1.35V BUCK", 158, 55, 12, 9),
        placeholder("U23", "3.3V BUCK", 176, 60, 12, 9),
        placeholder("U24", "DDR VTT", 158, 70, 12, 9),
        placeholder("L1", "POWER FILTER", 180, 72, 12, 9),
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
