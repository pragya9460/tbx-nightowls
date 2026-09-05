import type { Evidence } from "../types";

function formatCell(key: string, value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") {
    // amounts look like money — format compactly
    if (/amount|value/i.test(key) && Math.abs(value) >= 1000) {
      return value.toLocaleString("en-IN", { maximumFractionDigits: 0 });
    }
    return value.toLocaleString("en-IN");
  }
  return String(value);
}

export function EvidenceTable({
  title,
  rows,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
}) {
  if (!rows || rows.length === 0) return null;
  const columns = Object.keys(rows[0]);
  return (
    <div className="mt-3">
      <div className="mb-1 text-xs font-medium text-[var(--artha-muted)]">{title}</div>
      <div className="overflow-x-auto rounded-lg border border-[var(--artha-border)]">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-[var(--artha-accent-soft)] text-[var(--artha-muted)]">
            <tr>
              {columns.map((c) => (
                <th key={c} className="px-3 py-2 font-medium whitespace-nowrap">
                  {c.replace(/_/g, " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={i}
                className="border-t border-[var(--artha-border)] even:bg-black/[0.02]"
              >
                {columns.map((c) => (
                  <td key={c} className="px-3 py-1.5 whitespace-nowrap tabular-nums">
                    {formatCell(c, row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function EvidencePanel({ evidence }: { evidence: Evidence }) {
  const hc = evidence.how_calculated;
  const filterEntries = Object.entries(hc.filters ?? {});

  return (
    <details className="mt-3 rounded-lg border border-[var(--artha-border)] bg-[var(--artha-panel)]">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-[var(--artha-accent)]">
        ✓ Grounded — view how this was calculated
      </summary>
      <div className="border-t border-[var(--artha-border)] px-3 py-2 text-xs space-y-1.5">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span>
            <span className="text-[var(--artha-muted)]">Date: </span>
            {hc.date_range}
          </span>
          <span>
            <span className="text-[var(--artha-muted)]">Records matched: </span>
            <span className="tabular-nums">{hc.records_matched.toLocaleString("en-IN")}</span>
          </span>
          <span>
            <span className="text-[var(--artha-muted)]">Operation: </span>
            <code className="rounded bg-black/5 px-1 py-0.5">{hc.operation}</code>
          </span>
          {filterEntries.length > 0 && (
            <span>
              <span className="text-[var(--artha-muted)]">Filters: </span>
              {filterEntries.map(([k, v]) => `${k}=${v}`).join(", ")}
            </span>
          )}
        </div>
        <div className="text-[var(--artha-muted)]">Source: {evidence.source}</div>
      </div>
      {evidence.breakdown && evidence.breakdown.length > 0 && (
        <div className="border-t border-[var(--artha-border)] px-3 pb-3">
          <EvidenceTable
            title="Breakdown"
            rows={evidence.breakdown as Array<Record<string, unknown>>}
          />
        </div>
      )}
      {evidence.records && evidence.records.length > 0 && (
        <div className="border-t border-[var(--artha-border)] px-3 pb-3">
          <EvidenceTable
            title="Records"
            rows={evidence.records as Array<Record<string, unknown>>}
          />
        </div>
      )}
      {evidence.comparison && (
        <div className="border-t border-[var(--artha-border)] px-3 pb-3">
          <div className="mb-1 text-xs font-medium text-[var(--artha-muted)]">
            Comparison period: {evidence.comparison.how_calculated.date_range} —{" "}
            {evidence.comparison.how_calculated.records_matched.toLocaleString("en-IN")} records
          </div>
        </div>
      )}
    </details>
  );
}
