"""Throwaway Phase 0 Definition of Done check: prove core.llm.complete() works and
caches. Not part of the agent pipeline — safe to delete after Phase 0 is confirmed.
"""

from core import llm

if __name__ == "__main__":
    print("First call (should hit the API or error clearly if no key is set):")
    print(llm.complete("You are terse.", "Say OK"))

    print("\nSecond identical call (should be served from llm_cache.json, no API hit):")
    print(llm.complete("You are terse.", "Say OK"))
