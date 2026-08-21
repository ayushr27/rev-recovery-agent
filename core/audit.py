"""Append-only JSONL audit trail (audit_log.jsonl) — one JSON object per decision,
including idempotency_key and pre_debit_notified, so every action is traceable.

The output half of the I/O layer. JSONL rather than JSON so the file is appended to
rather than rewritten, stays readable with `tail`, and survives an interrupted run with
every completed decision intact.

Key order is fixed and meaningful: it walks the decision path in the order the agent
actually took it, so a reviewer reads each line left to right as a narrative — what
came in, how it was classified, what was planned, what the gate said, what was done,
and why.
"""

import json
from pathlib import Path

import config

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / config.AUDIT_LOG_PATH


def _path(path=None):
    return Path(path or _DEFAULT_PATH)


def entry(record, category, source, intervention, gate_result, execution_result, rationale, timestamp):
    """Assemble one audit object. Field order follows the decision path."""
    return {
        "payment_id": record["id"],
        "payment_type": record.get("payment_type"),
        "amount": record.get("amount"),
        "input_summary": (
            f"{record.get('method')} {record.get('error_code')}/"
            f"{record.get('error_reason')} at {record.get('error_step')}, "
            f"retry_count={record.get('retry_count')}"
        ),
        "category": category,
        "source": source,
        "intervention": intervention.action,
        "gate_result": gate_result.decision,
        "refusal_reason": gate_result.reason,
        "idempotency_key": gate_result.idempotency_key,
        "pre_debit_notified": gate_result.pre_debit_notified,
        "execution_result": execution_result,
        "rationale": rationale,
        "timestamp": timestamp,
    }


def reset(path=None):
    """Clear the log so a run starts fresh. Append-only *within* a run."""
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")


def append(audit_entry, path=None):
    """Append one decision to the trail."""
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")


def read_all(path=None):
    """Read the trail back. Used by metrics and the UI, never by the decision core."""
    target = _path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text().splitlines() if line.strip()]
