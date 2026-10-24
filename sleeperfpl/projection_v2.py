"""Projection v2: re-price defensive and attacking points with market odds.

Each week's league points split into three buckets by Sleeper stat code:
  defensive (clean sheet, goals against): replaced by the market's expectation
  attacking (goals, assists, shots on target, ...): scaled by team expected goals
  other (tackles, cards, saves, ...): recent average, unchanged
"""

import math
import re
from dataclasses import dataclass

DEF_CODES = {"cs", "ga"}
ATT_CODES = {"g", "at", "sot", "kp", "acnc", "cos", "pkd"}
SLEEPER_PREFIX = {"GK": "pos_gk_", "DEF": "pos_d_", "MID": "pos_m_", "FWD": "pos_f_"}
_KEY = re.compile(r"pos_(?:gk|d|m|f)_(\w+)")


@dataclass
class V2Inputs:
    att_avg: float
    other_avg: float
    p60: float          # share of recent appearances reaching 60 minutes (clean sheets need 60)
    cs_points: float
    ga_points: float    # points per goal conceded (negative)
    n: int


def split_points(stats, scoring):
    """(defensive, attacking, other) league points for one week's stats."""
    buckets = [0.0, 0.0, 0.0]
    for key, points in scoring.items():
        value = stats.get(key)
        if not value:
            continue
        m = _KEY.fullmatch(key)
        code = m.group(1) if m else key
        idx = 0 if code in DEF_CODES else 1 if code in ATT_CODES else 2
        buckets[idx] += float(value) * float(points)
    return tuple(buckets)


def v2_inputs(weeks, scoring, prefix, window):
    """Inputs from a player's played weeks (most recent first); None if none."""
    recent = weeks[:window]
    if not recent:
        return None
    parts = [split_points(w, scoring) for w in recent]
    n = len(recent)
    return V2Inputs(att_avg=sum(p[1] for p in parts) / n, other_avg=sum(p[2] for p in parts) / n,
                    p60=sum(1 for w in recent if (w.get("min") or 0) >= 60) / n,
                    cs_points=scoring.get(prefix + "cs", 0), ga_points=scoring.get(prefix + "ga", 0), n=n)


def project_v2(inp, own_lam, opp_lam, team_baseline):
    """Projected league points for one fixture."""
    defensive = inp.cs_points * math.exp(-opp_lam) * inp.p60 + inp.ga_points * opp_lam
    attacking = inp.att_avg * (own_lam / team_baseline)
    return defensive + attacking + inp.other_avg
