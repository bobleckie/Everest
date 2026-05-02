/**
 * Shared Excel (XLSX) export utility.
 *
 * Usage:
 *   import { exportToExcel } from '../utils/exportExcel';
 *   exportToExcel({
 *     filename: 'compliance-matrix',
 *     sheetName: 'Requirements',
 *     columns: [
 *       { header: 'Document', key: 'document_name' },
 *       { header: 'Section',  key: 'section_id' },
 *       { header: 'Title',    key: 'title', width: 40 },
 *     ],
 *     rows: arrayOfObjects,
 *   });
 */
import * as XLSX from 'xlsx';
import { saveAs } from 'file-saver';

/**
 * @param {Object} opts
 * @param {string}  opts.filename   – without extension
 * @param {string}  [opts.sheetName='Sheet1']
 * @param {Array<{header:string, key:string, width?:number, transform?:Function}>} opts.columns
 * @param {Array<Object>} opts.rows
 */
export function exportToExcel({ filename, sheetName = 'Sheet1', columns, rows }) {
  // Build header row
  const headers = columns.map((c) => c.header);

  // Build data rows
  const data = rows.map((row) =>
    columns.map((col) => {
      let v = row[col.key];
      if (col.transform) v = col.transform(v, row);
      if (v == null) return '';
      return v;
    })
  );

  // Create worksheet
  const ws = XLSX.utils.aoa_to_sheet([headers, ...data]);

  // Set column widths
  ws['!cols'] = columns.map((col) => ({
    wch: col.width || Math.max(col.header.length + 2, 14),
  }));

  // Create workbook & save
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, sheetName);
  const buf = XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
  const blob = new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  saveAs(blob, `${filename}.xlsx`);
}
