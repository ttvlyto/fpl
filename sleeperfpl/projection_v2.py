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
G60_CODES = {"cs"}     # clean sheets need 60 minutes; every other code is earned per minute played
# Every stat code in the league's scoring as of 2026-10-24; anything else gets a warning.
KNOWN_CODES = {"acnc", "aer", "at", "bs", "clr", "cos", "cs", "cs90", "dis", "g", "ga", "hcs", "int", "kp", "min",
               "og", "pkd", "pkm", "pks", "rc", "sm", "sot", "sv", "tkw", "yc", "yc2"}
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


@dataclass
class V2mInputs:
    att90: float
    other90: float
    cs_points: float
    ga_points: float


def stat_code(key):
    m = _KEY.fullmatch(key)
    return m.group(1) if m else key


def split_points(stats, scoring):
    """(defensive, attacking, other) league points for one week's stats."""
    buckets = [0.0, 0.0, 0.0]
    for key, points in scoring.items():
        value = stats.get(key)
        if not value:
            continue
        code = stat_code(key)
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
    """Projected league points for one fixture, at least 0 (as projection.project)."""
    defensive = inp.cs_points * math.exp(-opp_lam) * inp.p60 + inp.ga_points * opp_lam
    attacking = inp.att_avg * (own_lam / team_baseline)
    return max(0.0, defensive + attacking + inp.other_avg)


def v2m_inputs(weeks, scoring, prefix, window, prior=(0.0, 0.0), k=0.0):
    """Per-90 attacking and other points over a player's recent played weeks; None if none.
    Each rate is pulled toward `prior` (att90, other90) with `k` pseudo-90s."""
    recent = weeks[:window]
    minutes = sum(float(w.get("min") or 0) for w in recent)
    if not recent or minutes <= 0:
        return None
    parts = [split_points(w, scoring) for w in recent]
    n90 = minutes / 90 + k
    return V2mInputs(att90=(sum(p[1] for p in parts) + prior[0] * k) / n90,
                     other90=(sum(p[2] for p in parts) + prior[1] * k) / n90,
                     cs_points=scoring.get(prefix + "cs", 0), ga_points=scoring.get(prefix + "ga", 0))


def project_v2m(inp, fm, own_lam, opp_lam, team_baseline):
    """v2 for one fixture with minutes: clean sheets need 60+, everything else scales with minutes. At least 0."""
    share = fm.e_min / 90
    defensive = inp.cs_points * math.exp(-opp_lam) * fm.p60 + inp.ga_points * opp_lam * share
    return max(0.0, defensive + inp.att90 * share * (own_lam / team_baseline) + inp.other90 * share)
