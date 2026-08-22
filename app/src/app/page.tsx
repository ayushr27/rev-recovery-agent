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

      <div className="wrap">
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
}: {
  value: React.ReactNode;
  label: string;
  alert?: boolean;
}) {
  return (
    <div className={`stat${alert ? " alert" : ""}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
