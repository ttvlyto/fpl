"""Turn match-market quotes into each team's expected goals.

Model: home and away goals are independent Poisson(λh), Poisson(λa). We grid
search (λh, λa) to best match every usable quote's mid price, then read win,
draw and clean-sheet probabilities off the fitted model.
"""

import math
from dataclasses import dataclass, field
from datetime import timedelta

from .gameweek import parse_time
from .markets import find_fixture, mid, team_code, usable

MAX_GOALS = 10
GRID = [round(0.05 * i, 2) for i in range(1, 81)]   # 0.05 .. 4.00
LINES = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]


@dataclass
class FixtureOdds:
    home: str
    away: str
    lam_home: float
    lam_away: float
    p_home: float
    p_draw: float
    p_away: float
    cs_home: float
    cs_away: float
    sources: list = field(default_factory=list)
    fetched_at: str = ""
    n_quotes: int = 0


def _pmf(lam):
    return [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(MAX_GOALS + 1)]


_PMF = {lam: _pmf(lam) for lam in GRID}


def _over(pmf, line):
    return 1.0 - sum(p for k, p in enumerate(pmf) if k < line)


def poisson_probs(lam_home, lam_away):
    """Model probabilities for every market type we price."""
    ph, pa = _PMF.get(lam_home) or _pmf(lam_home), _PMF.get(lam_away) or _pmf(lam_away)
    home = draw = 0.0
    total = [0.0] * (2 * MAX_GOALS + 1)
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            p = x * y
            total[i + j] += p
            if i > j:
                home += p
            elif i == j:
                draw += p
    mass = sum(total)
    return {
        "home": home / mass, "draw": draw / mass, "away": (mass - home - draw) / mass,
        "over": {line: _over(total, line) / mass for line in LINES},
        "home_over": {line: _over(ph, line) for line in LINES},
        "away_over": {line: _over(pa, line) for line in LINES},
        "btts": (1 - ph[0]) * (1 - pa[0]),
    }


_GRID_PROBS = []


def _grid_probs():
    """Model probabilities for every grid point, computed once per process."""
    if not _GRID_PROBS:
        _GRID_PROBS.extend((lh, la, poisson_probs(lh, la)) for lh in GRID for la in GRID)
    return _GRID_PROBS


def _targets(quotes, home, away):
    """(kind, line, market probability) per quote; 1x2 mids normalised per source."""
    out = []
    by_source = {}
    for q in quotes:
        if q["market"] == "1x2":
            by_source.setdefault(q["source"], []).append(q)
    for src_quotes in by_source.values():
        total = sum(mid(q) for q in src_quotes)
        scale = 1 / total if len(src_quotes) == 3 and total > 0 else 1.0
        for q in src_quotes:
            kind = "home" if q["side"] == home else "away" if q["side"] == away else "draw"
            out.append((kind, None, mid(q) * scale))
    for q in quotes:
        m, line = q["market"], q.get("line")
        if m == "total" and line in LINES:
            out.append(("over", line, mid(q)))
        elif m == "team_total" and line in LINES:
            out.append(("home_over" if q["side"] == home else "away_over", line, mid(q)))
        elif m == "btts":
            out.append(("btts", None, mid(q)))
    return out


def fit_fixture(quotes, home, away):
    """Fit (λh, λa) to already-usable quotes for one fixture; None without quotes."""
    targets = _targets(quotes, home, away)
    if not targets:
        return None
    best = None
    for lh, la, pr in _grid_probs():
        err = sum((((pr[k][line] if line is not None else pr[k]) - p) ** 2) for k, line, p in targets)
        if best is None or err < best[0]:
            best = (err, lh, la, pr)
    _, lh, la, pr = best
    return FixtureOdds(home, away, lh, la, pr["home"], pr["draw"], pr["away"],
                       cs_home=math.exp(-la), cs_away=math.exp(-lh),
                       sources=sorted({q["source"] for q in quotes}),
                       fetched_at=max(q["fetched_at"] for q in quotes), n_quotes=len(quotes))


def fixture_odds(quotes, fixtures, teams, cfg, now, overrides):
    """{fixture_id: FixtureOdds} from fresh, usable quotes; plus unmatched descriptions."""
    codes = set(teams.values())
    cutoff = now - timedelta(hours=cfg.market_stale_hours)
    grouped, unmatched = {}, set()
    for q in quotes:
        fetched = parse_time(q.get("fetched_at"))
        if not fetched or fetched < cutoff or not usable(q, cfg):
            continue
        home = team_code(q["source"], q["home"], codes, overrides)
        away = team_code(q["source"], q["away"], codes, overrides)
        fixture = find_fixture(home, away, q.get("kickoff"), fixtures, teams) if home and away else None
        if fixture is None:
            unmatched.add(f"{q['source']}: {q['home']} vs {q['away']} ({(q.get('kickoff') or '')[:10]})")
            continue
        side = q["side"]
        if q["market"] in ("1x2", "team_total") and side not in ("draw",):
            side = team_code(q["source"], side, codes, overrides) or side
        grouped.setdefault(fixture["fixture_id"], (home, away, []))[2].append({**q, "side": side})
    odds = {}
    for fid, (home, away, qs) in grouped.items():
        fit = fit_fixture(qs, home, away)
        if fit:
            odds[fid] = fit
    return odds, sorted(unmatched)
