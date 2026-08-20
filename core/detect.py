"""Loads failed-payment fixtures and strips eval-only ground-truth fields before
records reach the rest of the pipeline, so diagnosis/decide/gate can never read them.
"""
