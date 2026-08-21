"""Single LLM interface (Groq/Gemini swap point) with a response cache.

`complete(system, user)` is the only entry point the rest of the agent calls. Every
call is cached by a hash of (system, user) in `llm_cache.json` so re-running the batch
never re-hits the API for unchanged inputs, and a warm cache lets the demo run with
zero live LLM calls.
"""

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

import config

load_dotenv()

_CACHE_PATH = Path(__file__).resolve().parent.parent / config.LLM_CACHE_PATH


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        return json.loads(_CACHE_PATH.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def _cache_key(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n---\n{user}".encode("utf-8")).hexdigest()


def _call_groq(system: str, user: str) -> str:
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to .env (see .env.example).")
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content.strip()


def _call_gemini(system: str, user: str) -> str:
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env (see .env.example).")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"),
        system_instruction=system,
    )
    response = model.generate_content(user)
    return response.text.strip()


_PROVIDERS = {"groq": _call_groq, "gemini": _call_gemini}


_CLASSIFY_SYSTEM = """You classify failed payment records into exactly one recovery category.

Reply with ONLY the category label in lowercase. No punctuation, no explanation.

bank_downtime - a transient failure at the bank, gateway or network. The instrument is
fine and retrying later will probably succeed.
insufficient_funds - the customer's account did not have the balance to cover it. The
instrument is fine; a retry may succeed once they top up.
dead_instrument - the instrument itself is permanently unusable: expired, blocked,
deactivated, cancelled or invalid. It must never be retried.
exhausted - attempts are used up, or the case has been given up on and needs a human.

Weigh the description text over the error codes when they disagree; the codes on these
records are sometimes wrong."""


def _classify_prompt(record) -> str:
    """Stable prompt text — no timestamps, so repeated runs hit the cache."""
    fields = (
        "method",
        "error_code",
        "error_source",
        "error_step",
        "error_reason",
        "error_description",
        "retry_count",
    )
    return "\n".join(f"{field}: {record.get(field)}" for field in fields)


def classify_failure(record, categories, fallback="exhausted") -> dict:
    """Resolve a record the rules table could not, constrained to `categories`.

    Returns {"category", "valid", "raw"}. Anything the model says that is not exactly
    one of the four labels is discarded and `fallback` used instead — an out-of-spec
    label means a human should look, not that the agent should guess an action. `valid`
    is reported so metrics can count these honestly rather than hiding them.
    """
    try:
        raw = complete(_CLASSIFY_SYSTEM, _classify_prompt(record))
    except Exception as exc:  # noqa: BLE001 - fail closed, never abort the batch
        return {"category": fallback, "valid": False, "raw": f"<error: {exc}>"}

    label = raw.strip().lower().strip(".`'\"* \n")
    if label not in categories:
        # Tolerate a model that wraps the label in a sentence, but only when exactly
        # one category is named — two candidates means it did not actually decide.
        named = [c for c in categories if c in label]
        label = named[0] if len(named) == 1 else None

    if label not in categories:
        return {"category": fallback, "valid": False, "raw": raw}
    return {"category": label, "valid": True, "raw": raw}


def complete(system: str, user: str) -> str:
    """Return an LLM completion for (system, user), served from cache when possible."""
    cache = _load_cache()
    key = _cache_key(system, user)
    if key in cache:
        return cache[key]

    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider not in _PROVIDERS:
        raise RuntimeError(
            f"LLM_PROVIDER must be one of {sorted(_PROVIDERS)}, got {provider!r}. "
            "Set it in .env (see .env.example)."
        )

    result = _PROVIDERS[provider](system, user)
    cache[key] = result
    _save_cache(cache)
    return result
