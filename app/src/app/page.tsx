import fs from "node:fs";
import path from "node:path";

// The batch writes both of these at the repo root. The page only renders what the
// agent already decided — no orchestration happens in the browser.
const ROOT = path.join(process.cwd(), "..");

// Re-read on every request, so refreshing after a batch run shows the new numbers
// instead of a copy captured at build time.
export const dynamic = "force-dynamic";

type Metrics = {
  total: number;
  recoverable: number;
  dead: number;
  recovered: number;
  recovery_rate: number;
  escalated: number;
  refused: number;
  refused_by_reason: Record<string, number>;
  budget_exhausted: boolean;
  afa_gated: number;
  false_intervention: number;
  impossible_recoveries: number;
  diagnosis_accuracy: number;
  diagnosis_by_source: Record<string, { total: number; correct: number }>;
  misclassified: number;
  recoverable_paise: number;
  recovered_paise: number;
  afa_paise: number;
};

type AuditRow = {
  payment_id: string;
  payment_type: string;
  amount: number;
  category: string;
  source: string;
  intervention: string;
  action_taken: string | null;
  gate_result: "allow" | "refuse" | "convert";
  refusal_reason: string | null;
  idempotency_key: string | null;
  pre_debit_notified: boolean;
  execution_result: {
    status: string;
    detail: string;
    provider_ref: string | null;
    simulated: boolean;
  };
  rationale: string;
};

function read<T>(file: string, parse: (raw: string) => T): T | null {
  try {
    return parse(fs.readFileSync(path.join(ROOT, file), "utf8"));
  } catch {
    return null;
  }
}

const rupees = (paise: number) =>
  `₹${(paise / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

export default function Home() {
  const metrics = read<Metrics>("metrics.json", JSON.parse);
  const rows =
    read<AuditRow[]>("audit_log.jsonl", (raw) =>
      raw
        .split("\n")
        .filter((line) => line.trim())
        .map((line) => JSON.parse(line)),
    ) ?? [];

  if (!metrics) {
    return (
      <main>
        <h1>Revenue Recovery Agent</h1>
        <div className="empty">
          No run found. Generate one from the repo root with <code>python run_batch.py</code>,
          then refresh.
        </div>
      </main>
    );
  }

  return (
    <main>
      <h1>Revenue Recovery Agent — batch audit</h1>

      <section className="headline">
        <div className="amount">
          {rupees(metrics.recovered_paise)}
          <small>
            recovered of {rupees(metrics.recoverable_paise)} recoverable, across{" "}
            {metrics.total} failed payments — {metrics.recovered} of {metrics.recoverable}{" "}
            recoverable payments ({(metrics.recovery_rate * 100).toFixed(1)}%)
          </small>
        </div>
        <div className="baseline">
          Without the agent: <strong>0 recovered</strong> — all{" "}
          {rupees(metrics.recoverable_paise)} stays failed. A further{" "}
          {rupees(metrics.afa_paise)} awaits customer authentication ({metrics.afa_gated}{" "}
          payments) and is not written off.
        </div>
        {/* The figure above is simulated. Saying so on the artifact itself, rather than
            only in the README, is the same honesty the rest of the project claims. */}
        <div className="caveat">
          <strong>Simulated outcomes.</strong> Retry success rates (75% bank downtime, 45%
          insufficient funds) are plausible values, not measured from real data. At ±20% on
          those rates the figure moves between ₹1,28,518 and ₹1,73,183; it cannot exceed
          ₹2,21,677 because only 28 payments were ever allowed a retry. Run{" "}
          <code>python scripts/analyze_value.py</code> for the full band.
        </div>
      </section>

      <section className="strip">
        <Stat value={metrics.recoverable} label="recoverable" />
        <Stat value={metrics.dead} label="dead — never retried" />
        <Stat value={metrics.escalated} label="escalated" />
        <Stat value={metrics.refused} label="refused by gate" />
        <Stat value={metrics.afa_gated} label="AFA-gated" />
        <Stat
          value={`${(metrics.diagnosis_accuracy * 100).toFixed(0)}%`}
          label="diagnosis accuracy"
          note={`95% CI ${wilson(
            metrics.total - metrics.misclassified,
            metrics.total,
          )} · ${metrics.diagnosis_by_source.rules?.correct ?? 0}/${
            metrics.diagnosis_by_source.rules?.total ?? 0
          } by rules`}
        />
        <Stat
          value={metrics.false_intervention}
          label="false interventions"
          alert={metrics.false_intervention > 0}
        />
      </section>

      {metrics.budget_exhausted && (
        <div className="note">
          The global attempt budget was reached, so payments after that point were never
          actioned. The recovery rate above is bounded by that cap — not by the agent
          failing to recover.
        </div>
      )}

      {/* Both README and SCENARIOS promise "colour-coded" refusals; until now nothing on
          the page said what the tints meant. */}
      <div className="legend">
        <span><i className="swatch refused" /> refused — nothing fired</span>
        <span><i className="swatch converted" /> converted — retry blocked, auth link sent instead</span>
        <span><i className="swatch escalated" /> escalated — handed to a human</span>
        <span><i className="swatch allowed" /> allowed — the action fired</span>
      </div>

      <div className="wrap" tabIndex={0} role="region" aria-label="Per-payment audit trail">
        <table>
          <colgroup>
            <col className="c-payment" />
            <col className="c-category" />
            <col className="c-source" />
            <col className="c-intervention" />
            <col className="c-gate" />
            <col className="c-outcome" />
            <col className="c-rationale" />
          </colgroup>
          <thead>
            <tr>
              <th>Payment</th>
              <th>Category</th>
              <th>Source</th>
              <th>Intervention</th>
              <th>Gate</th>
              <th>Outcome</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.payment_id} className={rowClass(row)}>
                <td className="mono">
                  {row.payment_id}
                  <br />
                  {rupees(row.amount)}
                </td>
                <td>
                  {row.category}
                  <br />
                  <span className="mono">{row.payment_type}</span>
                </td>
                <td>
                  <span className={`badge ${row.source.startsWith("llm") ? "llm" : "rules"}`}>
                    {row.source.startsWith("llm") ? "LLM" : "rules"}
                  </span>
                </td>
                <td>
                  {row.intervention}
                  {row.action_taken && row.action_taken !== row.intervention && (
                    <>
                      <br />
                      <span className="mono">→ {row.action_taken}</span>
                    </>
                  )}
                </td>
                <td>
                  <span className={`badge ${row.gate_result}`}>{row.gate_result}</span>
                  {row.refusal_reason && <span className="reason">{row.refusal_reason}</span>}
                </td>
                <td>
                  {row.execution_result.status}
                  {!row.execution_result.simulated && <span className="real">REAL</span>}
                  {row.execution_result.provider_ref && (
                    <>
                      <br />
                      <span className="mono">{row.execution_result.provider_ref}</span>
                    </>
                  )}
                </td>
                <td className="rationale">{row.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}

function rowClass(row: AuditRow) {
  if (row.gate_result === "refuse") return "refused";
  if (row.gate_result === "convert") return "converted";
  if (row.action_taken === "escalate") return "escalated";
  return "";
}

function Stat({
  value,
  label,
  alert = false,
  note,
}: {
  value: React.ReactNode;
  label: string;
  alert?: boolean;
  note?: string;
}) {
  return (
    <div className={`stat${alert ? " alert" : ""}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
      {note && <div className="note-sm">{note}</div>}
    </div>
  );
}

// Wilson score interval — the same arithmetic as report/stats.py, so the page and the CLI
// report never disagree. A bare "98%" invites a confidence the sample cannot support.
function wilson(successes: number, n: number, z = 1.96) {
  if (n <= 0) return "[0.0%, 100.0%]";
  const p = successes / n;
  const d = 1 + (z * z) / n;
  const centre = p + (z * z) / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  const low = Math.max(0, (centre - margin) / d);
  const high = Math.min(1, (centre + margin) / d);
  return `[${(low * 100).toFixed(1)}%, ${(high * 100).toFixed(1)}%]`;
}
