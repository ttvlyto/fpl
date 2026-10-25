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
                     "element_type, status, chance_next, total_minutes, ep_next, news, news_added, chance_this"),
    "fpl_teams": "fetched_at, team_id, short_name, name",
    "fpl_events": "fetched_at, event_id, deadline_time, finished",
    "fpl_fixtures": ("fetched_at, fixture_id, event, team_h, team_a, team_h_difficulty, "
                     "team_a_difficulty, kickoff_time, finished, team_h_score, team_a_score"),
    # Betting markets. market: 1x2 | total | team_total | btts; side: team code, draw, over or yes.
    "market_quotes": "fetched_at, source, home, away, kickoff, market, line, side, bid, ask, depth, ref",
    # Player markets: goal1 | goal2 | soa.
    "player_quotes": "fetched_at, source, home, away, kickoff, player, team, market, bid, ask, last, depth, ref",
}

OTHER_SCHEMA = """
-- FPL reuses player and fixture ids every season, so season is part of the key.
CREATE TABLE IF NOT EXISTS fpl_history (
    season TEXT, fpl_id INTEGER, fixture_id INTEGER, round INTEGER, kickoff_time TEXT,
    minutes INTEGER, stats_json TEXT, fetched_at TEXT,
    PRIMARY KEY (season, fpl_id, fixture_id)
);
CREATE TABLE IF NOT EXISTS sleeper_stats (
    season TEXT, week INTEGER, player_id TEXT, stats_json TEXT, fetched_at TEXT,
    PRIMARY KEY (season, week, player_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- Past Premier League results (football-data.co.uk); season is the start year, teams are FPL names.
CREATE TABLE IF NOT EXISTS match_results (
    season TEXT, date TEXT, home TEXT, away TEXT, home_goals INTEGER, away_goals INTEGER,
    PRIMARY KEY (season, date, home, away)
);
-- Fitted start models; the newest version is used.
CREATE TABLE IF NOT EXISTS minutes_model (
    version TEXT PRIMARY KEY, fitted_at TEXT, n_rows INTEGER, trained_on TEXT, model_json TEXT,
    holdout_brier REAL
);
-- How often players with each label started and played (O) against the model's expectation (E).
-- Rebuilt from projection_log and sleeper_stats, never appended.
CREATE TABLE IF NOT EXISTS label_counts (
    season TEXT, kind TEXT, cell TEXT, n INTEGER, starts INTEGER, apps INTEGER, sixty INTEGER,
    e_start REAL, e_app REAL, built_at TEXT,
    PRIMARY KEY (season, kind, cell)
);
"""

# Append-only logs for the report card. Columns are only ever added at the end,
# so `connect` can migrate an existing database in place.
LOG_TABLES = {
    # Every build's projections.
    "projection_log": ("made_at TEXT, season TEXT, gw INTEGER, sleeper_id TEXT, kickoff TEXT, "
                       "v1 REAL, v2 REAL, p_goal_market REAL, position TEXT, ep_next REAL, "
                       # Minutes model (shadow): final and pre-label probabilities, and the labels used.
                       "v1m REAL, v2m REAL, p_start REAL, p_app REAL, p60 REAL, e_min REAL, "
                       "fpl_bucket TEXT, slp_bucket TEXT, p_start_model REAL, p_app_model REAL, "
                       "p_start_fpl REAL, p_app_fpl REAL, label_at TEXT"),
    # Every build's fitted match odds; method: market (later blend or rating).
    "match_log": ("made_at TEXT, season TEXT, gw INTEGER, fixture_id INTEGER, kickoff TEXT, method TEXT, "
                  "lam_home REAL, lam_away REAL, p_home REAL, p_draw REAL, p_away REAL, "
                  "cs_home REAL, cs_away REAL, n_quotes INTEGER, avg_spread REAL"),
    # Every build's suggested XI (sleeper ids) and swaps ([drop_id, add_id]).
    "recommendation_log": ("made_at TEXT, season TEXT, gw INTEGER, first_kickoff TEXT, roster_id INTEGER, "
                           "xi_json TEXT, swaps_json TEXT"),
    # Claude's second opinion, once per game week: [{player_id, slot, reason}], [{drop_id, add_id, reason}],
    # [{fixture, note}].
    "second_opinion_log": ("made_at TEXT, season TEXT, gw INTEGER, first_kickoff TEXT, model TEXT, "
                           "xi_json TEXT, swaps_json TEXT, notes_json TEXT, summary TEXT, "
                           "input_tokens INTEGER, output_tokens INTEGER"),
}

TABLES = set(SNAPSHOT_TABLES) | set(LOG_TABLES) | {"fpl_history", "sleeper_stats", "meta", "minutes_model",
                                                    "label_counts", "match_results"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for table, cols in SNAPSHOT_TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
        _add_missing_columns(conn, table, cols)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_fetched ON {table}(fetched_at)")
    for table, cols in LOG_TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
        _add_missing_columns(conn, table, cols)
    conn.executescript(OTHER_SCHEMA)
    return conn


def _add_missing_columns(conn, table, cols):
    """Older databases lack columns added since: append them."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for col in cols.split(","):
        col = col.strip()
        if col.split()[0] not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    conn.commit()


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
