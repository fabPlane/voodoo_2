# Connector mechanical clearance — rev A pricing board

Checked 2026-09-17 against the generated KiCad board using actual library
footprints rather than four-pad placement placeholders.

## Single-slot bracket arrangement

Rev A uses one bidirectional VGA connector. The FPGA-controlled analog path
selects either VGA capture through the video ADC or VGA output through the
video DAC. HDMI remains output-only.

The usable vertical board edge is approximately 99.44 mm. The four external
connector courtyard heights are:

| Connector | Courtyard height on bracket |
|---|---:|
| Bidirectional VGA, HD15 | 31.91 mm |
| HDMI-A | 18.55 mm |
| USB-A | 14.35 mm |
| RJ45 magjack | 19.05 mm |
| **Total without gaps** | **83.86 mm** |

Three 2.00 mm gaps bring the occupied stack to 89.86 mm, leaving approximately
9.58 mm of additional vertical margin on the usable bracket edge.

## Verified positions

| Connector | Location | Bounding box / clearance result |
|---|---|---|
| J2 bidirectional VGA | left bracket | Y 22.00–53.91 mm |
| J4 HDMI-A output | left bracket | Y 55.91–74.46 mm; 2.00 mm from J2 |
| J5 USB-A host | left bracket | Y 76.46–90.80 mm; 2.00 mm from J4 |
| J6 Ethernet | left bracket | Y 92.80–111.84 mm; 2.00 mm from J5 |
| J11 USB-C service | right edge | Y 94.66–105.34 mm; no component overlap |

`validate_connector_clearance.py` runs automatically after board generation
and fails if a connector overlaps another footprint, a bracket gap drops below
0.75 mm, or the bracket stack exceeds the usable board edge.

The VGA, HDMI, USB-A and USB-C bodies are mechanically similar KiCad-library
parts because exact manufacturer STEP models are not installed. J6 uses the
exact HANRUN HR911105A footprint and a size-compatible Molex body. Manufacturer
drawings still control bracket holes and final release.
