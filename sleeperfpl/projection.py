"""v1 projection: recent average scaled by fixture difficulty.

This is the one swappable piece. A prediction model can replace `project`
(or run beside it) without touching fetchers, lineup or report code.

`project_minutes` (v1m) is v1 with the "played or ruled out" gate replaced by
a spread of likely minutes: points per 90 scale with minutes, and clean sheets
count only in scenarios of 60+ minutes.
"""

import sys
from dataclasses import dataclass

from .projection_v2 import G60_CODES, KNOWN_CODES, split_points, stat_code


@dataclass
class Projection:
    avg: float | None
    n: int
    proj: float


def project(history, difficulties, ruled_out, multipliers, window=5):
    """`history`: league points for matches with minutes > 0, most recent first.
    `difficulties`: one entry per fixture this game week (empty = blank, two = double).
    Each fixture projects at least 0: a negative recent average is mostly small-sample noise
    (the 2025 and 2026 backtests miss less with the floor, player order unchanged)."""
    recent = list(history[:window])
    avg = sum(recent) / len(recent) if recent else None
    if ruled_out or avg is None or not difficulties:
        return Projection(avg, len(recent), 0.0)
    return Projection(avg, len(recent), sum(max(0.0, avg * multipliers[d]) for d in difficulties))


_warned = set()


def minutes_rates(weeks, scoring, prior=0.0, k=0.0):
    """(league points per 90 outside clean sheets, clean-sheet points per 60+ minute week) over `weeks`.
    The per-90 rate is pulled toward `prior` with `k` pseudo-90s, so a few minutes can't give an absurd rate."""
    lin = minutes = 0.0
    g60_weeks = []
    for w in weeks:
        week_g60 = 0.0
        for key, points in scoring.items():
            value = w.get(key)
            if not value:
                continue
            code = stat_code(key)
            if code not in KNOWN_CODES and code not in _warned:
                _warned.add(code)
                sys.stderr.write(f"warning: unknown scoring code {code!r}; treated as earned per minute\n")
            pts = float(value) * float(points)
            if code in G60_CODES:
                week_g60 += pts
            else:
                lin += pts
        minutes += float(w.get("min") or 0)
        if (w.get("min") or 0) >= 60:
            g60_weeks.append(week_g60)
    lin90 = (lin + prior * k) / (minutes / 90 + k) if minutes > 0 or k > 0 else 0.0
    cs60 = sum(g60_weeks) / len(g60_weeks) if g60_weeks else 0.0
    return lin90, cs60


@dataclass
class Prior:
    lin90: float     # per 90 outside clean sheets, as minutes_rates
    att90: float     # per 90 attacking points, as v2m_inputs
    other90: float   # per 90 other points, as v2m_inputs


class PositionRates:
    """Points per 90 pooled over every played player-week of each position: what small samples shrink toward.
    Sleeper prefixes each player's stats with his own position (pos_d_min ...), which says whose rate it is."""

    def __init__(self, scoring):
        self.scoring = scoring
        self.sums = {}   # prefix -> [lin, att, other, minutes]

    def add(self, rows):
        """One week's {player_id: stats}."""
        for pid, stats in rows.items():
            minutes = float(stats.get("min") or 0)
            if pid.startswith("TEAM_") or minutes <= 0:
                continue
            prefix = next((key[:-3] for key in stats if key.startswith("pos_") and key.endswith("_min")), None)
            if prefix is None:
                continue
            _, att, other = split_points(stats, self.scoring)
            g60 = sum(float(stats.get(key) or 0) * float(points) for key, points in self.scoring.items()
                      if stat_code(key) in G60_CODES)
            total = sum(float(stats.get(key) or 0) * float(points) for key, points in self.scoring.items())
            acc = self.sums.setdefault(prefix, [0.0, 0.0, 0.0, 0.0])
            acc[0] += total - g60
            acc[1] += att
            acc[2] += other
            acc[3] += minutes

    def prior(self, prefix):
        acc = self.sums.get(prefix)
        if not acc or acc[3] <= 0:
            return None
        per90 = 90 / acc[3]
        return Prior(acc[0] * per90, acc[1] * per90, acc[2] * per90)


def project_minutes(lin90, cs60, fixtures, multipliers):
    """v1m: one FixtureMinutes and one difficulty multiplier per fixture this game week."""
    total = 0.0
    for fm, mult in zip(fixtures, multipliers):
        total += max(0.0, mult * sum(p * (lin90 * m / 90 + (cs60 if m >= 60 else 0.0))
                                     for m, p in fm.scenarios if m > 0))
    return total
