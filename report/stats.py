"""Confidence intervals for the accuracy figures this project reports.

A point estimate from a small sample is the easiest way to mislead honestly-intentioned
readers: 3 correct out of 4 reads as "75%" when the data supports anything from 30% to
95%. Every accuracy this project publishes carries an interval so the reader can see how
much the number is actually worth.

Wilson rather than the textbook normal approximation, because the normal interval is
badly behaved exactly where this project lives — small n, and proportions near 0 or 1,
where it happily produces bounds below 0 or above 1.

Standard library only; this is arithmetic, not a dependency.
"""

import math


def wilson_interval(successes, n, z=1.96):
    """Wilson score interval for a binomial proportion. Returns (low, high).

    `z` defaults to 1.96, i.e. 95% confidence. An empty sample returns (0.0, 1.0) —
    no observations means no information, which is the honest answer rather than a
    division by zero or a misleading (0, 0).
    """
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError(f"successes={successes} outside 0..{n}")

    proportion = successes / n
    denominator = 1 + z**2 / n
    centre = (proportion + z**2 / (2 * n)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / n + z**2 / (4 * n**2)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def format_interval(successes, n, z=1.96):
    """Render an accuracy with its interval, e.g. '17/20 = 85.0% [64.0%, 94.8%]'."""
    low, high = wilson_interval(successes, n, z)
    rate = successes / n if n else 0.0
    return f"{successes}/{n} = {rate:.1%}  95% CI [{low:.1%}, {high:.1%}]"
