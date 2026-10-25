"""Official Fantasy Premier League fetcher: bootstrap, fixtures, player history."""

import json
import sys

from .db import latest, meta_get, meta_set, now_iso
from .gameweek import season_of
from .http import FetchError, get_json

FPL = "https://fantasy.premierleague.com/api"
HISTORY_META = "history_last_gw"


def _float(value):
    """FPL sends expected points as strings ("4.5")."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def fetch_bootstrap(conn, get=get_json):
    """Snapshot elements (players), teams and events (game weeks)."""
    data = get(f"{FPL}/bootstrap-static/")
    ts = now_iso()
    with conn:
        conn.executemany(
            "INSERT INTO fpl_elements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(ts, e["id"], e.get("first_name"), e.get("second_name"), e.get("web_name"),
              e.get("team"), e.get("element_type"), e.get("status"),
              e.get("chance_of_playing_next_round"), e.get("minutes") or 0, _float(e.get("ep_next")))
             for e in data.get("elements", [])],
        )
        conn.executemany(
            "INSERT INTO fpl_teams VALUES (?, ?, ?, ?)",
            [(ts, t["id"], t.get("short_name"), t.get("name")) for t in data.get("teams", [])],
        )
        conn.executemany(
            "INSERT INTO fpl_events VALUES (?, ?, ?, ?)",
            [(ts, ev["id"], ev.get("deadline_time"), int(bool(ev.get("finished"))))
             for ev in data.get("events", [])],
        )
    return len(data.get("elements", []))


def fetch_fixtures(conn, get=get_json):
    fixtures = get(f"{FPL}/fixtures/") or []
    ts = now_iso()
    with conn:
        conn.executemany(
            "INSERT INTO fpl_fixtures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(ts, f["id"], f.get("event"), f.get("team_h"), f.get("team_a"),
              f.get("team_h_difficulty"), f.get("team_a_difficulty"), f.get("kickoff_time"),
              int(bool(f.get("finished"))), f.get("team_h_score"), f.get("team_a_score"))
             for f in fixtures],
        )
    return len(fixtures)


def _last_finished_gw(conn):
    finished = [r["event_id"] for r in latest(conn, "fpl_events") if r["finished"]]
    return max(finished) if finished else None


def fetch_history(conn, *, force=False, interval=0.5, get=get_json):
    """Download match history for every player with minutes this season.

    Runs only when forced or a game week has finished since the last full
    run. Returns the number of players fetched (0 when skipped).
    """
    last_finished = _last_finished_gw(conn)
    done = meta_get(conn, HISTORY_META)
    if not force:
        if last_finished is None or (done is not None and last_finished <= int(done)):
            return 0

    season = season_of([dict(r) for r in latest(conn, "fpl_events")]) or "unknown"
    ids = [r["fpl_id"] for r in latest(conn, "fpl_elements") if (r["total_minutes"] or 0) > 0]
    fetched, failed = 0, 0
    for fpl_id in ids:
        try:
            data = get(f"{FPL}/element-summary/{fpl_id}/", min_interval=interval)
        except FetchError as e:
            sys.stderr.write(f"warning: history for FPL player {fpl_id} failed: {e}\n")
            failed += 1
            continue
        ts = now_iso()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO fpl_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(season, fpl_id, h["fixture"], h.get("round"), h.get("kickoff_time"),
                  h.get("minutes") or 0, json.dumps(h), ts)
                 for h in data.get("history", [])],
            )
        fetched += 1

    if failed == 0 and last_finished is not None:
        meta_set(conn, HISTORY_META, str(last_finished))
        meta_set(conn, "history_season", season)
    return fetched
