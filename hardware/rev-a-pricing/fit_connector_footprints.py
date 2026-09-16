#!/usr/bin/env python3
"""Replace quote placeholders with mechanically meaningful connector footprints.

Run with KiCad's bundled Python so pcbnew is available.  Rev A uses one
bidirectional HD15 followed by HDMI-A, USB-A and RJ45 on the PCI bracket edge.
Their courtyard heights plus 2 mm gaps fit inside the usable 99.44 mm edge.
"""

from pathlib import Path
import sys

import pcbnew


BOARD = Path(sys.argv[1]).resolve()
KICAD = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport")
FOOTPRINTS = KICAD / "footprints"


def mm(x: float, y: float) -> pcbnew.VECTOR2I:
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def load(lib: str, name: str, ref: str, value: str,
         x: float, y: float, rotation: float) -> pcbnew.FOOTPRINT:
    fp = pcbnew.FootprintLoad(str(FOOTPRINTS / f"{lib}.pretty"), name)
    if fp is None:
        raise RuntimeError(f"Unable to load {lib}:{name}")
    fp.SetReference(ref)
    fp.SetValue(value)
    fp.SetPosition(mm(x, y))
    fp.SetOrientationDegrees(rotation)
    return fp


def use_molex_rj45_body(fp: pcbnew.FOOTPRINT) -> None:
    """Use a close envelope model because KiCad lacks the HANRUN STEP body."""
    fp.Models().clear()
    body = pcbnew.FP_3DMODEL()
    body.m_Filename = (
        "${KICAD10_3DMODEL_DIR}/Connector_RJ.3dshapes/"
        "RJ45_Molex_9346520x_Horizontal.step"
    )
    body.m_Offset = pcbnew.VECTOR3D(0.0, 3.9, 0.0)
    body.m_Scale = pcbnew.VECTOR3D(1.0, 1.0, 1.0)
    body.m_Rotation = pcbnew.VECTOR3D(0.0, 0.0, 0.0)
    fp.Add3DModel(body)


def bounds(fp: pcbnew.FOOTPRINT) -> tuple[float, float, float, float]:
    box = fp.GetBoundingBox(False, False)
    return tuple(
        pcbnew.ToMM(value)
        for value in (box.GetX(), box.GetY(), box.GetRight(), box.GetBottom())
    )


def overlaps(a: tuple[float, float, float, float],
             b: tuple[float, float, float, float]) -> bool:
    return (
        min(a[2], b[2]) - max(a[0], b[0]) > 0.01
        and min(a[3], b[3]) - max(a[1], b[1]) > 0.01
    )


def main() -> None:
    # Full-height single-slot bracket, top to bottom: one bidirectional VGA,
    # HDMI-A, USB-A and Ethernet.  Adjacent courtyards have 2 mm clearance.
    # The VGA footprint faces outward across the left board edge; 90 degrees
    # points its mating face into the card, so the correct orientation is 270.
    connectors = [
        load(
            "Connector_Dsub",
            "DSUB-15-HD_Socket_Horizontal_P2.29x1.90mm_EdgePinOffset3.03mm_Housed_MountingHolesOffset4.94mm",
            "J2", "KH-HDR15P-F3.08 VGA BIDIRECTIONAL (mechanical equivalent)",
            26.83, 42.27, 270.0,
        ),
        load(
            "Connector_Video",
            "HDMI_A_Amphenol_10029449-x01xLF_Horizontal",
            "J4", "HDMI-019S HDMI OUT (mechanical equivalent)",
            23.50, 65.18, 90.0,
        ),
        load(
            "Connector_USB",
            "USB_A_CNCTech_1001-011-01101_Horizontal",
            "J5", "902-131A1021D10100 USB HOST (mechanical equivalent)",
            23.80, 83.63, 0.0,
        ),
        load(
            "Connector_RJ",
            "RJ45_Hanrun_HR911105A_Horizontal",
            "J6", "HR911105A 100M ETHERNET",
            37.76, 106.76, 90.0,
        ),
        load(
            "Connector_USB",
            "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
            "J11", "TYPE-C-31-M-12 MCU USB (mechanical equivalent)",
            190.955, 100.0, -90.0,
        ),
    ]
    use_molex_rj45_body(connectors[3])
    connector_ref_order = ["J2", "J4", "J5", "J6", "J11"]
    connector_bounds = {
        ref: bounds(connector)
        for ref, connector in zip(connector_ref_order, connectors)
    }

    board = pcbnew.LoadBoard(str(BOARD))
    replaced = {"J2", "J3", "J4", "J5", "J6", "J11"}
    retained: list[pcbnew.FOOTPRINT] = []
    for footprint in list(board.GetFootprints()):
        if footprint.GetReference() in replaced:
            board.Remove(footprint)
        else:
            retained.append(footprint)
    bracket_bounds = [connector_bounds[ref] for ref in ("J2", "J4", "J5", "J6")]
    for upper, lower in zip(bracket_bounds, bracket_bounds[1:]):
        if lower[1] - upper[3] < 0.75:
            raise RuntimeError("Bracket connector courtyard gap is below 0.75 mm")
    if bracket_bounds[0][1] < 20.0 or bracket_bounds[-1][3] > 119.44:
        raise RuntimeError("Bracket connector stack exceeds the usable board edge")

    for connector in connectors:
        board.Add(connector)

    pcbnew.SaveBoard(str(BOARD), board)


if __name__ == "__main__":
    main()
