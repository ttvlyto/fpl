"""SQLite schema and snapshot helpers.

Snapshot tables are append-only: every fetch run inserts rows tagged with one
`fetched_at`, and readers use the newest batch via `latest()`.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_TABLES = {
    "sleeper_league": "fetched_at, league_id, data_json",
    "sleeper_players": "fetched_at, player_id, name, team, positions_json, status, injury_status, active",
    "sleeper_rosters": "fetched_at, roster_id, owner_id, players_json, starters_json, reserve_json",
    "sleeper_users": "fetched_at, user_id, display_name",
    "sleeper_trending": "fetched_at, direction, player_id, count",
    "fpl_elements": ("fetched_at, fpl_id, first_name, second_name, web_name, team_id, "
                     "element_type, status, chance_next, total_minutes"),
    "fpl_teams": "fetched_at, team_id, short_name, name",
    "fpl_events": "fetched_at, event_id, deadline_time, finished",
    "fpl_fixtures": ("fetched_at, fixture_id, event, team_h, team_a, team_h_difficulty, "
                     "team_a_difficulty, kickoff_time, finished"),
}

OTHER_SCHEMA = """
-- FPL reuses player and fixture ids every season, so season is part of the key.
CREATE TABLE IF NOT EXISTS fpl_history (
    season TEXT, fpl_id INTEGER, fixture_id INTEGER, round INTEGER, kickoff_time TEXT,
    minutes INTEGER, stats_json TEXT, fetched_at TEXT,
    PRIMARY KEY (season, fpl_id, fixture_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

TABLES = set(SNAPSHOT_TABLES) | {"fpl_history", "meta"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for table, cols in SNAPSHOT_TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_fetched ON {table}(fetched_at)")
    conn.executescript(OTHER_SCHEMA)
    return conn


def _check(table):
    if table not in SNAPSHOT_TABLES:
        raise ValueError(f"not a snapshot table: {table!r}")


def latest(conn, table):
    """Rows of the newest snapshot in `table`, or [] if it is empty."""
    _check(table)
    return conn.execute(
        f"SELECT * FROM {table} WHERE fetched_at = (SELECT MAX(fetched_at) FROM {table})"
    ).fetchall()


def latest_fetched_at(conn, table):
    _check(table)
    return conn.execute(f"SELECT MAX(fetched_at) FROM {table}").fetchone()[0]


def meta_get(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(conn, key, value):
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
