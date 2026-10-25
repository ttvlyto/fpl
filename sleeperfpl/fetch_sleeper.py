"""Sleeper fetcher: league, users, players, rosters and trending snapshots."""

import json
import sys
from pathlib import Path

from .db import now_iso
from .http import FetchError, get_json

# sleeper.py lives next to this package; reuse its name/position helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sleeper import player_name, positions  # noqa: E402

API = "https://api.sleeper.app/v1"


def fetch_league(conn, league_id, get=get_json):
    """Store the league and its users; return the sport slug."""
    league = get(f"{API}/league/{league_id}")
    if not league or not league.get("sport"):
        raise FetchError(f"League {league_id} not found or has no sport")
    users = get(f"{API}/league/{league_id}/users") or []
    ts = now_iso()
    with conn:
        conn.execute("INSERT INTO sleeper_league VALUES (?, ?, ?)", (ts, league_id, json.dumps(league)))
        conn.executemany(
            "INSERT INTO sleeper_users (fetched_at, user_id, display_name, team_name) VALUES (?, ?, ?, ?)",
            [(ts, u["user_id"], u.get("display_name"), (u.get("metadata") or {}).get("team_name")) for u in users],
        )
    return league["sport"]


def fetch_players(conn, sport, get=get_json):
    """Snapshot every player (rostered ones can be inactive or clubless); return the count."""
    players = get(f"{API}/players/{sport}") or {}
    ts = now_iso()
    rows = []
    for pid, p in players.items():
        fp = p.get("fantasy_positions") or ([p["position"]] if p.get("position") else [])
        rows.append((ts, pid, player_name(p), p.get("team"), json.dumps(fp),
                     p.get("status"), p.get("injury_status"), int(bool(p.get("active", True)))))
    with conn:
        conn.executemany("INSERT INTO sleeper_players VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def fetch_rosters(conn, league_id, get=get_json):
    rosters = get(f"{API}/league/{league_id}/rosters") or []
    ts = now_iso()
    rows = [(ts, r.get("roster_id"), r.get("owner_id"),
             json.dumps(r.get("players") or []), json.dumps(r.get("starters") or []),
             json.dumps(r.get("reserve") or []),
             *((r.get("settings") or {}).get(k) for k in ("wins", "losses", "ties")))
            for r in rosters]
    with conn:
        conn.executemany("INSERT INTO sleeper_rosters (fetched_at, roster_id, owner_id, players_json, starters_json, "
                         "reserve_json, wins, losses, ties) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def fetch_trending(conn, sport, get=get_json):
    """Snapshot trending adds. Failure is non-fatal: warn and return 0."""
    try:
        items = get(f"{API}/players/{sport}/trending/add?lookback_hours=24&limit=100") or []
    except FetchError as e:
        sys.stderr.write(f"warning: trending unavailable ({e}); leaving trend column empty\n")
        return 0
    ts = now_iso()
    rows = [(ts, "add", str(i["player_id"]), i.get("count")) for i in items if i.get("player_id")]
    with conn:
        conn.executemany("INSERT INTO sleeper_trending VALUES (?, ?, ?, ?)", rows)
    return len(rows)
