"""The model is the least predictable component, so its containment is tested.

Two claims in the README depend on this file: that an out-of-spec label defaults to
human escalation rather than a wrong action, and that a warm cache makes the run
hermetic. Neither should be believed without a test.

Nothing here touches the network — `complete` is always replaced — and the cache tests
redirect to a temp path so the committed llm_cache.json is never written.
"""

import json

import pytest

from core import decide, diagnose, explain, gate, llm

CATEGORIES = diagnose.CATEGORIES


@pytest.fixture
def says(monkeypatch):
    """Force the model to return a given string."""

    def _says(text):
        monkeypatch.setattr(llm, "complete", lambda system, user: text)

    return _says


# --- constrained output: only the four labels may escape -----------------------


@pytest.mark.parametrize("label", CATEGORIES)
def test_each_valid_label_is_accepted(says, label):
    says(label)
    result = llm.classify_failure({}, CATEGORIES)
    assert result == {"category": label, "valid": True, "raw": label}


@pytest.mark.parametrize(
    "noisy",
    ["BANK_DOWNTIME", "  bank_downtime  ", "`bank_downtime`", "bank_downtime.", "**bank_downtime**"],
)
def test_formatting_noise_is_tolerated(says, noisy):
    # Models add punctuation and casing; that is not the same as being wrong.
    says(noisy)
    assert llm.classify_failure({}, CATEGORIES)["category"] == "bank_downtime"


def test_a_label_wrapped_in_a_sentence_is_still_read(says):
    says("This looks like dead_instrument to me.")
    result = llm.classify_failure({}, CATEGORIES)
    assert result["category"] == "dead_instrument"
    assert result["valid"] is True


def test_naming_two_categories_is_treated_as_undecided(says):
    # "bank_downtime or insufficient_funds" is not a decision. Picking the first would
    # silently invent an answer the model did not give.
    says("could be bank_downtime or insufficient_funds")
    result = llm.classify_failure({}, CATEGORIES)
    assert result["category"] == "exhausted"
    assert result["valid"] is False


@pytest.mark.parametrize(
    "junk",
    ["", "   ", "no idea", "CATEGORY_5", "null", "I cannot determine this from the information given"],
)
def test_out_of_spec_output_escalates_to_a_human(says, junk):
    # The README claim: an out-of-spec label becomes a human's problem, never an action.
    says(junk)
    result = llm.classify_failure({}, CATEGORIES)
    assert result["category"] == "exhausted"
    assert result["valid"] is False


def test_a_provider_failure_does_not_abort_the_batch(monkeypatch):
    def explode(system, user):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(llm, "complete", explode)
    result = llm.classify_failure({}, CATEGORIES)
    assert result["category"] == "exhausted"
    assert result["valid"] is False
    assert "rate limited" in result["raw"]


def test_the_fallback_is_always_a_real_category():
    # Whatever happens, decide() must be able to act on the result.
    assert decide.decide("exhausted").action == decide.ESCALATE


def test_invalid_results_are_reported_not_hidden(says):
    # metrics counts these; swallowing them would overstate diagnosis accuracy.
    says("garbage")
    assert llm.classify_failure({}, CATEGORIES)["valid"] is False


# --- caching: what makes the demo hermetic ------------------------------------


def test_a_cache_hit_makes_no_provider_call(monkeypatch, tmp_path):
    cache = tmp_path / "llm_cache.json"
    cache.write_text(json.dumps({llm._cache_key("sys", "usr"): "cached answer"}))
    monkeypatch.setattr(llm, "_CACHE_PATH", cache)

    def explode(*_a, **_k):
        raise AssertionError("provider was called despite a cache hit")

    monkeypatch.setitem(llm._PROVIDERS, "groq", explode)
    monkeypatch.setenv("LLM_PROVIDER", "groq")

    assert llm.complete("sys", "usr") == "cached answer"


def test_a_miss_is_written_back_so_the_next_run_is_hermetic(monkeypatch, tmp_path):
    cache = tmp_path / "llm_cache.json"
    monkeypatch.setattr(llm, "_CACHE_PATH", cache)
    monkeypatch.setitem(llm._PROVIDERS, "groq", lambda s, u: "fresh answer")
    monkeypatch.setenv("LLM_PROVIDER", "groq")

    assert llm.complete("sys", "usr") == "fresh answer"
    assert json.loads(cache.read_text())[llm._cache_key("sys", "usr")] == "fresh answer"


def test_cache_key_separates_different_inputs():
    assert llm._cache_key("a", "b") == llm._cache_key("a", "b")
    assert llm._cache_key("a", "b") != llm._cache_key("a", "c")
    assert llm._cache_key("a", "b") != llm._cache_key("c", "b")


def test_classification_prompt_is_stable_across_runs():
    # If the prompt varied — say it embedded a timestamp — every run would miss the
    # cache and the demo would depend on a live API.
    record = {"method": "upi", "error_code": "GATEWAY_ERROR", "error_reason": "timeout"}
    assert llm._classify_prompt(record) == llm._classify_prompt(dict(record))


def test_missing_provider_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "_CACHE_PATH", tmp_path / "empty.json")
    monkeypatch.setenv("LLM_PROVIDER", "")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        llm.complete("sys", "usr")


# --- explanations degrade rather than disappear -------------------------------


def trace(**overrides):
    base = {
        "payment_id": "pay_TEST00001",
        "payment_type": "recurring",
        "amount": 49_900,
        "category": "bank_downtime",
        "source": "rules",
        "intervention": "retry",
        "gate_result": gate.ALLOW,
        "action": "retry",
        "refusal_reason": None,
        "execution_status": "captured",
    }
    base.update(overrides)
    return base


def test_an_llm_failure_still_produces_a_rationale(monkeypatch):
    # A blank rationale would leave a hole in the audit trail.
    def explode(*_a, **_k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm, "complete", explode)
    sentence = explain.explain(trace())
    assert sentence
    assert "pay_TEST00001" in sentence


def test_an_empty_model_response_falls_back_to_the_template(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda s, u: "   ")
    assert explain.explain(trace()).strip()


def test_explanations_can_be_switched_off_for_dev_runs(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("no LLM call should happen when disabled")

    monkeypatch.setattr(llm, "complete", explode)
    assert explain.explain(trace(), use_llm=False)


def test_a_refusal_is_explained_as_a_refusal():
    sentence = explain.explain(
        trace(
            gate_result=gate.REFUSE,
            action=None,
            refusal_reason=gate.DEAD_INSTRUMENT_NEVER_RETRIED,
            execution_status="not_attempted",
        ),
        use_llm=False,
    )
    assert "No action taken" in sentence
    assert "dead" in sentence.lower()


def test_an_afa_conversion_is_explained_as_a_conversion():
    sentence = explain.explain(
        trace(
            gate_result=gate.CONVERT,
            action="requires_afa",
            refusal_reason=gate.AFA_REQUIRED_ABOVE_THRESHOLD,
            execution_status="link_sent",
        ),
        use_llm=False,
    )
    assert "authentication link" in sentence
    assert "15,000" in sentence


def test_the_explain_prompt_excludes_the_timestamp():
    # Same decision, different moments — the prompt must not change, or the cache
    # would miss on every run and the demo would stop being hermetic.
    assert explain._prompt(trace()) == explain._prompt({**trace(), "timestamp": 1_755_000_000})
