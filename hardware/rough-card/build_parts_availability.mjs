import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.resolve("outputs/01a0a9af-6eae-7e02-8e64-a55eea3bb977");
await fs.mkdir(outputDir, { recursive: true });

const rows = [
  ["U1", 1, "XC7A200T-2FBG484 FPGA", "Verified in stock", "XC7A200T-2FBG484I", "C494680", 1116, 135.7604, "Lock industrial/commercial grade and confirm JLC BGA assembly eligibility.", "https://www.lcsc.com/product-detail/C494680.html"],
  ["U2", 1, "Allwinner T113-S3", "Verified in stock", "T113-S3", "C5197687", 2444, 22.9138, "Use the ELQFP-128 footprint from the manufacturer drawing.", "https://www.lcsc.com/product-detail/C5197687.html"],
  ["U3", 1, "SPI NAND, density/package TBD", "Verified in stock", "GD5F1GQ5UEYIGR", "C2829047", 2721, 8.1515, "Concrete 1 Gbit, 3.3 V SPI NAND candidate. Check Linux/U-Boot ECC support.", "https://www.lcsc.com/brand/1288-457.html"],
  ["U4", 1, "100BASE-TX Ethernet PHY", "Verified in stock", "RTL8201F-VB-CG", "C45044", 38498, 0.4933, "Use RMII and verify the T113 reference clock direction and strap plan.", "https://www.lcsc.com/product-detail/C45044.html"],
  ["U6", 1, "ADV7123 video DAC", "Verified in stock", "ADV7123KSTZ50", "C514372", 187, 11.4304, "Stock is adequate for prototypes but lower than the other locked ICs.", "https://www.lcsc.com/product-detail/C514372.html"],
  ["U8,U9", 2, "4 Gbit x16 DDR3L", "Verified in stock", "K4B4G1646E-BYMA", "C500275", 2258, 2.6454, "Preferred replacement for the generic DDR row. Validate the exact 96-ball footprint and MIG timing model.", "https://lcsc.com/products/Memory_893.html"],
  ["U10", 1, "128 Mbit QSPI NOR", "Verified in stock", "W25Q128JVSIQ", "C97521", 32790, 2.6188, "Good configuration-flash candidate at 3.3 V; confirm boot-mode wiring.", "https://www.lcsc.com/product-detail/C97521.html"],
  ["U11,U12,U13,U14", 4, "64 Mbit HyperRAM", "Verified in stock", "IS66WVH8M8BLL-100B1LI", "C1349096", 959, 6.6486, "Locked primary: 3.0 V, 100 MHz, TFBGA-24. Put the four devices on a 3.3 V FPGA bank, use single-ended CK, and validate the MJoergen controller constraints and board timing.", "https://www.lcsc.com/brand/1288-230.html"],
  ["U15,U16,U17", 3, "5 V PCI bus translation", "Replacement candidate", "SN74CB3T16210DGGR", "C2674635", null, null, "JLC-supported 20-bit bidirectional FET bus switch with 5 V-to-3.3 V translation and power-off isolation. Three parts provide 60 channels. Lock after the PCI signal and OE-group audit.", "https://www.lcsc.com/product-detail/C2674635.html"],
  ["U20", 1, "1.0 V FPGA-core buck", "Verified in stock", "TPS62480RNCR", "C2071434", 575, 1.0839, "Preferred production candidate: active TI 6 A dual-phase converter. Configure as one 1.0 V/6 A output and lock only after the FPGA power estimate and transient/thermal review.", "https://www.lcsc.com/product-detail/C2071434.html"],
  ["L20,L21", 2, "0.47 uH core-buck inductors", "Verified in stock", "DFE252012F-R47M=P2", "C703140", 2830, 0.1758, "Two inductors are required by the TPS62480 dual-phase power stage. 4.9 A rated, 7.4 A saturation, 23 mOhm DCR; validate against the final ripple and thermal calculation.", "https://www.lcsc.com/product-detail/C703140.html"],
  ["U21", 1, "1.8 V auxiliary buck", "Stocked design candidate", "TPS54318RTER", "C57128", 437, 0.89, "3 A adjustable synchronous buck from the 5 V slot rail. Use for 1.8 V after checking VCCAUX current, sequencing, compensation, and thermal margin.", "https://www.lcsc.com/product-detail/C57128.html"],
  ["U22", 1, "1.35 V DDR3L buck", "Stocked design candidate", "TPS54318RTER", "C57128", 437, 0.89, "Reuse the 3 A regulator footprint for DDR3L. Finalize the output network from the MIG current estimate and the selected Samsung memory model.", "https://www.lcsc.com/product-detail/C57128.html"],
  ["U23", 1, "3.3 V I/O and HyperRAM buck", "Stocked design candidate", "TPS54318RTER", "C57128", 437, 0.89, "Reuse the 3 A regulator footprint. Check FPGA VCCO, four HyperRAM devices, DAC, PHY, flash, clocks, and connector loads before locking it.", "https://www.lcsc.com/product-detail/C57128.html"],
  ["U24", 1, "DDR VTT regulator", "Verified in stock", "TPS51200DRCR", "C34771", 67492, 0.5054, "Strong candidate; confirm VTT sink/source current and required output capacitance.", "https://www.lcsc.com/product-detail/C34771.html"],
  ["L1", 1, "PCI +5 V input filter", "Power design pending", "", "", null, null, "Choose the inductor/ferrite after slot-current, inrush, ripple, saturation, and DCR limits are known.", ""],
  ["Y1", 1, "50 MHz FPGA oscillator", "Verified in stock", "ASE-50.000MHZ-LC-T", "C596955", 994, 1.5488, "3.3 V CMOS, 3.2 x 2.5 mm candidate. Confirm FPGA clock-pin voltage and jitter budget.", "https://www.lcsc.com/product-detail/C596955.html"],
  ["Y2", 1, "24 MHz T113 crystal", "Verified in stock", "X1E0000210129", "C255917", 11840, 0.2844, "12 pF, 3.2 x 2.5 mm candidate. Recalculate external load capacitors from board stray capacitance.", "https://www.lcsc.com/product-detail/C255917.html"],
  ["K1,K2,K3", 3, "Fail-safe VGA analog switching", "Verified in stock", "G6K-2F-Y DC5 BY OMZ/C", "C231389", 4775, 1.639, "Use three DPDT relays for RGB, HSync, and VSync, leaving one spare pole. Wire the normally-closed contacts for unpowered VGA passthrough; add one low-side coil driver and flyback path per relay.", "https://www.lcsc.com/product-detail/C231389.html"],
  ["J2", 1, "VGA input connector", "Replacement candidate", "634-015-363-543", "C3099918", null, null, "JLC lists this exact 15-pin, 3-row, female right-angle connector for wave soldering. Define the bracket and board-edge datum from its drawing before locking the footprint.", "https://jlcpcb.com/partdetail/EDAC-634_015_363543/C3099918"],
  ["J3", 1, "VGA output connector", "Replacement candidate", "634-015-363-543", "C3099918", null, null, "Use the same connector as VGA input. Two identical parts simplify the bracket and assembly fixture; pre-order through JLC after the mechanical drawing is frozen.", "https://jlcpcb.com/partdetail/EDAC-634_015_363543/C3099918"],
  ["J4", 1, "HDMI output connector", "Verified in stock", "HDMI-019S", "C111617", 13355, 0.2645, "Right-angle HDMI-A receptacle in the JLC parts library. Verify shell-stake holes and bracket height from the manufacturer drawing.", "https://jlcpcb.com/partdetail/HDMI-019S/C111617"],
  ["J5", 1, "USB-A host connector", "Verified in stock", "902-131A1021D10100", "C2344", 12892, 0.0477, "Right-angle USB 2.0 Type-A receptacle. JLC lists wave-solder assembly; include the assembly fixture and bracket opening in the quote.", "https://jlcpcb.com/parts/2nd/Connectors/USB_Connectors_2945"],
  ["J6", 1, "RJ45 with magnetics", "Verified in stock", "HR911105A", "C12074", 16727, 1.5049, "Shielded right-angle 10/100 magjack with LEDs. Wave solder or manual secondary assembly is required; verify transformer pinout, PHY center taps, and LED polarity.", "https://www.lcsc.com/product-detail/C12074.html"],
  ["J7", 1, "UART debug connector", "Verified in stock", "SM04B-SRSS-TB(LF)(SN)", "C160404", 64341, 0.2134, "JST SH 1.0 mm, four-pin, right-angle SMT connector. Assign GND, 3.3 V reference, TX and RX; do not power the board through this port.", "https://jlcpcb.com/partdetail/Jst-SM04B_SRSS_TB_LF_SN/C160404"],
  ["TP1", 1, "+5 V test point", "PCB feature; no BOM part", "1.0 mm exposed test pad", "", null, null, "Use a plated, soldermask-open PCB pad sized for a pogo pin; omit it from the assembly BOM and CPL.", ""],
  ["TP2", 1, "Rail test points", "PCB feature; no BOM part", "1.0 mm exposed test pads", "", null, null, "Provide one labeled pad per rail plus nearby grounds; omit them from the assembly BOM and CPL.", ""],
];

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Availability");
sheet.showGridLines = false;

sheet.getRange("A1").values = [["Voodoo 2 Artix card parts availability"]];
sheet.getRange("A1").format = { font: { bold: true, size: 16, color: "#17365D" } };
sheet.getRange("A2").values = [["Checked: 2026-09-16"]];
sheet.getRange("C2").values = [["Basis: 25 purchased BOM groups after removing PCB-only test pads"]];
sheet.getRange("F2").values = [["Rule: only concrete LCSC parts with observed stock count as verified"]];
sheet.getRange("A2:J2").format = { font: { italic: true, color: "#555555" } };

sheet.getRange("A4:J4").values = [["Purchased BOM line groups", 25, "Verified in stock", null, "Verified coverage", null, "Concrete replacement candidates", null, "Coverage including candidates", null]];
sheet.getRange("D4").formulas = [["=COUNTIF(D7:D33,\"Verified in stock\")"]];
sheet.getRange("F4").formulas = [["=D4/B4"]];
sheet.getRange("H4").formulas = [["=COUNTIF(D7:D33,\"Replacement candidate\")"]];
sheet.getRange("J4").formulas = [["=(D4+H4)/B4"]];
sheet.getRange("A4:J4").format = { fill: "#EAF0F6", font: { bold: true, color: "#17365D" }, wrapText: true, verticalAlignment: "center" };
sheet.getRange("F4").format.numberFormat = "0.0%";
sheet.getRange("J4").format.numberFormat = "0.0%";

const headers = [["Designators", "Qty/card", "Original intent", "Status", "Candidate MPN", "LCSC part #", "Stock observed", "Qty-1 price (USD)", "Replacement or next action", "Source"]];
sheet.getRange("A6:J6").values = headers;
sheet.getRange("A7:J33").values = rows;
sheet.getRange("A6:J33").format.font = { name: "Arial", size: 10, color: "#202124" };
sheet.getRange("A6:J6").format = { fill: "#17365D", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
sheet.getRange("A7:J33").format.verticalAlignment = "top";
sheet.getRange("C7:J33").format.wrapText = true;
sheet.getRange("B7:B33").format.numberFormat = "0";
sheet.getRange("G7:G33").format.numberFormat = "#,##0";
sheet.getRange("H7:H33").format.numberFormat = "$0.0000";
sheet.getRange("A6:J33").format.borders = { insideHorizontal: { style: "thin", color: "#D9E0E7" }, bottom: { style: "thin", color: "#B8C2CC" } };
sheet.getRange("A6:J33").conditionalFormats.add("containsText", { text: "Verified in stock", format: { fill: "#DCEBE1", font: { color: "#24563E" } } });
sheet.getRange("A6:J33").conditionalFormats.add("containsText", { text: "Out of stock", format: { fill: "#F4CCCC", font: { color: "#8A1C1C" } } });
sheet.getRange("A6:J33").conditionalFormats.add("containsText", { text: "Replacement candidate", format: { fill: "#FFF2CC", font: { color: "#7F6000" } } });
sheet.getRange("A6:J33").conditionalFormats.add("containsText", { text: "design candidate", format: { fill: "#FFF2CC", font: { color: "#7F6000" } } });
sheet.getRange("A6:J33").conditionalFormats.add("containsText", { text: "pending", format: { fill: "#F3F3F3", font: { color: "#555555" } } });
sheet.getRange("A6:J33").conditionalFormats.add("containsText", { text: "no BOM part", format: { fill: "#E8EEF7", font: { color: "#335A7D" } } });

const widths = [14, 9, 29, 22, 28, 14, 14, 16, 64, 48];
for (let i = 0; i < widths.length; i += 1) {
  sheet.getRangeByIndexes(0, i, 33, 1).format.columnWidth = widths[i];
}
sheet.getRange("1:1").format.rowHeight = 25;
sheet.getRange("2:2").format.rowHeight = 34;
sheet.getRange("4:4").format.rowHeight = 34;
sheet.getRange("6:6").format.rowHeight = 32;
sheet.freezePanes.freezeRows(6);
sheet.tables.add("A6:J33", true, "AvailabilityTable").style = "TableStyleMedium2";

const inspect = await workbook.inspect({ kind: "table", sheetId: "Availability", range: "A1:J33", include: "values,formulas", tableMaxRows: 33, tableMaxCols: 10, maxChars: 18000 });
console.log(inspect.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" });
console.log(errors.ndjson);

const preview = await workbook.render({ sheetName: "Availability", range: "A1:J33", scale: 1 });
await fs.writeFile(path.join(outputDir, "voodoo2_jlc_parts_availability_preview.png"), new Uint8Array(await preview.arrayBuffer()));
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(path.join(outputDir, "voodoo2_jlc_parts_availability.xlsx"));
