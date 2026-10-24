"""Which game week to plan for, and whether pickups are instant or waivers.

Players are instant free agents until a game week's first kickoff; once a
game week is under way, pickups go through waivers and we plan the next one.
"""

from dataclasses import dataclass
from datetime import datetime

from .models import Fixture


@dataclass
class GameweekInfo:
    gw: int | None
    mode: str                     # "instant" | "waiver"
    first_kickoff: datetime | None


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def season_of(events):
    """Season label = year of the earliest game-week deadline ("2026" for 2026/27)."""
    deadlines = [parse_time(e.get("deadline_time")) for e in events]
    deadlines = [d for d in deadlines if d]
    return str(min(deadlines).year) if deadlines else None


def _first_kickoffs(fixtures):
    out = {}
    for f in fixtures:
        k = parse_time(f.get("kickoff_time"))
        if f.get("event") is None or k is None:
            continue
        out[f["event"]] = min(out.get(f["event"], k), k)
    return out


def target_gameweek(events, fixtures, now):
    kickoffs = _first_kickoffs(fixtures)
    events = sorted(events, key=lambda e: e["event_id"])
    unfinished = [e for e in events if not e["finished"]]

    in_progress = [e for e in unfinished
                   if kickoffs.get(e["event_id"]) and kickoffs[e["event_id"]] <= now]
    if in_progress:
        current = in_progress[-1]["event_id"]
        later = [e["event_id"] for e in events if e["event_id"] > current]
        gw = later[0] if later else None
        return GameweekInfo(gw, "waiver", kickoffs.get(gw))

    upcoming = [e["event_id"] for e in unfinished
                if kickoffs.get(e["event_id"]) and kickoffs[e["event_id"]] > now]
    gw = upcoming[0] if upcoming else None
    return GameweekInfo(gw, "instant", kickoffs.get(gw))


def team_fixtures(fixtures, teams, gw):
    """{team short_name: [Fixture, ...]} for game week `gw`, in kickoff order."""
    out = {}
    rows = sorted((f for f in fixtures if f.get("event") == gw), key=lambda f: f.get("kickoff_time") or "")
    for f in rows:
        h, a = teams.get(f["team_h"]), teams.get(f["team_a"])
        if not h or not a:
            continue
        out.setdefault(h, []).append(Fixture(a, True, int(f["team_h_difficulty"]), f.get("kickoff_time")))
        out.setdefault(a, []).append(Fixture(h, False, int(f["team_a_difficulty"]), f.get("kickoff_time")))
    return out
