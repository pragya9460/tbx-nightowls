import { useEffect, useState } from "react";

interface CashPosition {
  available_balance: number;
  restricted_amount: number;
  protected_reserves: number;
  upcoming_commitments: number;
  true_available_cash: number;
  components: Array<{ component: string; amount: number; sign: string; provenance: string; note?: string; items?: string[] }>;
  provenance: Record<string, string>;
}

interface VendorProfile {
  vendor: string;
  transaction_count: number;
  total_spend: number;
  average_transaction: number;
  largest_transaction: number;
  last_transaction: string | null;
}

interface Anomaly {
  is_anomalous: boolean;
  counterparty: string | null;
  current_amount: number;
  historical_average: number | null;
  ratio: number | null;
  reason: string;
}

interface RulesReserves {
  rules: Array<{ rule_type: string; value: number | string; enabled: boolean; source: string }>;
  reserves: Array<{ name: string; amount: number; purpose: string; source: string }>;
}

function inr(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e7) return `₹${(v / 1e7).toFixed(2)} crore`;
  if (abs >= 1e5) return `₹${(v / 1e5).toFixed(2)} lakh`;
  return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

const PROV_BADGE: Record<string, string> = {
  OFFICIAL_DATASET: "bg-sky-50 text-sky-700",
  DERIVED: "bg-violet-50 text-violet-700",
  SYNTHETIC_DEMO: "bg-amber-50 text-amber-700",
  USER_PREFERENCE: "bg-emerald-50 text-emerald-700",
};

function Provenance({ level }: { level: string }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[9px] font-medium tracking-wide ${
        PROV_BADGE[level] ?? "bg-slate-100 text-slate-500"
      }`}
      title={`Provenance: ${level}`}
    >
      {level === "SYNTHETIC_DEMO" ? "DEMO" : level}
    </span>
  );
}

export function TwinPanel() {
  const [cash, setCash] = useState<CashPosition | null>(null);
  const [vendors, setVendors] = useState<VendorProfile[] | null>(null);
  const [anomalies, setAnomalies] = useState<Anomaly[] | null>(null);
  const [rules, setRules] = useState<RulesReserves | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [c, v, a, r] = await Promise.all([
          fetch("/api/twin/cash-position").then((x) => x.json()),
          fetch("/api/twin/vendors?limit=5").then((x) => x.json()),
          fetch("/api/twin/anomalies").then((x) => x.json()),
          fetch("/api/twin/rules").then((x) => x.json()),
        ]);
        setCash(c);
        setVendors(v.vendors ?? []);
        setAnomalies(a.anomalies ?? []);
        setRules(r);
      } catch {
        setError("Financial Twin data unavailable");
      }
    })();
  }, []);

  if (error) {
    return (
      <aside className="w-full space-y-4 text-sm">
        <div className="rounded-lg border border-[var(--artha-border)] bg-[var(--artha-panel)] p-4 text-xs text-[var(--artha-muted)]">
          {error}
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-full space-y-4 text-sm">
      {/* Cash position */}
      <section className="rounded-xl border border-[var(--artha-border)] bg-[var(--artha-panel)] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--artha-muted)]">
            Cash Position
          </h2>
          {cash && <Provenance level="DERIVED" />}
        </div>
        {cash && (
          <>
            <div className="mt-2 text-2xl font-semibold tabular-nums">
              {inr(cash.true_available_cash)}
            </div>
            <div className="text-[11px] text-[var(--artha-muted)]">
              truly available
            </div>
            <div className="mt-3 space-y-1.5 text-xs">
              {cash.components.map((c) => (
                <div key={c.component} className="flex items-baseline justify-between gap-2">
                  <span className="text-[var(--artha-muted)]">
                    {c.component}
                    {c.provenance !== "OFFICIAL_DATASET" && (
                      <span className="ml-1.5">
                        <Provenance level={c.provenance} />
                      </span>
                    )}
                  </span>
                  <span className="tabular-nums">
                    {c.sign === "-" ? `−${inr(c.amount)}` : inr(c.amount)}
                  </span>
                </div>
              ))}
              <div className="flex items-baseline justify-between border-t border-[var(--artha-border)] pt-1.5 font-medium">
                <span>Truly available</span>
                <span className="tabular-nums">{inr(cash.true_available_cash)}</span>
              </div>
            </div>
          </>
        )}
      </section>

      {/* Top vendors */}
      <section className="rounded-xl border border-[var(--artha-border)] bg-[var(--artha-panel)] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--artha-muted)]">
            Top Vendors
          </h2>
          {vendors && <Provenance level="DERIVED" />}
        </div>
        {vendors && vendors.length > 0 && (
          <div className="mt-2 space-y-1.5 text-xs">
            {vendors.map((v) => (
              <div key={v.vendor} className="flex items-baseline justify-between gap-2">
                <span className="truncate">{v.vendor}</span>
                <span className="tabular-nums text-[var(--artha-muted)]">
                  {inr(v.total_spend)}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Alerts */}
      <section className="rounded-xl border border-[var(--artha-border)] bg-[var(--artha-panel)] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--artha-muted)]">
            Alerts
          </h2>
          {anomalies && <Provenance level="DERIVED" />}
        </div>
        {anomalies && anomalies.length === 0 && (
          <div className="mt-2 text-xs text-[var(--artha-muted)]">
            ✓ No unusual transactions detected
          </div>
        )}
        {anomalies && anomalies.length > 0 && (
          <div className="mt-2 space-y-2 text-xs">
            {anomalies.map((a, i) => (
              <div key={i} className="rounded-lg bg-amber-50 px-2.5 py-2 text-amber-800">
                <div className="font-medium">⚠ Unusual transaction</div>
                <div className="mt-0.5">
                  {inr(a.current_amount)} to {a.counterparty} —{" "}
                  {a.ratio != null && `${a.ratio}× historical average`}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Rules */}
      <section className="rounded-xl border border-[var(--artha-border)] bg-[var(--artha-panel)] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-[var(--artha-muted)]">
            Rules &amp; Reserves
          </h2>
          {rules && <Provenance level="SYNTHETIC_DEMO" />}
        </div>
        {rules && (
          <div className="mt-2 space-y-1.5 text-xs">
            {rules.reserves.map((r) => (
              <div key={r.name} className="flex items-baseline justify-between gap-2">
                <span className="text-[var(--artha-muted)]">{r.name}</span>
                <span className="tabular-nums">{inr(r.amount)}</span>
              </div>
            ))}
            {rules.rules.map((r) => (
              <div key={r.rule_type} className="flex items-baseline justify-between gap-2">
                <span className="text-[var(--artha-muted)]">
                  {r.rule_type.replace(/_/g, " ")}
                </span>
                <span className="tabular-nums">
                  {r.rule_type === "approval_threshold" ? inr(Number(r.value)) : inr(Number(r.value))}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </aside>
  );
}
