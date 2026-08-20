# SCENARIOS.md — demo beats

Filled in during Phase 1 after fixture generation, with the actual payment ids that
hit each named demo beat. If any beat has no matching id, fixtures get adjusted until
it does — this file is what keeps fixture generation honest.

| Beat                                                              | Payment id     | Notes |
|---------------------------------------------------------------------|----------------|-------|
| Successful recovery (recoverable → retry → captured)                | TBD            | Needs Phase 4's seeded `execute()` outcome — pick once that lands. |
| Dead-card retry refused (`dead_instrument`)                         | `pay_TEST00011`| `invalid_card`, retry_count=0. Gate must refuse any retry. |
| AFA-threshold refusal (recoverable, `recurring`, > ₹15,000)          | `pay_TEST00003`| `insufficient_funds`, recurring, amount ₹87,970.25 (> 1,500,000 paise). 4 such records exist total. |
| Graceful escalation (`exhausted` / retry cap hit)                   | `pay_TEST00006`| retry_count == MAX_RETRY_ATTEMPTS (3) already. |
| LLM-resolved ambiguous case (rules returned `needs_llm`)             | `pay_TEST00024`| One of 4 atypical-signature records; ground truth `dead_instrument` (`authentication_failed`, source mismatched as `bank`), description-only signal. |
