"""Stateful stopping rules: retry caps, cooling-off windows, dead-instrument and
AFA-threshold refusals, global attempt budget, and idempotency — reads/writes
run_state.json. This is the differentiator; every bound reads from config.py.
"""
