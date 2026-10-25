"""Backtest: replay finished game weeks to compare projection methods.

For week g, a player's inputs come only from his Sleeper stats in earlier
weeks of the same season; nothing from week g or later. Seasons are replayed
separately and never pooled. Methods live in METHODS, so the next projection
idea is one entry there, judged against "v1" (what the page uses today).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from .db import latest
from .gameweek import team_fixtures
from .matching import load_overrides, match_players
from .metrics import Scored, category, error_summary, mean, position_group, rank_by_position
from .projection import project
from .scoring_map import flatten_scoring_settings
from .sleeper_stats import irregular_week, sleeper_points

NEUTRAL = 0           # difficulty meaning "no fixture data": v1 uses multiplier 1.0
SAMPLE_WEEKS = 5      # scored only if he played in one of the 5 weeks before
CANNOT_REPLAY = ["v2 and market numbers (no historical odds stored)",
                 "injury and status flags (no point-in-time snapshots)",
                 "lineup and swap decisions (no historical rosters or recommendations)"]


@dataclass
class BacktestInput:
    sleeper_id: str
    position: str
    weeks: list           # [(week, stats)] earlier weeks with a stats row, most recent first, 0-minute rows included
    history: list         # league points of earlier played weeks (min > 0), most recent first
    difficulties: list    # one per week-g fixture, from his side; [NEUTRAL] without fixture data
    scoring: dict
    season: str = ""
    gw: int = 0


def _v1(inp, cfg):
    multipliers = {**cfg.difficulty_multipliers, NEUTRAL: 1.0}
    return project(inp.history, inp.difficulties, False, multipliers, cfg.avg_window).proj


def _season_avg(inp, cfg):
    avg = mean(inp.history)
    return avg * len(inp.difficulties) if avg is not None else 0.0


METHODS: dict[str, Callable[[BacktestInput, object], float]] = {"v1": _v1, "season_avg": _season_avg}


def _played(stats):
    return stats is not None and (stats.get("min") or 0) > 0


def _current_fixtures(conn, cfg, players):
    """(club of each Sleeper id, {gw: {club: [Fixture]}} builder) from the newest FPL snapshots."""
    teams = {t["team_id"]: t["short_name"] for t in latest(conn, "fpl_teams")}
    elements = [dict(e) for e in latest(conn, "fpl_elements")]
    fixtures = [dict(f) for f in latest(conn, "fpl_fixtures")]
    match = match_players(players, elements, [{"team_id": k, "short_name": v} for k, v in teams.items()],
                          load_overrides(cfg.overrides_path))
    el_team = {e["fpl_id"]: e["team_id"] for e in elements}
    club = {sid: teams.get(el_team.get(fid)) for sid, fid in match.mapping.items()}
    return club, lambda gw: team_fixtures(fixtures, teams, gw)


def _score(rows):
    return {**error_summary(rows), "rank": rank_by_position(rows)}


def run_backtest(conn, cfg, seasons=None):
    league_rows = latest(conn, "sleeper_league")
    league = json.loads(league_rows[0]["data_json"]) if league_rows else {}
    scoring = flatten_scoring_settings(league.get("scoring_settings") or {})
    current = str(league.get("season") or "")
    players = []
    for p in latest(conn, "sleeper_players"):
        p = dict(p)
        p["positions"] = json.loads(p["positions_json"] or "[]")
        players.append(p)
    pos_of = {p["player_id"]: position_group(p["positions"]) for p in players}
    if seasons is None:
        seasons = [r[0] for r in conn.execute("SELECT DISTINCT season FROM sleeper_stats ORDER BY season")]
    finished = [e["event_id"] for e in latest(conn, "fpl_events") if e["finished"]]

    out = {"seasons": {}, "cannot_replay": list(CANNOT_REPLAY)}
    for season in seasons:
        stats = {}
        for week, pid, raw in conn.execute("SELECT week, player_id, stats_json FROM sleeper_stats WHERE season = ?",
                                           (season,)):
            stats.setdefault(week, {})[pid] = json.loads(raw)
        is_current = season == current
        last = max(stats, default=0)
        notes, irregular = [], []
        if is_current:
            last = min(last, max(finished, default=0))   # never score a week still in progress
            club, fixtures_for = _current_fixtures(conn, cfg, players)
            notes.append("fixture difficulty comes from the newest snapshot; clubs are players' current clubs")
        else:
            club, fixtures_for = {}, None
            notes.append("no fixture data: v1 runs with a neutral multiplier")
        logged = {}
        for gw, sid in conn.execute("SELECT DISTINCT gw, sleeper_id FROM projection_log WHERE season = ?", (season,)):
            logged.setdefault(gw, set()).add(sid)

        weeks = []
        for gw in range(2, last + 1):
            rows = stats.get(gw)
            if not rows:
                continue
            if not is_current and irregular_week(rows):
                irregular.append(gw)
                continue
            by_club = fixtures_for(gw) if fixtures_for else {}
            recent = range(max(1, gw - SAMPLE_WEEKS), gw)
            sample = sorted({pid for w in recent for pid, s in stats.get(w, {}).items()
                             if not pid.startswith("TEAM_") and pos_of.get(pid) and _played(s)})
            scored = {name: [] for name in METHODS}
            scored_logged = {name: [] for name in METHODS}
            for pid in sample:
                prior = [(w, stats[w][pid]) for w in range(gw - 1, 0, -1) if pid in stats.get(w, {})]
                c = club.get(pid)
                difficulties = [f.difficulty for f in by_club.get(c, [])] if c else [NEUTRAL]
                inp = BacktestInput(pid, pos_of[pid], prior,
                                    [sleeper_points(s, scoring) for _, s in prior if _played(s)],
                                    difficulties, scoring, season, gw)
                actual_stats = rows.get(pid)
                pts = sleeper_points(actual_stats, scoring) if actual_stats else 0.0
                cat = category(actual_stats, pts, cfg.category_low, cfg.category_haul)
                for name, method in METHODS.items():
                    row = Scored(inp.position, method(inp, cfg), pts, cat)
                    scored[name].append(row)
                    if pid in logged.get(gw, ()):
                        scored_logged[name].append(row)
            entry = {"gw": gw, "n": len(sample), "methods": {name: _score(r) for name, r in scored.items()}}
            if any(scored_logged.values()):
                entry["logged"] = {name: _score(r) for name, r in scored_logged.items()}
            weeks.append(entry)
        out["seasons"][season] = {"weeks": weeks, "summary": _summary(weeks), "irregular_weeks": irregular,
                                  "notes": notes}
    return out


def _summary(weeks):
    ref = [w["methods"]["v1"] for w in weeks]
    summary = {"v1": {"mean_rank": mean(r["rank"] for r in ref if r["rank"] is not None),
                      "mean_mae": mean(r["mae"] for r in ref if r["mae"] is not None)}}
    for name in METHODS:
        if name == "v1":
            continue
        pairs = [(w["methods"][name], w["methods"]["v1"]) for w in weeks]
        ranked = [(m, v) for m, v in pairs if m["rank"] is not None and v["rank"] is not None]
        missed = [(m, v) for m, v in pairs if m["mae"] is not None and v["mae"] is not None]
        summary[name] = {"weeks_beat_v1_rank": sum(1 for m, v in ranked if m["rank"] > v["rank"]),
                         "weeks_compared": len(ranked),
                         "weeks_beat_v1_mae": sum(1 for m, v in missed if m["mae"] < v["mae"]),
                         "weeks_mae_compared": len(missed),
                         "mean_rank": mean(m["rank"] for m, _ in pairs if m["rank"] is not None),
                         "mean_mae": mean(m["mae"] for m, _ in pairs if m["mae"] is not None)}
    return summary


def _f(v, fmt):
    return "–" if v is None else format(v, fmt)


def format_backtest(result):
    lines = []
    names = list(METHODS)
    for season, s in result["seasons"].items():
        lines.append(f"Season {season}: {len(s['weeks'])} week(s) replayed")
        lines.append("  week     n  " + "  ".join(f"{n + ' miss/played/order':>28}" for n in names))
        for w in s["weeks"]:
            cells = "  ".join(f"{_f(m['mae'], '.2f'):>8} {_f(m['mae_played'], '.2f'):>8} {_f(m['rank'], '.2f'):>10}"
                              for m in (w["methods"][n] for n in names))
            lines.append(f"  GW{w['gw']:<3} {w['n']:>4}  {cells}")
        v1 = s["summary"]["v1"]
        lines.append(f"  v1: average miss {_f(v1['mean_mae'], '.2f')}, average order {_f(v1['mean_rank'], '.2f')}")
        for name in names[1:]:
            m = s["summary"][name]
            lines.append(f"  {name} vs v1: better order in {m['weeks_beat_v1_rank']} of {m['weeks_compared']} weeks, "
                         f"smaller miss in {m['weeks_beat_v1_mae']} of {m['weeks_mae_compared']} weeks "
                         f"(average miss {_f(m['mean_mae'], '.2f')}, average order {_f(m['mean_rank'], '.2f')})")
        for note in s["notes"]:
            lines.append(f"  note: {note}")
        if s["irregular_weeks"]:
            lines.append(f"  skipped (blank or double weeks): {', '.join(f'GW{w}' for w in s['irregular_weeks'])}")
    lines.append("Can't replay: " + "; ".join(result["cannot_replay"]))
    return "\n".join(lines)
