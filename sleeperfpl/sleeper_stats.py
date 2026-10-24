"""Sleeper's own per-week player stats (undocumented endpoint).

Stat keys are the same as the league's scoring_settings keys (e.g. "pos_d_g"),
so points are exact: sum(stat * points) over the league's scoring rules.
Sleeper's week number is treated as the FPL game week.
"""

import json
import sys

from .db import meta_get, meta_set, now_iso
from .http import FetchError, get_json

API = "https://api.sleeper.app/v1"
META = "sleeper_stats_week"


def sleeper_points(stats, scoring_settings):
    return sum(float(stats.get(k, 0) or 0) * float(v) for k, v in scoring_settings.items())


def _stored_weeks(conn, season):
    return {r[0] for r in conn.execute("SELECT DISTINCT week FROM sleeper_stats WHERE season = ?", (season,))}


def fetch_sleeper_stats(conn, sport, season, last_week, *, force=False, get=get_json):
    """Fetch finished weeks not stored yet, plus the latest stored week again
    (Sleeper corrects stats after matches). Returns how many weeks were fetched."""
    if not last_week:
        return 0
    stored = _stored_weeks(conn, season)
    if force:
        weeks = set(range(1, last_week + 1))
    else:
        weeks = {w for w in range(1, last_week + 1) if w not in stored}
        latest = max((w for w in stored if w <= last_week), default=None)
        if latest:
            weeks.add(latest)
    fetched = 0
    for week in sorted(weeks):
        try:
            data = get(f"{API}/stats/{sport}/regular/{season}/{week}") or {}
        except FetchError as e:
            sys.stderr.write(f"warning: Sleeper stats for week {week} failed: {e}\n")
            continue
        ts = now_iso()
        with conn:
            conn.execute("DELETE FROM sleeper_stats WHERE season = ? AND week = ?", (season, week))
            conn.executemany("INSERT INTO sleeper_stats VALUES (?, ?, ?, ?, ?)",
                             [(season, week, str(pid), json.dumps(stats), ts)
                              for pid, stats in data.items() if isinstance(stats, dict)])
        fetched += 1
    stored = _stored_weeks(conn, season)
    if stored:
        meta_set(conn, META, str(max(stored)))
    return fetched


def has_stats(conn, season):
    return conn.execute("SELECT 1 FROM sleeper_stats WHERE season = ? LIMIT 1", (season,)).fetchone() is not None


def sleeper_history(conn, season, player_id, scoring_settings):
    """League points per played week (minutes > 0), most recent first."""
    rows = conn.execute("SELECT stats_json FROM sleeper_stats WHERE season = ? AND player_id = ? "
                        "ORDER BY week DESC", (season, str(player_id))).fetchall()
    out = []
    for (raw,) in rows:
        stats = json.loads(raw)
        if (stats.get("min") or 0) > 0:
            out.append(sleeper_points(stats, scoring_settings))
    return out


def last_stats_week(conn):
    value = meta_get(conn, META)
    return int(value) if value else None
