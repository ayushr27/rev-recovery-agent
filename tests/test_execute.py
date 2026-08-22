"""Execution honesty: the recovered figure must not be inflatable.

No test here touches the network. The real-API path is exercised only with credentials
forced empty, which proves the fallback without creating a Razorpay object.
"""

import config
from core import decide, execute, gate

RECORD = {"id": "pay_TEST00011", "amount": 1_056_863, "currency": "INR"}


def gate_result(action, decision=gate.ALLOW, reason=None):
    return gate.GateResult(decision, action, reason, "idem_test", False)


# --- the invariant the rupee number rests on ----------------------------------


def test_dead_instrument_retry_can_never_succeed():
    # Even reaching simulate() directly, bypassing the gate entirely, a retry on an
    # unrecoverable category cannot report a capture. This is the last line of defence
    # for the honesty of the recovered figure.
    result = execute.simulate(RECORD, "dead_instrument", decide.RETRY)
    assert result["status"] == execute.FAILED


def test_exhausted_retry_can_never_succeed():
    result = execute.simulate(RECORD, "exhausted", decide.RETRY)
    assert result["status"] == execute.FAILED


def test_unrecoverable_retry_fails_even_if_a_success_rate_is_configured(monkeypatch):
    # The guard is structural, not probabilistic: configuring a 100% success rate for
    # an unrecoverable category still cannot produce a capture.
    monkeypatch.setitem(config.SIMULATED_RETRY_SUCCESS_RATE, "dead_instrument", 1.0)
    result = execute.simulate(RECORD, "dead_instrument", decide.RETRY)
    assert result["status"] == execute.FAILED


def test_recoverable_categories_can_succeed():
    # The mirror of the above — if nothing could ever succeed the metric is equally
    # meaningless. Across many ids both outcomes must occur.
    outcomes = {
        execute.simulate({"id": f"pay_TEST{i:05d}"}, "bank_downtime", decide.RETRY)["status"]
        for i in range(50)
    }
    assert outcomes == {execute.CAPTURED, execute.FAILED}


# --- determinism --------------------------------------------------------------


def test_outcome_is_deterministic_for_a_payment():
    first = execute.simulate(RECORD, "bank_downtime", decide.RETRY)
    second = execute.simulate(RECORD, "bank_downtime", decide.RETRY)
    assert first == second


def test_outcome_does_not_depend_on_processing_order():
    # Outcomes hash the payment id rather than drawing from a running rng, so the
    # recovered total is identical however the batch is ordered.
    ids = [f"pay_TEST{i:05d}" for i in range(20)]
    forward = [execute.simulate({"id": i}, "bank_downtime", decide.RETRY) for i in ids]
    backward = [execute.simulate({"id": i}, "bank_downtime", decide.RETRY) for i in reversed(ids)]
    assert forward == list(reversed(backward))


# --- gate refusals and the real-API fallback ----------------------------------


def test_refused_actions_are_not_executed():
    refused = gate.GateResult(gate.REFUSE, None, gate.RETRY_CAP_EXCEEDED, None, False)
    result = execute.execute(RECORD, "bank_downtime", refused)
    assert result["status"] == execute.NOT_ATTEMPTED
    assert gate.RETRY_CAP_EXCEEDED in result["detail"]


def test_missing_credentials_degrade_to_simulation(monkeypatch):
    # A missing key must not abort the batch — it falls back with a logged notice.
    monkeypatch.setenv("RAZORPAY_KEY_ID", "")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "")
    result = execute.execute(
        RECORD, "dead_instrument", gate_result(decide.SEND_LINK), use_real_api=True
    )
    assert result["simulated"] is True
    assert result["status"] == execute.LINK_SENT


def test_a_failed_real_call_is_never_reported_as_simulated(monkeypatch):
    """The audit must not claim an action was simulated when a real request went out.

    The try block used to wrap the call AND the response handling, so a read timeout
    after Razorpay had already created the link fell through to simulate() and wrote
    `simulated: true` with a fabricated plink_SIM reference. A trail that asserts a real
    action was fake is worse than one that admits it does not know.
    """
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_dummy")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "dummy")

    def timeout(*_args, **_kwargs):
        raise TimeoutError("read timed out waiting for the response")

    monkeypatch.setattr(execute, "razorpay_testmode_create_link", timeout)
    result = execute.execute(
        RECORD, "dead_instrument", gate_result(decide.SEND_LINK), use_real_api=True
    )
    assert result["status"] == execute.UNVERIFIED
    assert result["simulated"] is False
    assert result["provider_ref"] is None
    assert "unknown" in result["detail"]


def test_an_unrecognised_response_is_also_unverified(monkeypatch):
    # The request succeeded, so a link almost certainly exists even though we cannot name
    # it. Claiming simulation here would be the same lie in a different shape.
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_dummy")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "dummy")
    monkeypatch.setattr(execute, "razorpay_testmode_create_link", lambda _r: {"unexpected": 1})
    result = execute.execute(
        RECORD, "dead_instrument", gate_result(decide.SEND_LINK), use_real_api=True
    )
    assert result["status"] == execute.UNVERIFIED
    assert result["simulated"] is False


def test_a_successful_real_call_is_marked_real(monkeypatch):
    # The mirror of the two above — without it they would pass on a function that always
    # returned UNVERIFIED.
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_dummy")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "dummy")
    monkeypatch.setattr(
        execute, "razorpay_testmode_create_link", lambda _r: {"id": "plink_FAKE123"}
    )
    result = execute.execute(
        RECORD, "dead_instrument", gate_result(decide.SEND_LINK), use_real_api=True
    )
    assert result["status"] == execute.LINK_SENT
    assert result["simulated"] is False
    assert result["provider_ref"] == "plink_FAKE123"


def test_simulated_run_makes_no_api_call_by_default(monkeypatch):
    # use_real_api defaults to False, so a plain batch run never reaches the network.
    def explode(*_args, **_kwargs):
        raise AssertionError("no network call should happen without use_real_api")

    monkeypatch.setattr(execute, "razorpay_testmode_create_link", explode)
    assert execute.execute(RECORD, "dead_instrument", gate_result(decide.SEND_LINK))


def test_converted_afa_action_sends_an_authentication_link():
    result = execute.simulate(RECORD, "bank_downtime", decide.REQUIRES_AFA)
    assert result["status"] == execute.LINK_SENT
    assert "authentication" in result["detail"]


def test_escalation_takes_no_automated_action():
    result = execute.simulate(RECORD, "exhausted", decide.ESCALATE)
    assert result["status"] == execute.ESCALATED
