// Minimal, dependency-free CSV parser for the client-side "try it now" demo only.
// The real upload pipeline (app/core/ingestion/csv_source.py, via /bff/ingest/csv)
// does the actual production-grade parsing once someone signs up — this is
// intentionally a lighter best-effort parse, only ever used to pull a handful of
// review texts out of a file before any account exists.
export function parseCsv(text: string): Record<string, string>[] {
  const rows = splitCsvRows(text)
  if (rows.length < 2) return []
  const header = rows[0].map(h => h.trim().toLowerCase())
  return rows.slice(1).map(row => {
    const record: Record<string, string> = {}
    header.forEach((key, i) => { record[key] = (row[i] ?? '').trim() })
    return record
  })
}

function splitCsvRows(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let inQuotes = false

  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++ }
      else if (c === '"') { inQuotes = false }
      else { field += c }
    } else if (c === '"') {
      inQuotes = true
    } else if (c === ',') {
      row.push(field); field = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++
      row.push(field); field = ''
      if (row.some(f => f.length > 0)) rows.push(row)
      row = []
    } else {
      field += c
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field)
    if (row.some(f => f.length > 0)) rows.push(row)
  }
  return rows
}

// Heuristic text-column detection, mirroring the server's "auto-detected" promise
// (app/core/ingestion/csv_source.py) closely enough for a best-effort client demo:
// prefer an explicitly-named review/text/comment column, else the column with the
// longest average value.
export function detectTextColumn(records: Record<string, string>[]): string | null {
  if (records.length === 0) return null
  const columns = Object.keys(records[0])
  const named = columns.find(c => /review|text|comment|description|feedback/.test(c))
  if (named) return named

  let best: string | null = null
  let bestAvgLen = 0
  for (const col of columns) {
    const avgLen =
      records.reduce((sum, r) => sum + (r[col]?.length ?? 0), 0) / records.length
    if (avgLen > bestAvgLen) { bestAvgLen = avgLen; best = col }
  }
  return best
}
