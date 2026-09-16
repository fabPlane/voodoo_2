import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outDir = path.dirname(new URL(import.meta.url).pathname);
const bomPath = path.join(outDir, "voodoo2_rev_a_pricing_bom.xlsx");
const cplPath = path.join(outDir, "voodoo2_rev_a_pricing_cpl.xlsx");
const font = "Arial";

const parts = [
  ["FPGA (rev A; XC7A200T-2FBG484I C494680 is the pin-identical Voodoo 2 alternate)", "U1", 1, "XC7A100T-2FGG484I", "C410276", "Selected", "BGA-484", "AMD Artix-7 100T FPGA"],
  ["Companion CPU", "U2", 1, "T113-S3", "C5197687", "Selected", "LQFP-128", "Allwinner application processor"],
  ["SPI NAND", "U3", 1, "GD5F1GQ5UEYIGR", "C2829047", "Selected", "WSON-8", "GigaDevice 1 Gbit SPI NAND"],
  ["Ethernet PHY", "U4", 1, "RTL8201F-VB-CG", "C45044", "Selected", "QFN-32", "Realtek 10/100 PHY"],
  ["Video DAC", "U6", 1, "ADV7123KSTZ50", "C514372", "Selected", "LQFP-48", "Analog Devices video DAC"],
  ["DDR3L, 32-bit bus (K4B4G1646E-BYMA C500275 is the 1 GB alternate)", "U8,U9", 2, "W632GU6NB-11", "C3034991", "Candidate", "VFBGA-96", "Winbond 2 Gbit x16 DDR3L"],
  ["QSPI flash", "U10", 1, "W25Q128JVSIQ", "C97521", "Selected", "SOIC-8", "Winbond 128 Mbit QSPI NOR"],
  ["HyperRAM", "U11,U12,U13,U14", 4, "IS66WVH8M8BLL-100B1LI", "C1349096", "Selected", "TFBGA-24", "ISSI 64 Mbit HyperRAM"],
  ["PCI bus switch", "U15,U16,U17", 3, "SN74CBTD16211DGGR", "C2674790", "Candidate", "TSSOP-56", "TI 24-bit bus switch with 5 V clamp diode"],
  ["FPGA core buck", "U20", 1, "TPS62480RNCR", "C2071434", "Candidate", "VQFN-18", "TI 6 A dual-phase buck"],
  ["Core inductors", "L20,L21", 2, "DFE252012F-R47M=P2", "C703140", "Candidate", "2520", "Murata 0.47 uH"],
  ["1.8 V buck", "U21", 1, "TPS54318RTER", "C57128", "Candidate", "WQFN-16", "TI 3 A adjustable buck"],
  ["1.35 V buck", "U22", 1, "TPS54318RTER", "C57128", "Candidate", "WQFN-16", "TI 3 A adjustable buck"],
  ["3.3 V buck", "U23", 1, "TPS54318RTER", "C57128", "Candidate", "WQFN-16", "TI 3 A adjustable buck"],
  ["DDR termination", "U24", 1, "TPS51200DRCR", "C34771", "Selected", "VSON-10", "TI DDR VTT regulator"],
  ["PCI input filter", "L1", 1, "TBD", "", "Provisional", "Power block", "Input filter allowance"],
  ["FPGA clock", "Y1", 1, "ASE-50.000MHZ-LC-T", "C596955", "Selected", "OSC-SMD", "Abracon 50 MHz oscillator"],
  ["CPU crystal", "Y2", 1, "X1E0000210129", "C255917", "Selected", "Crystal-SMD", "Epson 24 MHz crystal"],
  ["VGA relays", "K1,K2,K3", 3, "G6K-2F-Y DC5 BY OMZ/C", "C231389", "Selected", "G6K-SMD", "Omron DPDT signal relay"],
  ["VGA input", "J2", 1, "KH-HDR15P-F3.08", "C3038673", "Candidate", "DSub-15-RA", "Kinghelm right-angle HD15 female"],
  ["VGA output", "J3", 1, "KH-HDR15P-F3.08", "C3038673", "Candidate", "DSub-15-RA", "Kinghelm right-angle HD15 female"],
  ["HDMI output", "J4", 1, "HDMI-019S", "C111617", "Selected", "HDMI-A-RA", "SOFNG right-angle HDMI-A"],
  ["USB host", "J5", 1, "902-131A1021D10100", "C2344", "Selected", "USB-A-RA", "Right-angle USB-A"],
  ["Ethernet magjack", "J6", 1, "HR911105A", "C12074", "Selected", "RJ45-Magjack", "HANRUN 10/100 magjack"],
  ["UART service", "J7", 1, "SM04B-SRSS-TB(LF)(SN)", "C160404", "Selected", "JST-SH-4", "JST 4-pin service connector"],
  ["100 nF bypass", expand("C", 1, 96).join(","), 96, "CL05B104KO5NNNC", "C1525", "Candidate", "C_0402", "Samsung 100 nF 16 V X7R"],
  ["1 uF bypass", expand("C", 101, 24).join(","), 24, "CL10A105KB8NNNC", "C15849", "Candidate", "C_0603", "Samsung 1 uF 50 V X7R"],
  ["10 uF rail capacitors", expand("C", 131, 16).join(","), 16, "CL21A106KAYNNNE", "C15850", "Candidate", "C_0805", "Samsung 10 uF 25 V X5R"],
  ["22 uF rail capacitors", expand("C", 151, 12).join(","), 12, "CL21A226MAQNNNE", "C45783", "Candidate", "C_0805", "Samsung 22 uF 25 V X5R"],
  ["Configuration straps", expand("R", 1, 24).join(","), 24, "0402WGF0000TCE", "C17168", "Candidate", "R_0402", "0 ohm links"],
  ["Pull resistors", expand("R", 31, 32).join(","), 32, "0402WGF1002TCE", "C25744", "Candidate", "R_0402", "10 kohm 1%"],
  ["Series damping", expand("R", 71, 40).join(","), 40, "RC0402FR-0733RL", "C138002", "Candidate", "R_0402", "33 ohm 1%"],
  ["Feedback and video resistors", expand("R", 121, 24).join(","), 24, "TBD", "", "Provisional", "R_0402", "Value mix; placement allowance"],
  ["Relay drivers", "Q1,Q2,Q3", 3, "2N7002", "C8545", "Candidate", "SOT-23", "N-channel MOSFET"],
  ["Relay flyback diodes", "D1,D2,D3", 3, "1N4148W", "C81598", "Candidate", "SOD-123", "Switching diode"],
  ["USB/Ethernet ESD", "U30,U34", 2, "USBLC6-2SC6", "C7519", "Candidate", "SOT-23-6", "ST two-line ESD array"],
  ["HDMI ESD", "U31,U32", 2, "TBD", "", "Provisional", "ESD-array", "Ultra-low-capacitance arrays"],
  ["VGA ESD", "U33", 1, "TBD", "", "Provisional", "ESD-array", "Analog-video protection"],
  ["Auxiliary buck inductors", "L22,L23,L24", 3, "TBD", "", "Provisional", "Power inductor", "1 uH / current rating TBD"],
  ["PCI input fuse", "F1", 1, "TBD", "", "Provisional", "Fuse-SMD", "Resettable or fixed fuse"],
  ["PCI 5 V TVS", "D10", 1, "SMAJ5.0A-TP", "C129544", "Candidate", "SMA", "5 V unidirectional TVS"],
  ["Input load switch", "Q10", 1, "TBD", "", "Provisional", "QFN", "Inrush and reverse-block allowance"],
  ["FPGA JTAG header (Xilinx 2 mm 2x7 cable pinout)", "J8", 1, "IDC2.0-2*7PZ", "C53207571", "Candidate", "IDC-2x7-2.0mm", "JXTCONN 2 mm 2x7 shrouded box header"],
  ["Bench 5 V input (diode-OR or load switch against the slot rail)", "J10", 1, "DC-005C-20A", "C84007", "Candidate", "DC-Jack-RA", "Korean Hroparts 2.0 mm barrel jack, 3 A"],
  ["Manufacturing header", "J9", 1, "TBD", "", "Provisional", "Header-SMD", "Connector selection pending"],
  ["Status LEDs", expand("D", 20, 5).join(","), 5, "TBD", "", "Provisional", "LED_0603", "Color and current pending"],
];

function expand(prefix, start, count) {
  return Array.from({ length: count }, (_, i) => `${prefix}${start + i}`);
}

function addGrid(rows, refs, startX, startY, columns, dx, dy, rotation = 0) {
  refs.forEach((ref, index) => {
    rows.push([ref, startX + (index % columns) * dx, startY + Math.floor(index / columns) * dy, "Top", rotation]);
  });
}

const cpl = [
  ["J2", 31.5, 36, "Top", 0], ["J3", 31.5, 55, "Top", 0],
  ["J4", 31.5, 72, "Top", 0], ["J5", 31.5, 87, "Top", 0],
  ["J6", 31.5, 105, "Top", 0], ["U6", 82, 87, "Top", 0],
  ["K1", 54, 88, "Top", 90], ["K2", 62, 88, "Top", 90], ["K3", 70, 88, "Top", 90],
  ["U2", 69, 47, "Top", 0], ["U3", 56, 31, "Top", 0], ["U4", 79, 31, "Top", 0],
  ["Y2", 55, 58, "Top", 0], ["J7", 81, 58, "Top", 0],
  ["U1", 119, 54, "Top", 0], ["U8", 142, 46, "Top", 0], ["U9", 142, 61, "Top", 0],
  ["U10", 104, 76, "Top", 0], ["Y1", 118, 76, "Top", 0],
  ["U11", 99, 89, "Top", 0], ["U12", 111, 89, "Top", 0],
  ["U13", 123, 89, "Top", 0], ["U14", 135, 89, "Top", 0],
  ["U20", 164, 35, "Top", 0], ["L20", 160, 43, "Top", 0], ["L21", 168, 43, "Top", 0],
  ["U21", 180, 35, "Top", 0], ["U22", 164, 51, "Top", 0], ["U23", 180, 51, "Top", 0],
  ["U24", 164, 67, "Top", 0], ["L1", 180, 67, "Top", 0],
  ["U15", 74, 108, "Top", 0], ["U16", 98, 108, "Top", 0], ["U17", 122, 108, "Top", 0],
];

addGrid(cpl, expand("C", 1, 32), 92, 32, 8, 1.75, 1.65);
addGrid(cpl, expand("C", 33, 32), 92, 68, 8, 1.75, 1.65);
addGrid(cpl, expand("C", 65, 16), 51, 37, 4, 1.65, 1.65);
addGrid(cpl, expand("C", 81, 16), 137, 75, 4, 1.65, 1.65);
addGrid(cpl, expand("C", 101, 24), 153.5, 78, 6, 2.15, 2);
addGrid(cpl, expand("C", 131, 16), 154, 88, 4, 3, 2.5);
addGrid(cpl, expand("C", 151, 12), 174, 78, 4, 3, 2.5);
addGrid(cpl, expand("R", 1, 24), 49.5, 25, 8, 2, 1.7);
addGrid(cpl, expand("R", 31, 32), 49.5, 64, 8, 2, 1.7);
addGrid(cpl, expand("R", 71, 40), 91.5, 100, 10, 2.2, 1.8);
addGrid(cpl, expand("R", 121, 24), 148, 100, 8, 2.1, 1.8);
cpl.push(
  ["Q1", 49.5, 84, "Top", 0], ["Q2", 49.5, 88, "Top", 0], ["Q3", 49.5, 92, "Top", 0],
  ["D1", 51.5, 84, "Top", 0], ["D2", 51.5, 88, "Top", 0], ["D3", 51.5, 92, "Top", 0],
  ["U30", 43.5, 86, "Top", 0], ["U31", 43.5, 70, "Top", 0], ["U32", 43.5, 74, "Top", 0],
  ["U33", 43.5, 47, "Top", 0], ["U34", 43.5, 103, "Top", 0],
  ["L22", 158, 57.5, "Top", 0], ["L23", 176, 57.5, "Top", 0], ["L24", 158, 73, "Top", 0],
  ["F1", 184, 72, "Top", 0], ["D10", 188, 72, "Top", 0], ["Q10", 184, 77, "Top", 0],
  ["J8", 86, 92, "Top", 0], ["J9", 73, 96, "Top", 0], ["J10", 190, 27, "Top", 0],
  ["D20", 188, 83, "Top", 0], ["D21", 188, 85, "Top", 0], ["D22", 188, 87, "Top", 0],
  ["D23", 188, 89, "Top", 0], ["D24", 188, 91, "Top", 0],
);

if (cpl.length !== 327) throw new Error(`Expected 327 placements, found ${cpl.length}`);

const bomRows = [["Comment", "Designator", "Footprint", "LCSC Part #"]];
for (const [func, designators, qty, mpn, lcsc, state, footprint, description] of parts) {
  const suffix = state === "Provisional" ? " — PRICING ALLOWANCE" : "";
  bomRows.push([`${description}; ${mpn}; qty ${qty}${suffix}`, designators, footprint, lcsc]);
}

function styleHeader(range) {
  range.format = {
    fill: "#17365D",
    font: { name: font, bold: true, color: "#FFFFFF", size: 10 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
  };
}

async function buildBom() {
  const wb = Workbook.create();
  const sheet = wb.worksheets.add("BOM");
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.getRangeByIndexes(0, 0, bomRows.length, 4).values = bomRows;
  sheet.getRangeByIndexes(0, 0, bomRows.length, 4).format.font = { name: font, size: 10 };
  sheet.getRangeByIndexes(0, 0, bomRows.length, 4).format.verticalAlignment = "top";
  sheet.getRangeByIndexes(0, 0, bomRows.length, 4).format.wrapText = true;
  styleHeader(sheet.getRange("A1:D1"));
  [58, 24, 24, 18].forEach((width, i) => sheet.getRangeByIndexes(0, i, bomRows.length, 1).format.columnWidth = width);

  const status = wb.worksheets.add("Parts status");
  status.showGridLines = false;
  status.getRange("A1:H1").merge();
  status.getRange("A1").values = [["Rev A pricing population"]];
  status.getRange("A1").format.font = { name: font, size: 16, bold: true, color: "#17365D" };
  status.getRange("A2:H2").merge();
  status.getRange("A2").values = [["Quantity 5; top-side-only assembly; support values remain provisional until schematic and power-tree review."]];
  status.getRange("A2").format.font = { name: font, size: 10, italic: true, color: "#555555" };
  status.getRange("A4:A7").values = [["BOM line groups"], ["Placements per board"], ["Placements with LCSC match"], ["Placement coverage"]];
  status.getRange("B4").formulas = [["=COUNTA(A11:A56)"]];
  status.getRange("B5").formulas = [["=SUM(C11:C56)"]];
  status.getRange("B6").formulas = [["=SUMIF(F11:F56,\"<>\",C11:C56)"]];
  status.getRange("B7").formulas = [["=B6/B5"]];
  status.getRange("B4:B6").format.numberFormat = "#,##0";
  status.getRange("B7").format.numberFormat = "0.0%";
  status.getRange("A4:B7").format.font = { name: font, size: 11 };
  status.getRange("A4:A7").format.font = { name: font, size: 11, bold: true };

  const statusRows = [["Function", "Designators", "Qty/board", "Qty/5", "MPN", "LCSC", "State", "Source"]];
  for (const [func, designators, qty, mpn, lcsc, state] of parts) {
    statusRows.push([func, designators, qty, null, mpn, lcsc, state, lcsc ? `https://www.lcsc.com/product-detail/${lcsc}.html` : ""]);
  }
  status.getRangeByIndexes(9, 0, statusRows.length, 8).values = statusRows;
  status.getRange("D11").formulas = [["=C11*5"]];
  status.getRange("D11:D56").fillDown();
  styleHeader(status.getRange("A10:H10"));
  status.freezePanes.freezeRows(10);
  status.getRange("A10:H56").format.font = { name: font, size: 9 };
  status.getRange("A10:H56").format.verticalAlignment = "top";
  status.getRange("A10:H56").format.wrapText = true;
  status.getRange("C11:D56").format.numberFormat = "#,##0";
  [24, 22, 11, 11, 31, 14, 14, 48].forEach((width, i) => status.getRangeByIndexes(0, i, 56, 1).format.columnWidth = width);
  status.getRange("G11:G56").conditionalFormats.add("containsText", { text: "Provisional", format: { fill: "#FFF2CC", font: { color: "#7F6000" } } });

  console.log((await wb.inspect({ kind: "table", sheetId: "Parts status", range: "A1:H16", include: "values,formulas", tableMaxRows: 16, tableMaxCols: 8 })).ndjson);
  console.log((await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" })).ndjson);
  const preview = await wb.render({ sheetName: "Parts status", range: "A1:H22", scale: 1 });
  await fs.writeFile(path.join(outDir, "pricing_bom_preview.png"), new Uint8Array(await preview.arrayBuffer()));
  await (await SpreadsheetFile.exportXlsx(wb)).save(bomPath);
}

async function buildCpl() {
  const wb = Workbook.create();
  const sheet = wb.worksheets.add("CPL");
  const rows = [["Designator", "Mid X", "Mid Y", "Layer", "Rotation"], ...cpl];
  sheet.getRangeByIndexes(0, 0, rows.length, 5).values = rows;
  sheet.freezePanes.freezeRows(1);
  styleHeader(sheet.getRange("A1:E1"));
  sheet.getRangeByIndexes(0, 0, rows.length, 5).format.font = { name: font, size: 10 };
  sheet.getRange("B2:C328").format.numberFormat = "0.000";
  sheet.getRange("E2:E328").format.numberFormat = "0";
  [18, 14, 14, 12, 12].forEach((width, i) => sheet.getRangeByIndexes(0, i, rows.length, 1).format.columnWidth = width);
  console.log((await wb.inspect({ kind: "table", sheetId: "CPL", range: "A1:E12", include: "values,formulas", tableMaxRows: 12, tableMaxCols: 5 })).ndjson);
  console.log((await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" })).ndjson);
  const preview = await wb.render({ sheetName: "CPL", range: "A1:E22", scale: 1 });
  await fs.writeFile(path.join(outDir, "pricing_cpl_preview.png"), new Uint8Array(await preview.arrayBuffer()));
  await (await SpreadsheetFile.exportXlsx(wb)).save(cplPath);
}

await buildBom();
await buildCpl();
console.log(JSON.stringify({ bomPath, cplPath, lineGroups: parts.length, placements: cpl.length }));
