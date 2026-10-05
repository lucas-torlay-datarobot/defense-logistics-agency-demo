import { Download, FileCode2, Database, BookmarkPlus } from "lucide-react";
import { label, number, type Result, type Row } from "../api";

export function display(value: Row[string], key: string) {
  if (value === null) return "—";
  if (
    typeof value === "number" &&
    (key.includes("probability") || key === "mean_risk")
  )
    return `${(value * 100).toFixed(1)}%`;
  if (typeof value === "number") return number(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value).replace("T00:00:00", "");
}
function download(result: Result) {
  const escape = (value: unknown) => {
    let s = String(value ?? "");
    if (/^[=+@\-\t\r]/.test(s)) s = `'${s}`;
    return `"${s.replaceAll('"', '""')}"`;
  };
  const csv = [
    result.columns,
    ...result.rows.map((row) => result.columns.map((c) => row[c])),
  ]
    .map((row) => row.map(escape).join(","))
    .join("\r\n");
  const url = URL.createObjectURL(
    new Blob([csv], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "SYNTHETIC_query_result.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function DataTable({
  result,
  compact = false,
  onRow,
}: {
  result: Result;
  compact?: boolean;
  onRow?: (row: Row) => void;
}) {
  return (
    <div className={`table-scroll ${compact ? "compact" : ""}`}>
      <table>
        <thead>
          <tr>
            {result.columns.map((c) => (
              <th key={c}>{label(c)}</th>
            ))}
            {onRow && <th>Investigate</th>}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, i) => (
            <tr key={i}>
              {result.columns.map((c) => (
                <td
                  key={c}
                  className={c.includes("probability") ? "risk-value" : ""}
                >
                  {display(row[c], c)}
                </td>
              ))}
              {onRow && (
                <td>
                  <button className="text-button" onClick={() => onRow(row)}>
                    Ask agent ↗
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {!result.rows.length && (
        <p className="empty">No matching rows in this snapshot.</p>
      )}
    </div>
  );
}
export function Evidence({
  result,
  compact = false,
  onSave,
}: {
  result: Result;
  compact?: boolean;
  onSave?: () => void;
}) {
  return (
    <div className="evidence">
      <DataTable result={result} compact={compact} />
      <div className="evidence-footer">
        <span>
          {result.rows.length} rows{result.truncated ? " · result capped" : ""}
        </span>
        <button className="text-button" onClick={() => download(result)}>
          <Download size={13} /> CSV
        </button>
        {onSave && (
          <button className="text-button" onClick={onSave}>
            <BookmarkPlus size={13} /> Save for review
          </button>
        )}
      </div>
      <details>
        <summary>
          <FileCode2 size={13} /> Inspect SQL & sources
        </summary>
        <pre>{result.sql}</pre>
        <div className="source-chips">
          {result.sources.map((s) => (
            <span key={s}>
              <Database size={11} />
              {s}
            </span>
          ))}
        </div>
        <p className="muted small">
          Synthetic operations. Results bounded to {result.row_limit} rows. Text
          cells capped at 2,000 characters.
        </p>
      </details>
    </div>
  );
}
