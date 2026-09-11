import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = "data/synthetic_orders.csv";
const outputDir = "outputs/supply-chain-poc";
const csvText = await fs.readFile(inputPath, "utf8");
const workbook = await Workbook.fromCSV(csvText, { sheetName: "Orders" });
const orders = workbook.worksheets.getItem("Orders");
const used = orders.getUsedRange();
const values = used.values;
const headers = values[0];
const numericFields = new Set([
  "ordered_qty", "confirmed_qty", "received_qty", "on_hand", "allocated",
  "safety_stock", "unit_price", "contract_price",
]);
const dateFields = new Set(["order_date", "promised_date", "current_eta"]);

for (let row = 1; row < values.length; row += 1) {
  for (let col = 0; col < headers.length; col += 1) {
    if (numericFields.has(headers[col])) values[row][col] = Number(values[row][col]);
    if (dateFields.has(headers[col]) && values[row][col]) values[row][col] = new Date(`${values[row][col]}T00:00:00Z`);
  }
}
used.values = values;

orders.showGridLines = false;
orders.freezePanes.freezeRows(1);
orders.freezePanes.freezeColumns(4);
used.format.font = { name: "Arial", size: 10, color: "#1F2937" };
orders.getRange(`A1:U1`).format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
orders.getRange("F2:H201").format.numberFormat = "mm/dd/yy";
orders.getRange("I2:O201").format.numberFormat = "#,##0";
orders.getRange("P2:Q201").format.numberFormat = '"$"#,##0.00';
orders.getRange("A1:U201").format.verticalAlignment = "center";
orders.getRange("A1:U201").format.autofitColumns();
orders.getRange("A1:U201").format.autofitRows();
orders.getRange("A:D").format.columnWidthPx = 92;
orders.getRange("E:E").format.columnWidthPx = 60;
orders.getRange("F:H").format.columnWidthPx = 86;
orders.getRange("I:O").format.columnWidthPx = 78;
orders.getRange("P:Q").format.columnWidthPx = 82;
orders.getRange("R:U").format.columnWidthPx = 125;
const ordersTable = orders.tables.add("A1:U201", true, "SyntheticOrdersTable");
ordersTable.style = "TableStyleMedium2";
orders.getRange("U2:U201").conditionalFormats.add("containsText", {
  text: "NO_EXCEPTION",
  format: { fill: "#E2F0D9", font: { color: "#375623" } },
});
orders.getRange("U2:U201").conditionalFormats.add("containsText", {
  text: "DATA_QUALITY",
  format: { fill: "#FCE4D6", font: { color: "#9C0006", bold: true } },
});

const guide = workbook.worksheets.add("Scenario guide");
guide.showGridLines = false;
guide.getRange("A2").values = [["Synthetic ERP order dataset"]];
guide.getRange("A2").format.font = { name: "Arial", size: 14, bold: true, color: "#1F2937" };
guide.getRange("A3:D3").format.borders = { bottom: { style: "thin", color: "#4472C4" } };
guide.getRange("A5:D11").values = [
  ["Scenario", "Records", "Trigger", "POC use"],
  ["NO_EXCEPTION", 75, "No configured rule triggers", "Control group"],
  ["LATE_SHIPMENT", 25, "Current ETA is after promised date", "Delivery-risk workflow"],
  ["INVENTORY_SHORTAGE", 25, "Available inventory is below ordered quantity", "Allocation workflow"],
  ["QUANTITY_SHORTFALL", 25, "Confirmed quantity is below ordered quantity", "Supplier recovery workflow"],
  ["PRICE_MISMATCH", 25, "Unit price differs from contract by more than 2%", "Commercial hold workflow"],
  ["DATA_QUALITY", 25, "A required ERP field is blank", "Data stewardship workflow"],
];
guide.getRange("A5:D5").format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
guide.getRange("A6:D11").format.font = { name: "Arial", size: 10, color: "#1F2937" };
guide.getRange("B6:B11").format.numberFormat = "#,##0";
guide.getRange("A5:D11").format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
guide.getRange("A13:D16").values = [
  ["Usage notes", null, null, null],
  ["Ground truth", "expected_exception is test metadata and is not used by the classifier.", null, null],
  ["Reproducibility", "Run python -m supply_chain_poc.data_generator --count 200 --seed 42.", null, null],
  ["Safety", "All organizations, orders, customers, suppliers, prices, and quantities are fictional.", null, null],
];
guide.mergeCells("B14:D14");
guide.mergeCells("B15:D15");
guide.mergeCells("B16:D16");
guide.getRange("A13:D13").format = { fill: "#D9EAF7", font: { name: "Arial", size: 10, bold: true, color: "#1F2937" } };
guide.getRange("A14:A16").format.font = { name: "Arial", size: 10, bold: true, color: "#1F2937" };
guide.getRange("B14:D16").format.font = { name: "Arial", size: 10, color: "#1F2937" };
guide.getRange("A1:D16").format.verticalAlignment = "center";
guide.getRange("A:A").format.columnWidthPx = 155;
guide.getRange("B:B").format.columnWidthPx = 105;
guide.getRange("C:C").format.columnWidthPx = 310;
guide.getRange("D:D").format.columnWidthPx = 190;
guide.getRange("B14:D16").format.wrapText = true;
guide.getRange("A14:D16").format.rowHeightPx = 34;

workbook.recalculate();
console.log((await workbook.inspect({
  kind: "table",
  range: "Orders!A1:U8",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 21,
})).ndjson);
console.log((await workbook.inspect({
  kind: "table",
  range: "Scenario guide!A2:D16",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 6,
})).ndjson);
console.log((await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
})).ndjson);

await fs.mkdir(outputDir, { recursive: true });
for (const [sheetName, fileName, range] of [
  ["Orders", "orders-preview.png", "A1:U30"],
  ["Scenario guide", "guide-preview.png", "A1:D16"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1.5, format: "png" });
  await fs.writeFile(`${outputDir}/${fileName}`, new Uint8Array(await preview.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outputDir}/synthetic_erp_orders.xlsx`);
console.log(`Saved ${outputDir}/synthetic_erp_orders.xlsx`);
