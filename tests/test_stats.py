"""The confidence interval is a published claim, so its arithmetic is checked.

An off-by-something here would not crash anything — it would quietly publish a
misleading number, which is the failure this project cares most about avoiding.
"""

import pytest

from report import stats


def test_the_interval_this_project_actually_quotes():
    # 3 of 4 is the figure the README contrasts against the larger set. Hand-checked
    # against the Wilson formula: centre 0.6275, margin 0.3269.
    low, high = stats.wilson_interval(3, 4)
    assert low == pytest.approx(0.3006, abs=0.001)
    assert high == pytest.approx(0.9544, abs=0.001)


def test_a_small_sample_is_visibly_uninformative():
    # The whole reason for reporting intervals: 75% from 4 observations spans most of
    # the possible range, and the reader can see that at a glance.
    low, high = stats.wilson_interval(3, 4)
    assert high - low > 0.6


def test_more_evidence_narrows_the_interval():
    same_rate = [stats.wilson_interval(int(0.75 * n), n) for n in (4, 40, 400)]
    widths = [high - low for low, high in same_rate]
    assert widths[0] > widths[1] > widths[2]


def test_no_observations_means_no_information():
    assert stats.wilson_interval(0, 0) == (0.0, 1.0)


def test_a_perfect_score_does_not_claim_certainty():
    # 22/22 must not report [100%, 100%] — the honest lower bound is well below 1.
    low, high = stats.wilson_interval(22, 22)
    assert high == pytest.approx(1.0, abs=0.001)
    assert 0.8 < low < 1.0


def test_a_zero_score_does_not_claim_impossibility():
    low, high = stats.wilson_interval(0, 20)
    assert low == pytest.approx(0.0, abs=0.001)
    assert 0.0 < high < 0.3


def test_the_interval_stays_inside_zero_and_one():
    # The normal approximation fails exactly here; Wilson must not.
    for n in (1, 3, 7, 50):
        for successes in range(n + 1):
            low, high = stats.wilson_interval(successes, n)
            assert 0.0 <= low <= high <= 1.0, (successes, n)


def test_the_interval_contains_the_observed_rate():
    # Tolerance is for float representation only: at 100/100 the upper bound lands one
    # ULP below 1.0. The property holds exactly in arithmetic, and both render as 100%.
    epsilon = 1e-12
    for n in (5, 20, 100):
        for successes in range(n + 1):
            low, high = stats.wilson_interval(successes, n)
            assert low - epsilon <= successes / n <= high + epsilon, (successes, n)


def test_a_wider_confidence_level_gives_a_wider_interval():
    narrow = stats.wilson_interval(15, 20, z=1.0)
    wide = stats.wilson_interval(15, 20, z=2.58)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


@pytest.mark.parametrize("successes,n", [(5, 4), (-1, 10)])
def test_impossible_counts_are_rejected(successes, n):
    with pytest.raises(ValueError):
        stats.wilson_interval(successes, n)


def test_formatting_shows_the_count_the_rate_and_the_interval():
    rendered = stats.format_interval(3, 4)
    assert "3/4" in rendered
    assert "75.0%" in rendered
    assert "95% CI" in rendered
