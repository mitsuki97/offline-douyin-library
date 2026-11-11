import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [creatorsPath, outPath] = process.argv.slice(2);
const creators = JSON.parse(await fs.readFile(creatorsPath, 'utf8')) || [];
const rows = creators.map((c, i) => {
  const url = String(c.url || '');
  const match = url.match(/\/user\/([^/?#]+)/);
  return [i + 1, c.name || '', url, match ? match[1] : '', c.id || '', c.created_at || ''];
});

const workbook = Workbook.create();
const sheet = workbook.worksheets.add('全部博主');
const headers = ['序号', '博主名称', '主页链接', '博主ID', '本地记录ID', '保存时间'];
const matrix = [headers, ...rows];
sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
const all = sheet.getRangeByIndexes(0, 0, matrix.length, headers.length);
sheet.getRangeByIndexes(0, 0, 1, headers.length).format = {
  fill: '#1F4E78',
  font: { bold: true, color: '#FFFFFF' },
  wrapText: true,
  verticalAlignment: 'center',
};
all.format.wrapText = true;
all.format.verticalAlignment = 'center';
all.format.borders = { preset: 'all', style: 'thin', color: '#D9E2F3' };
sheet.getRangeByIndexes(0, 0, 1, headers.length).format.rowHeight = 30;
[8, 24, 58, 48, 22, 21].forEach((width, i) => sheet.getRangeByIndexes(0, i, matrix.length, 1).format.columnWidth = width);
sheet.freezePanes.freezeRows(1);
sheet.showGridLines = false;
if (rows.length) sheet.tables.add(`A1:F${rows.length + 1}`, true, 'CreatorsTable');
await workbook.recalculate();
const inspect = await workbook.inspect({ kind: 'table', range: `A1:F${Math.min(rows.length + 1, 8)}`, include: 'values', tableMaxRows: 8, tableMaxCols: 6, maxChars: 3000 });
console.log(inspect.ndjson || inspect);
const preview = await workbook.render({ sheetName: '全部博主', range: `A1:F${Math.min(rows.length + 1, 20)}`, scale: 1, format: 'png' });
await fs.mkdir(path.dirname(outPath), { recursive: true });
await fs.writeFile(`${outPath}.preview.png`, new Uint8Array(await preview.arrayBuffer()));
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(outPath);
console.log(`EXPORTED ${outPath} creators=${rows.length}`);
