import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const outputDir = path.resolve(
  "outputs/01a0a9af-6eae-7e02-8e64-a55eea3bb977",
);
await mkdir(outputDir, { recursive: true });

const bomRows = [
  ["Comment", "Designator", "Footprint", "LCSC Part #"],
  ["AMD XC7A200T-2FBG484I FPGA", "U1", "Rough:U1", "C494680"],
  ["Allwinner T113-S3 application processor", "U2", "Rough:U2", "C5197687"],
  ["GigaDevice GD5F1GQ5UEYIGR 1 Gbit SPI NAND", "U3", "Rough:U3", "C2829047"],
  ["Realtek RTL8201F-VB-CG 100BASE-TX PHY", "U4", "Rough:U4", "C45044"],
  ["Analog Devices ADV7123KSTZ50 video DAC", "U6", "Rough:U6", "C514372"],
  ["Samsung K4B4G1646E-BYMA 4 Gbit x16 DDR3L", "U8,U9", "Rough:U8", "C500275"],
  ["Winbond W25Q128JVSIQ 128 Mbit QSPI NOR", "U10", "Rough:U10", "C97521"],
  ["ISSI IS66WVH8M8BLL-100B1LI 64 Mbit HyperRAM", "U11,U12,U13,U14", "Rough:U11", "C1349096"],
  ["TI SN74CB3T16210DGGR 20-bit PCI bus switch", "U15,U16,U17", "Rough:U15", "C2674635"],
  ["TI TPS62480RNCR 6 A dual-phase 1.0 V buck", "U20", "Rough:U20", "C2071434"],
  ["Murata DFE252012F-R47M=P2 0.47 uH power inductor", "L20,L21", "Rough:L20", "C703140"],
  ["TI TPS54318RTER 3 A 1.8 V buck candidate", "U21", "Rough:U21", "C57128"],
  ["TI TPS54318RTER 3 A 1.35 V buck candidate", "U22", "Rough:U22", "C57128"],
  ["TI TPS54318RTER 3 A 3.3 V buck candidate", "U23", "Rough:U23", "C57128"],
  ["TI TPS51200DRCR DDR termination regulator", "U24", "Rough:U24", "C34771"],
  ["Power input filter block (inductor and passives TBD)", "L1", "Rough:L1", ""],
  ["Abracon ASE-50.000MHZ-LC-T 50 MHz oscillator", "Y1", "Rough:Y1", "C596955"],
  ["Epson X1E0000210129 24 MHz crystal", "Y2", "Rough:Y2", "C255917"],
  ["Omron G6K-2F-Y DC5 BY OMZ/C DPDT signal relay", "K1,K2,K3", "Rough:K1", "C231389"],
  ["EDAC 634-015-363-543 right-angle VGA connector", "J2", "Rough:J2", "C3099918"],
  ["EDAC 634-015-363-543 right-angle VGA connector", "J3", "Rough:J3", "C3099918"],
  ["SOFNG HDMI-019S right-angle HDMI-A connector", "J4", "Rough:J4", "C111617"],
  ["902-131A1021D10100 right-angle USB-A connector", "J5", "Rough:J5", "C2344"],
  ["HANRUN HR911105A 10/100 magjack", "J6", "Rough:J6", "C12074"],
  ["JST SM04B-SRSS-TB(LF)(SN) UART service connector", "J7", "Rough:J7", "C160404"],
];

const cplRows = [
  ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"],
  ["J2", 31.5, 36, "Top", 0],
  ["J3", 31.5, 55, "Top", 0],
  ["J4", 31.5, 72, "Top", 0],
  ["J5", 31.5, 87, "Top", 0],
  ["J6", 31.5, 105, "Top", 0],
  ["U6", 82, 87, "Top", 0],
  ["K1", 54, 88, "Top", 90],
  ["K2", 62, 88, "Top", 90],
  ["K3", 70, 88, "Top", 90],
  ["U2", 69, 47, "Top", 0],
  ["U3", 56, 31, "Top", 0],
  ["U4", 79, 31, "Top", 0],
  ["Y2", 55, 58, "Top", 0],
  ["J7", 81, 58, "Top", 0],
  ["U1", 119, 54, "Top", 0],
  ["U8", 142, 46, "Top", 0],
  ["U9", 142, 61, "Top", 0],
  ["U10", 104, 76, "Top", 0],
  ["Y1", 118, 76, "Top", 0],
  ["U11", 99, 89, "Top", 0],
  ["U12", 111, 89, "Top", 0],
  ["U13", 123, 89, "Top", 0],
  ["U14", 135, 89, "Top", 0],
  ["U20", 164, 35, "Top", 0],
  ["L20", 160, 43, "Top", 0],
  ["L21", 168, 43, "Top", 0],
  ["U21", 180, 35, "Top", 0],
  ["U22", 164, 51, "Top", 0],
  ["U23", 180, 51, "Top", 0],
  ["U24", 164, 67, "Top", 0],
  ["L1", 180, 67, "Top", 0],
  ["U15", 74, 108, "Top", 0],
  ["U16", 98, 108, "Top", 0],
  ["U17", 122, 108, "Top", 0],
];

async function makeWorkbook(rows, sheetName, widths, outputName) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(sheetName);
  const range = sheet.getRangeByIndexes(0, 0, rows.length, rows[0].length);
  range.values = rows;
  sheet.freezePanes.freezeRows(1);
  const header = sheet.getRangeByIndexes(0, 0, 1, rows[0].length);
  header.format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
  };
  for (let i = 0; i < widths.length; i += 1) {
    sheet.getRangeByIndexes(0, i, rows.length, 1).format.columnWidth = widths[i];
  }
  range.format.wrapText = true;
  const preview = await workbook.render({ sheetName, range: range.address, scale: 1 });
  await writeFile(
    path.join(outputDir, outputName.replace(/\.xlsx$/, "_preview.png")),
    new Uint8Array(await preview.arrayBuffer()),
  );
  const file = await SpreadsheetFile.exportXlsx(workbook);
  await file.save(path.join(outputDir, outputName));
}

await makeWorkbook(
  bomRows,
  "BOM",
  [56, 24, 22, 18],
  "voodoo2_preliminary_bom.xlsx",
);
await makeWorkbook(
  cplRows,
  "CPL",
  [18, 14, 14, 12, 12],
  "voodoo2_preliminary_cpl.xlsx",
);
