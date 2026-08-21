"""Loads failed-payment fixtures and strips eval-only ground-truth fields before
records reach the rest of the pipeline, so diagnosis/decide/gate can never read them.

This is the input half of the I/O layer. `load_failures()` is what the pipeline calls;
`load_ground_truth()` exists solely for `report/metrics.py` and must not be called from
the decision core.
"""

import json
from pathlib import Path

import config

# Fields the agent is forbidden to see. They exist only so metrics can score the run.
EVAL_ONLY_FIELDS = ("_ground_truth_category", "_recoverable")

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / config.FIXTURES_PATH


def _read(path):
    return json.loads(Path(path or _DEFAULT_PATH).read_text())


def strip_eval_fields(records):
    """Drop every eval-only field from an in-memory batch."""
    return [
        {k: v for k, v in record.items() if k not in EVAL_ONLY_FIELDS}
        for record in records
    ]


def ground_truth_from(records):
    """Build the ground-truth map from an in-memory batch. metrics.py ONLY."""
    return {
        record["id"]: {
            "category": record["_ground_truth_category"],
            "recoverable": record["_recoverable"],
        }
        for record in records
    }


def load_failures(path=None):
    """Return the batch with every eval-only field removed."""
    return strip_eval_fields(_read(path))


def load_ground_truth(path=None):
    """Return {payment_id: {category, recoverable}} — for report/metrics.py ONLY."""
    return ground_truth_from(_read(path))
