"""Scoring functions shared by the report card and the backtest.

Player projections: error overall and by outcome group, and how well the
projection ordered players within each position (Spearman). Match odds: log
loss, ranked probability score and Brier. Comparing two methods week by week:
paired t, sign-flip permutation and a confidence interval. Rates: Wilson
intervals and reliability bins.
"""

import math
import random
from dataclasses import dataclass

CATEGORIES = ("dnp", "low", "mid", "haul")
_GROUPS = {"GK": "GK", "D": "DEF", "DEF": "DEF", "M": "MID", "MID": "MID", "F": "FWD", "FWD": "FWD"}
_OUTCOMES = ("home", "draw", "away")
_EPS = 1e-6
# Two-sided 95% t critical values by degrees of freedom; 1.96 beyond 30.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
       11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
       20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
       29: 2.045, 30: 2.042}


@dataclass
class Scored:
    position: str | None
    proj: float
    actual: float
    cat: str


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def position_group(positions):
    """GK / DEF / MID / FWD from Sleeper's (or FPL-style) positions, primary first."""
    return next((_GROUPS[p] for p in positions or [] if p in _GROUPS), None)


def category(stats, points, low, haul):
    if stats is None or (stats.get("min") or 0) <= 0:
        return "dnp"
    return "low" if points < low else "haul" if points >= haul else "mid"


def error_summary(rows):
    def mae(rs):
        return mean(abs(r.proj - r.actual) for r in rs)
    played = [r for r in rows if r.cat != "dnp"]
    by_cat = {}
    for cat in CATEGORIES:
        group = [r for r in rows if r.cat == cat]
        by_cat[cat] = {"n": len(group), "mae": mae(group)}
    return {"n": len(rows), "mae": mae(rows), "n_played": len(played), "mae_played": mae(played),
            "by_cat": by_cat}


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1   # ties share the average rank
        i = j + 1
    return ranks


def spearman(xs, ys):
    """Rank correlation; None when there are too few points or one side never varies."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = mean(rx), mean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def rank_by_position(rows, min_players=5):
    """Mean per-position Spearman over positions with enough players; None if none qualify."""
    groups = {}
    for r in rows:
        if r.position is not None:
            groups.setdefault(r.position, []).append(r)
    values = []
    for group in groups.values():
        if len(group) >= min_players:
            rho = spearman([r.proj for r in group], [r.actual for r in group])
            if rho is not None:
                values.append(rho)
    return mean(values)


def log_loss(p_home, p_draw, p_away, outcome):
    p = dict(zip(_OUTCOMES, (p_home, p_draw, p_away)))[outcome]
    return -math.log(max(p, _EPS))


def rps(p_home, p_draw, p_away, outcome):
    o = [1.0 if outcome == k else 0.0 for k in _OUTCOMES]
    return ((p_home - o[0]) ** 2 + (p_home + p_draw - o[0] - o[1]) ** 2) / 2


def brier(p, happened):
    return (p - (1.0 if happened else 0.0)) ** 2


def wilson(successes, n, z=1.645):
    """Interval for a rate (90% by default); (0, 1) when there's nothing to go on."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def _sd(d):
    m = mean(d)
    return math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1))


def paired_t(d):
    """(t, degrees of freedom) for per-week differences; t is None when it can't be computed."""
    d = list(d)
    if len(d) < 2:
        return (None, max(len(d) - 1, 0))
    sd = _sd(d)
    if sd == 0:
        return (None, len(d) - 1)
    return (mean(d) / (sd / math.sqrt(len(d))), len(d) - 1)


def mean_ci(d):
    """95% interval for the mean of per-week differences, or None with fewer than 2."""
    d = list(d)
    if len(d) < 2:
        return None
    half = T95.get(len(d) - 1, 1.96) * _sd(d) / math.sqrt(len(d))
    return (mean(d) - half, mean(d) + half)


def signflip_p(d, b=20000, seed=1):
    """Two-sided permutation p-value: how often random signs give a total this far from 0."""
    d = list(d)
    if not d:
        return None
    rnd = random.Random(seed)
    obs = abs(sum(d))
    hits = sum(1 for _ in range(b) if abs(sum(x if rnd.random() < 0.5 else -x for x in d)) >= obs - 1e-12)
    return (hits + 1) / (b + 1)


def reliability(pairs, bins=10):
    """Predicted vs observed rate in equal-width probability bins (non-empty bins only)."""
    groups = {}
    for p, happened in pairs:
        groups.setdefault(min(int(p * bins), bins - 1), []).append((p, happened))
    return [{"lo": i / bins, "hi": (i + 1) / bins, "n": len(g), "mean_p": mean(p for p, _ in g),
             "observed": mean(1.0 if h else 0.0 for _, h in g)} for i, g in sorted(groups.items())]
