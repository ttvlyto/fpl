"""Scoring functions shared by the report card and the backtest.

Player projections: error overall and by outcome group, and how well the
projection ordered players within each position (Spearman). Match odds: log
loss, ranked probability score and Brier.
"""

import math
from dataclasses import dataclass

CATEGORIES = ("dnp", "low", "mid", "haul")
_GROUPS = {"GK": "GK", "D": "DEF", "DEF": "DEF", "M": "MID", "MID": "MID", "F": "FWD", "FWD": "FWD"}
_OUTCOMES = ("home", "draw", "away")
_EPS = 1e-6


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
