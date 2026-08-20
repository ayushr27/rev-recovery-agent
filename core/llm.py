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
