"""Past Premier League results from football-data.co.uk, for head-to-head records.

One CSV per season (free, no key). Clubs are renamed to FPL's names so they
line up with fpl_teams; overrides.json `results_teams` fixes the rest.
"""

import csv
import io
from datetime import datetime

from .db import meta_get, meta_set, now_iso
from .http import get_text as _get_text

URL = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
META = "results_fetched_{season}"
# football-data.co.uk name -> FPL fpl_teams.name; the rest are already the same.
RESULTS_ALIASES = {
    "Man United": "Man Utd",
    "Tottenham": "Spurs",
    "Nott'm Forest": "Nott'm Forest",
    "Sheffield United": "Sheffield Utd",
}


def _date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_results(text, season, aliases):
    """Rows (season, date, home, away, home_goals, away_goals) from one season's CSV."""
    out = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
        date = _date(row.get("Date") or "")
        try:
            hg, ag = int(row.get("FTHG") or ""), int(row.get("FTAG") or "")
        except ValueError:
            continue
        if not (home and away and date):
            continue
        out.append((season, date, aliases.get(home, home), aliases.get(away, away), hg, ag))
    return out


def fetch_results(conn, from_season, last_season, overrides, *, force=False, get_text=_get_text):
    """Fetch seasons from_season..last_season (start years) not stored yet. Returns how many were fetched."""
    aliases = {**RESULTS_ALIASES, **(overrides.get("results_teams") or {})}
    fetched = 0
    for year in range(from_season, last_season + 1):
        season = str(year)
        if not force and meta_get(conn, META.format(season=season)):
            continue
        code = f"{year % 100:02d}{(year + 1) % 100:02d}"
        rows = parse_results(get_text(URL.format(code=code)), season, aliases)
        with conn:
            conn.execute("DELETE FROM match_results WHERE season = ?", (season,))
            conn.executemany("INSERT OR REPLACE INTO match_results VALUES (?, ?, ?, ?, ?, ?)", rows)
        meta_set(conn, META.format(season=season), now_iso())
        fetched += 1
    return fetched
