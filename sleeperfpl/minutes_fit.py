"""Fit the start model: logistic regression by Newton's method (IRLS), plain Python.

Training rows come from stored Sleeper weeks: for each regular week, every
player with at least 3 earlier weeks this season who played in one of his last
5. The same rows give the minute tables and bench rates the model carries.
"""

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from .db import latest, now_iso
from .metrics import brier, mean, position_group
from .minutes import (MIN_HISTORY, POSITIONS, MinutesModel, features, history, regular_weeks, season_rows)

DEFAULT_PATH = Path(__file__).with_name("minutes_coef.json")
MIN_ROWS = 50
RECENT = 5
START_EDGES = [(90, 999, 90), (75, 90, 82), (60, 75, 67), (0, 60, 45)]          # (lo, hi, default minutes)
SUB_EDGES = [(0, 10, 5), (10, 20, 15), (20, 30, 25), (30, 60, 40), (60, 999, 65)]
_PREFIX = {"gk": "GK", "d": "DEF", "m": "MID", "f": "FWD"}


@dataclass
class TrainRow:
    season: str
    week: int
    pid: str
    pos: str
    x: list
    started: bool
    appeared: bool
    minutes: float
    hist: list


def solve(a, b):
    """Solve a·x = b by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            raise ValueError("singular matrix")
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def _standardise(x):
    d = len(x[0])
    mu = [sum(r[j] for r in x) / len(x) for j in range(d)]
    sd = [max(1e-9, math.sqrt(sum((r[j] - mu[j]) ** 2 for r in x) / len(x))) for j in range(d)]
    return [[1.0] + [(r[j] - mu[j]) / sd[j] for j in range(d)] for r in x], mu, sd


def _sigmoid(t):
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, t))))


def fit_irls(x, y, l2=1e-3, iters=25, tol=1e-8, w0=None):
    """Ridge logistic regression on standardised features: (weights, mu, sd), intercept first."""
    z, mu, sd = _standardise(x)
    k, n = len(z[0]), len(z)
    w = list(w0) if w0 and len(w0) == k else [0.0] * k
    for _ in range(iters):
        h = [[0.0] * k for _ in range(k)]
        g = [0.0] * k
        for zi, yi in zip(z, y):
            p = _sigmoid(sum(a * b for a, b in zip(w, zi)))
            s = max(p * (1 - p), 1e-9)
            r = (1.0 if yi else 0.0) - p
            for a in range(k):
                g[a] += r * zi[a]
                sa = s * zi[a]
                row = h[a]
                for b in range(a, k):
                    row[b] += sa * zi[b]
        for a in range(k):
            for b in range(a):
                h[a][b] = h[b][a]
            if a:
                h[a][a] += l2 * n
                g[a] -= l2 * n * w[a]
        step = solve(h, g)
        w = [wi + si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < tol:
            break
    return w, mu, sd


def fit_gd(x, y, l2=1e-3, lr=0.5, iters=3000):
    """Same objective by gradient descent: a slow check on fit_irls, used only in tests."""
    z, _, _ = _standardise(x)
    k, n = len(z[0]), len(z)
    w = [0.0] * k
    for _ in range(iters):
        g = [0.0] * k
        for zi, yi in zip(z, y):
            r = (1.0 if yi else 0.0) - _sigmoid(sum(a * b for a, b in zip(w, zi)))
            for a in range(k):
                g[a] += r * zi[a]
        w = [wi + lr * (gi - (l2 * n * wi if a else 0.0)) / n for a, (wi, gi) in enumerate(zip(w, g))]
    return w


def _earlier(season, week, before):
    return before is None or season < before[0] or (season == before[0] and week < before[1])


def _rows(conn, pos_of, seasons, before):
    full, short = [], []
    for season in seasons:
        rows = season_rows(conn, season)
        regular = regular_weeks(rows)
        seen = set()
        for g in sorted(rows):
            ids = {pid for pid in rows[g] if not pid.startswith("TEAM_")}
            if g in regular and _earlier(season, g, before):
                for pid in sorted(seen | ids):
                    pos = pos_of.get(pid)
                    if not pos:
                        continue
                    hist = history(rows, pid, g, regular)
                    s = rows[g].get(pid) or {}
                    started, minutes = (s.get("gs") or 0) >= 1, float(s.get("min") or 0)
                    if len(hist) < MIN_HISTORY:
                        short.append(TrainRow(season, g, pid, pos, [], started, minutes > 0, minutes, hist))
                    elif any(h.minutes > 0 for h in hist[-RECENT:]):
                        full.append(TrainRow(season, g, pid, pos, features(hist, pos), started, minutes > 0,
                                             minutes, hist))
            seen |= ids
    return full, short


def training_rows(conn, pos_of, seasons, before=None):
    return _rows(conn, pos_of, seasons, before)[0]


def short_rows(conn, pos_of, seasons, before=None):
    return _rows(conn, pos_of, seasons, before)[1]


def _by_pos(rows, value, fallback):
    """{position: mean of value(row)} with the all-position mean where a position has no rows."""
    vals = [value(r) for r in rows]
    overall = mean(v for v in vals if v is not None)
    out = {}
    for pos in POSITIONS:
        own = mean(v for r, v in zip(rows, vals) if r.pos == pos and v is not None)
        out[pos] = own if own is not None else (overall if overall is not None else fallback)
    return out


def _buckets(minutes, edges):
    total = len(minutes)
    out = []
    for lo, hi, default in edges:
        inside = [m for m in minutes if lo <= m < hi]
        out.append([mean(inside) if inside else float(default), len(inside) / total if total else 0.0])
    if not total:
        out[0][1] = 1.0
    return out


def measure_priors(rows, short):
    starts = [r for r in rows if r.started]
    everyone = [r.minutes for r in starts]
    start_buckets = {}
    for pos in POSITIONS:
        own = [r.minutes for r in starts if r.pos == pos]
        start_buckets[pos] = _buckets(own if own else everyone, START_EDGES)
    subs = [r.minutes for r in rows if r.appeared and not r.started]
    return {"q_prior": _by_pos([r for r in rows if not r.started], lambda r: 1.0 if r.appeared else 0.0, 0.3),
            "start_buckets": start_buckets,
            "sub_buckets": _buckets(subs, SUB_EDGES),
            "short_prior": _by_pos(short, lambda r: 1.0 if r.started else 0.0, 0.3)}


def all_rows(conn, pos_of, seasons, before=None):
    """(training rows, short-history rows), built once so a walk-forward can filter them by week."""
    return _rows(conn, pos_of, seasons, before)


def earlier(row, before):
    return _earlier(row.season, row.week, before)


def fit_model(conn, pos_of, seasons, before=None, w0=None):
    return fit_rows(*_rows(conn, pos_of, seasons, before), w0=w0)


def fit_rows(rows, short, w0=None):
    if len(rows) < MIN_ROWS:
        return None
    w, mu, sd = fit_irls([r.x for r in rows], [r.started for r in rows], w0=w0)
    return MinutesModel(version=now_iso(), weights=w, mu=mu, sd=sd, **measure_priors(rows, short),
                        trained_on=sorted({r.season for r in rows}), n_rows=len(rows))


def holdout_brier(model, rows):
    return mean(brier(model.p_start(r.x), r.started) for r in rows)


def pos_of_players(conn, rows_by_season):
    """Position group per Sleeper id: from the player map, else from his stat keys (pos_m_...)."""
    out = {}
    for rows in rows_by_season.values():
        for week_rows in rows.values():
            for pid, s in week_rows.items():
                if pid in out or pid.startswith("TEAM_"):
                    continue
                key = next((k for k in s if k.startswith("pos_")), None)
                if key:
                    out[pid] = _PREFIX.get(key.split("_")[1])
    for p in latest(conn, "sleeper_players"):
        pos = position_group(json.loads(p["positions_json"] or "[]"))
        if pos:
            out[p["player_id"]] = pos
    return {k: v for k, v in out.items() if v}


def save_model(conn, model, last_key, holdout):
    with conn:
        conn.execute("INSERT OR REPLACE INTO minutes_model VALUES (?, ?, ?, ?, ?, ?)",
                     (model.version, now_iso(), model.n_rows, last_key, json.dumps(model.to_dict()), holdout))


def last_fit_key(conn):
    row = conn.execute("SELECT trained_on FROM minutes_model ORDER BY fitted_at DESC, version DESC LIMIT 1").fetchone()
    return row[0] if row else None


def load_model(conn):
    """Newest fitted model, else the default shipped beside this module, else None."""
    row = conn.execute("SELECT model_json FROM minutes_model ORDER BY fitted_at DESC, version DESC LIMIT 1").fetchone()
    if row:
        try:
            return MinutesModel.from_dict(json.loads(row[0]))
        except (ValueError, TypeError):
            sys.stderr.write("warning: stored minutes model unreadable; using the shipped default\n")
    return load_default()


def load_default():
    """The fit shipped beside this module (last season only), or None."""
    try:
        return MinutesModel.from_dict(json.loads(DEFAULT_PATH.read_text()))
    except (OSError, ValueError, TypeError):
        return None
