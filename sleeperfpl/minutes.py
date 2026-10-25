"""Expected minutes: will he start, will he appear, and for how long.

A player's regular weeks this season (one fixture for every club) become
states: started, came off the bench, or no minutes. A logistic regression on
recent states gives P(start); his own bench record gives P(sub | no start); and
per-position minute tables turn both into a spread of likely minutes for each
fixture. Weeks FPL flagged him out of are left out (labels handle those).
"""

import json
import math
from dataclasses import asdict, dataclass

from .sleeper_stats import irregular_week

FEATURES = ["ewma3", "prev_st", "prev_sub", "last3", "prev_m90", "zero_streak", "GK", "DEF", "FWD", "ewma10"]
POSITIONS = ("GK", "DEF", "MID", "FWD")
MIN_HISTORY = 3        # fewer weeks than this: the short-history rule
SUB_WINDOW = 10        # recent non-starts behind P(sub | no start)
SUB_PSEUDO = 5
START_WINDOW = 10      # recent starts behind a player's own minute shares
START_PSEUDO = 10
SHORT_PSEUDO = 3


@dataclass
class Week:
    week: int
    minutes: float
    started: bool


@dataclass
class MinutesModel:
    version: str
    weights: list          # intercept first, then one per FEATURES entry (standardised)
    mu: list
    sd: list
    q_prior: dict          # P(sub | no start) by position
    start_buckets: dict    # position -> [[minutes, prob]] for 90+, 75-89, 60-74, under 60
    sub_buckets: list      # [[minutes, prob]], sub appearances
    short_prior: dict      # P(start) by position for players with little history
    trained_on: list
    n_rows: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    def p_start(self, x):
        t = self.weights[0] + sum(w * (v - m) / s for w, v, m, s in zip(self.weights[1:], x, self.mu, self.sd))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, t))))


@dataclass
class PlayerMinutes:
    p_start: float
    p_app: float
    start_buckets: list
    sub_buckets: list
    short: bool


@dataclass
class FixtureMinutes:
    scenarios: list        # [(minutes, prob)], summing to 1
    p1: float
    p60: float
    e_min: float


def season_rows(conn, season, max_week=None):
    out = {}
    for week, pid, raw in conn.execute("SELECT week, player_id, stats_json FROM sleeper_stats WHERE season = ?",
                                       (season,)):
        if max_week is None or week <= max_week:
            out.setdefault(week, {})[pid] = json.loads(raw)
    return out


def regular_weeks(rows_by_week):
    """Weeks where every club played once. A week without club rows can't be checked and counts."""
    return {w for w, rows in rows_by_week.items()
            if not any(pid.startswith("TEAM_") for pid in rows) or not irregular_week(rows)}


def history(rows_by_week, pid, before, regular, excluded=frozenset()):
    """His regular weeks before `before`, oldest first, from his first row of the season on.
    No row after that counts as no minutes."""
    seen = [w for w, rows in rows_by_week.items() if w < before and pid in rows]
    if not seen:
        return []
    out = []
    for w in range(min(seen), before):
        if w not in regular or w in excluded:
            continue
        s = rows_by_week.get(w, {}).get(pid) or {}
        out.append(Week(w, float(s.get("min") or 0), (s.get("gs") or 0) >= 1))
    return out


def ewma(vals, half_life):
    """Weighted mean, newest last in `vals`, weight halving every `half_life` steps back."""
    lam = 0.5 ** (1.0 / half_life)
    num = den = 0.0
    w = 1.0
    for v in reversed(vals):
        num += w * v
        den += w
        w *= lam
    return num / den if den else 0.0


def features(hist, pos):
    st = [1.0 if h.started else 0.0 for h in hist]
    prev = hist[-1]
    streak = 0
    for h in reversed(hist):
        if h.minutes > 0:
            break
        streak += 1
    last3 = st[-3:]
    return [ewma(st, 3), st[-1], 1.0 if (not prev.started and prev.minutes > 0) else 0.0,
            sum(last3) / len(last3), prev.minutes / 90.0, min(streak, 5) / 5.0,
            float(pos == "GK"), float(pos == "DEF"), float(pos == "FWD"), ewma(st, 10)]


def _prior(table, pos):
    return table[pos] if pos in table else sum(table.values()) / len(table)


def p_sub_given_not_start(hist, pos, model):
    ns = [h for h in hist if not h.started][-SUB_WINDOW:]
    return (sum(1 for h in ns if h.minutes > 0) + SUB_PSEUDO * _prior(model.q_prior, pos)) / (len(ns) + SUB_PSEUDO)


def personal_start_buckets(hist, pos, model):
    """The position's minute shares for a start, moved toward his own last 10 starts."""
    base = model.start_buckets.get(pos) or model.start_buckets["MID"]
    starts = [h.minutes for h in hist if h.started][-START_WINDOW:]
    n = len(starts)
    p90 = (sum(1 for m in starts if m >= 90) + START_PSEUDO * base[0][1]) / (n + START_PSEUDO)
    plo = (sum(1 for m in starts if m < 60) + START_PSEUDO * base[3][1]) / (n + START_PSEUDO)
    mid = max(0.0, 1 - p90 - plo)
    split = base[1][1] + base[2][1]
    r = base[1][1] / split if split > 0 else 0.5
    return [[base[0][0], p90], [base[1][0], mid * r], [base[2][0], mid * (1 - r)], [base[3][0], plo]]


def player_minutes(hist, pos, model, last_season_share):
    if len(hist) >= MIN_HISTORY:
        p_start, short = model.p_start(features(hist, pos)), False
    else:
        p0 = last_season_share if last_season_share is not None else _prior(model.short_prior, pos)
        p_start = (sum(1 for h in hist if h.started) + SHORT_PSEUDO * p0) / (len(hist) + SHORT_PSEUDO)
        short = True
    q = p_sub_given_not_start(hist, pos, model)
    return PlayerMinutes(p_start, p_start + (1 - p_start) * q, personal_start_buckets(hist, pos, model),
                         model.sub_buckets, short)


def fixture_minutes(p_start, p_app, start_buckets, sub_buckets):
    p_start = min(1.0, max(0.0, p_start))
    p_app = min(1.0, max(p_start, p_app))
    scen = [(m, p_start * q) for m, q in start_buckets] + [(m, (p_app - p_start) * q) for m, q in sub_buckets]
    scen.append((0, 1 - p_app))
    return FixtureMinutes(scen, p_app, sum(p for m, p in scen if m >= 60), sum(m * p for m, p in scen))


def last_season_start_share(rows_by_week, pid):
    regular = regular_weeks(rows_by_week)
    rows = [rows_by_week[w][pid] for w in regular if pid in rows_by_week[w]]
    if not rows:
        return None
    return sum(1 for s in rows if (s.get("gs") or 0) >= 1) / len(rows)
