"""Orchestrates detect -> diagnose (rules -> llm) -> decide -> gate -> execute ->
explain -> audit over a batch of failed payments, then prints the metrics report.
This is the single source of truth for the agent loop — keep it readable.
"""
