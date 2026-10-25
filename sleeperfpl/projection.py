"""v1 projection: recent average scaled by fixture difficulty.

This is the one swappable piece. A prediction model can replace `project`
(or run beside it) without touching fetchers, lineup or report code.

`project_minutes` (v1m) is v1 with the "played or ruled out" gate replaced by
a spread of likely minutes: points per 90 scale with minutes, and clean sheets
count only in scenarios of 60+ minutes.
"""

import sys
from dataclasses import dataclass

from .projection_v2 import G60_CODES, KNOWN_CODES, stat_code


@dataclass
class Projection:
    avg: float | None
    n: int
    proj: float


def project(history, difficulties, ruled_out, multipliers, window=5):
    """`history`: league points for matches with minutes > 0, most recent first.
    `difficulties`: one entry per fixture this game week (empty = blank, two = double)."""
    recent = list(history[:window])
    avg = sum(recent) / len(recent) if recent else None
    if ruled_out or avg is None or not difficulties:
        return Projection(avg, len(recent), 0.0)
    return Projection(avg, len(recent), sum(avg * multipliers[d] for d in difficulties))


_warned = set()


def minutes_rates(weeks, scoring):
    """(league points per 90 outside clean sheets, clean-sheet points per 60+ minute week) over `weeks`."""
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
    lin90 = lin / minutes * 90 if minutes > 0 else 0.0
    cs60 = sum(g60_weeks) / len(g60_weeks) if g60_weeks else 0.0
    return lin90, cs60


def project_minutes(lin90, cs60, fixtures, multipliers):
    """v1m: one FixtureMinutes and one difficulty multiplier per fixture this game week."""
    total = 0.0
    for fm, mult in zip(fixtures, multipliers):
        total += mult * sum(p * (lin90 * m / 90 + (cs60 if m >= 60 else 0.0)) for m, p in fm.scenarios if m > 0)
    return total
