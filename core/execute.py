"""Carries out a gated intervention: simulated by default (seeded, deterministic),
with one real Razorpay test-mode payment-link path for demo credibility.

Part of the I/O layer, not the decision core — nothing here decides anything. It
receives a GateResult that has already been allowed and does what it says.

Outcomes are a hash of the payment id rather than draws from a running rng, so the
recovered rupee figure does not depend on iteration order and does not drift between
runs. The one honesty invariant that matters: a retry can only ever succeed for a
recoverable category. An unrecoverable failure cannot be simulated into a recovery.
"""

import hashlib
import os

from dotenv import load_dotenv

import config
from core import decide

load_dotenv()

# Execution outcomes.
CAPTURED = "captured"
FAILED = "failed"
LINK_SENT = "link_sent"
ESCALATED = "escalated"
NOT_ATTEMPTED = "not_attempted"  # the gate refused; nothing was fired


def _roll(payment_id, salt):
    """Deterministic pseudo-random value in [0, 1) for this payment and purpose."""
    raw = f"{payment_id}|{salt}|{config.SIMULATION_SEED}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0x1_0000_0000


def _result(status, detail, provider_ref=None, simulated=True):
    return {
        "status": status,
        "detail": detail,
        "provider_ref": provider_ref,
        "simulated": simulated,
    }


def simulate(record, category, action):
    """Produce a deterministic fake outcome for an already-gated action."""
    payment_id = record["id"]

    if action == decide.RETRY:
        # Structural guard, not a probability. Even if a rate were configured for an
        # unrecoverable category, a dead instrument can never be retried into success.
        # The gate blocks this long before here; this is the second line of defence.
        if category not in decide.RECOVERABLE_CATEGORIES:
            return _result(
                FAILED,
                f"retry attempted on unrecoverable category {category}; cannot succeed",
            )
        rate = config.SIMULATED_RETRY_SUCCESS_RATE.get(category, 0.0)
        if _roll(payment_id, "retry") < rate:
            digest = hashlib.sha256(f"{payment_id}|ref".encode("utf-8")).hexdigest()[:14]
            return _result(
                CAPTURED,
                f"retry succeeded after the {category} condition cleared",
                provider_ref=f"pay_SIM{digest}",
            )
        return _result(FAILED, f"retry attempted; the {category} condition persisted")

    if action in (decide.SEND_LINK, decide.REQUIRES_AFA):
        label = (
            "authentication link sent for customer approval"
            if action == decide.REQUIRES_AFA
            else "update-payment-method link sent"
        )
        digest = hashlib.sha256(payment_id.encode("utf-8")).hexdigest()[:14]
        return _result(LINK_SENT, label, provider_ref=f"plink_SIM{digest}")

    if action == decide.ESCALATE:
        return _result(ESCALATED, "queued for human review; no automated action taken")

    return _result(NOT_ATTEMPTED, f"no execution path for action {action!r}")


def razorpay_testmode_create_link(record):
    """Create a REAL Razorpay test-mode Payment Link and return the API response.

    Test mode only — no real money moves. Notifications are explicitly disabled: these
    are synthetic customers with placeholder contact details and nothing should ever be
    sent to anyone. Raises if credentials are missing; the caller degrades to simulate.
    """
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not (key_id and key_secret):
        raise RuntimeError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set")

    import razorpay

    client = razorpay.Client(auth=(key_id, key_secret))
    return client.payment_link.create(
        data={
            "amount": record["amount"],
            "currency": record.get("currency", "INR"),
            "description": f"Update payment method for {record['id']}",
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
        }
    )


def execute(record, category, gate_result, use_real_api=False):
    """Run the gated action. Returns an execution-result dict for the audit trail."""
    if not gate_result.allowed:
        return _result(NOT_ATTEMPTED, f"refused by gate: {gate_result.reason}")

    action = gate_result.action

    if use_real_api and action in (decide.SEND_LINK, decide.REQUIRES_AFA):
        try:
            link = razorpay_testmode_create_link(record)
            return _result(
                LINK_SENT,
                "real Razorpay test-mode payment link created",
                provider_ref=link["id"],
                simulated=False,
            )
        except Exception as exc:  # noqa: BLE001 - degrade rather than abort the batch
            print(f"[execute] real Razorpay call unavailable ({exc}); simulating instead")

    return simulate(record, category, action)
