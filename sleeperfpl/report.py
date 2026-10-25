"""Assemble the game-week report from the DB and render it as a static page.

report.json is the structured result (a later Claude review can read it);
index.html is the page the owner opens.
"""

import html
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from .availability import (LabelTable, apply_labels, audit_rows, chance_for, fpl_bucket, fpl_stale, hard_out_weeks, is_hard_out,
                           load_counts, rebuild_label_counts, slp_bucket)
from .db import latest, latest_fetched_at, meta_get
from .fetch_fpl import HISTORY_META
from .gameweek import MATCH_LENGTH, live_gameweek, matches_left, season_of, target_gameweek, team_fixtures
from .gameweek import parse_time
from .lineup import best_xi, rank_free_agents, starting_slots, swap_tips
from .matching import FPL_POSITIONS, load_overrides, match_players, normalize_name
from .live import unlikely_to_play, zero_minute_replacements
from .markets import find_fixture, latest_quotes, mid, team_code, usable
from .minutes import fixture_minutes, history, last_season_start_share, player_minutes, regular_weeks, season_rows
from .minutes_fit import load_model
from .models import PlayerCtx
from .pricing import fixture_odds
from .projection import PositionRates, minutes_rates, project, project_minutes
from .projection_v2 import SLEEPER_PREFIX, project_v2, project_v2m, v2_inputs, v2m_inputs
from .report_card import (log_goal_odds, log_matches, log_projections, log_recommendation, score_decisions,
                          score_matches, score_weeks, summary)
from .scoring_map import coverage, flatten_scoring_settings, league_drift, load_scoring_map, score_match
from .sleeper_stats import META as SLEEPER_STATS_META, has_stats, sleeper_points, sleeper_weeks

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sleeper import rostered_ids  # noqa: E402

class BuildError(Exception):
    pass


def _rows(conn, table):
    return [dict(r) for r in latest(conn, table)]


def _owner_roster(users, rosters, owner):
    user = next((u for u in users if owner in (u["user_id"], u["display_name"])), None)
    roster = next((r for r in rosters if user and r["owner_id"] == user["user_id"]), None)
    if roster is None:
        names = ", ".join(sorted(u["display_name"] or u["user_id"] for u in users))
        raise BuildError(f"No roster for owner '{owner}' in this league. League members: {names}")
    return roster


def _flag(sp, el, deadline_passed):
    if el is None:
        return "no data"
    bucket = fpl_bucket(el["status"], chance_for(el, deadline_passed))
    if is_hard_out(bucket):
        return "out"
    if bucket[0] in "di":
        return "doubtful"
    slp = slp_bucket(sp.get("injury_status"))
    return f"Sleeper: {slp}" if slp != "none" else None


def _history(conn, season, fpl_id, position, rules, max_round=None):
    rows = conn.execute("SELECT stats_json FROM fpl_history WHERE season = ? AND fpl_id = ? AND minutes > 0 "
                        "AND (? IS NULL OR round <= ?) ORDER BY kickoff_time DESC",
                        (season, fpl_id, max_round, max_round)).fetchall()
    return [score_match(json.loads(r[0]), position, rules) for r in rows]


def _player_dict(pl):
    return {
        "sleeper_id": pl.sleeper_id, "name": pl.name, "positions": pl.positions, "club": pl.club,
        "flag": pl.flag, "avg": None if pl.avg is None else round(pl.avg, 1), "n": pl.n,
        "proj": round(pl.proj, 1), "trending_adds": pl.trending_adds, "undroppable": pl.undroppable,
        "proj_v1": round(pl.proj_v1, 1), "proj_source": pl.proj_source, "market_flags": pl.market_flags,
        "p_goal_market": None if pl.p_goal_market is None else round(pl.p_goal_market, 2),
        "p_goal_hist": None if pl.p_goal_hist is None else round(pl.p_goal_hist, 2),
        **{k: None if getattr(pl, k) is None else round(getattr(pl, k), 2) for k in ("p_start", "p_app", "p60")},
        "e_min": None if pl.e_min is None else round(pl.e_min, 1),
        "fpl_bucket": pl.fpl_bucket, "slp_bucket": pl.slp_bucket,
        "proj_v1m": None if pl.proj_v1m is None else round(pl.proj_v1m, 1),
        "proj_v2m": None if pl.proj_v2m is None else round(pl.proj_v2m, 1),
        "fixtures": [{"opponent": f.opponent, "home": f.home, "difficulty": f.difficulty, "kickoff": f.kickoff}
                     for f in pl.fixtures],
    }


def _share(weeks, test):
    return sum(1 for w in weeks if test(w)) / len(weeks) if weeks else None


def _goal_quotes(conn, cfg, now, codes, overrides, fixtures, teams):
    """{(normalized player name, FPL team code, fixture_id): P(1+ goals)} from fresh, usable,
    pre-kickoff quotes, each tied to the exact fixture it prices."""
    cutoff = now - timedelta(hours=cfg.market_stale_hours)
    out = {}
    for q in sorted(latest_quotes(conn, "player_quotes"), key=lambda q: q["fetched_at"]):
        fetched = parse_time(q["fetched_at"])
        if q["market"] != "goal1" or not fetched or fetched < cutoff or not usable(q, cfg):
            continue
        team = team_code(q["source"], q["team"], codes, overrides)
        home = team_code(q["source"], q["home"], codes, overrides)
        away = team_code(q["source"], q["away"], codes, overrides)
        fixture = find_fixture(home, away, q.get("kickoff"), fixtures, teams) if home and away else None
        kickoff = parse_time(fixture.get("kickoff_time")) if fixture else None
        if team and fixture and kickoff and fetched < kickoff:
            out[(normalize_name(q["player"]), team, fixture["fixture_id"])] = mid(q)
    return out


def _match_entry(f, teams, odds, stale):
    base = {"fixture_id": f["fixture_id"], "home": teams.get(f["team_h"]), "away": teams.get(f["team_a"]),
            "kickoff": f.get("kickoff_time")}
    if odds is None:
        return {**base, "market": None}
    return {**base, "market": "ok", "stale": stale, "sources": odds.sources, "fetched_at": odds.fetched_at,
            **{k: round(getattr(odds, k), 2) for k in ("p_home", "p_draw", "p_away", "lam_home", "lam_away",
                                                        "cs_home", "cs_away")}}


def build_report(conn, cfg, now):
    league_rows = _rows(conn, "sleeper_league")
    players = _rows(conn, "sleeper_players")
    rosters = _rows(conn, "sleeper_rosters")
    elements = _rows(conn, "fpl_elements")
    if not (league_rows and players and rosters and elements):
        raise BuildError("no data yet; run fetch-daily and fetch-rosters first")

    league = json.loads(league_rows[0]["data_json"])
    slots = starting_slots(league.get("roster_positions") or [], cfg.slot_eligibility)
    scoring = flatten_scoring_settings(league.get("scoring_settings") or {})
    sleeper_season = str(league.get("season") or "")
    use_sleeper = cfg.stats_source == "sleeper" and has_stats(conn, sleeper_season)
    if use_sleeper:
        # Exact league points straight from Sleeper's stats: nothing to approximate.
        rules, covered, missing, changed = None, sorted(scoring), [], []
    else:
        rules = load_scoring_map(cfg.scoring_map_path)
        covered, missing = coverage(rules)
        not_in_map, changed = league_drift(rules, scoring)
        missing = sorted(set(missing) | set(not_in_map))

    for r in rosters:
        for key in ("players_json", "starters_json", "reserve_json"):
            r[key[:-5]] = json.loads(r[key] or "[]")
    mine = _owner_roster(_rows(conn, "sleeper_users"), rosters, cfg.owner)
    taken = rostered_ids(rosters)

    teams = {t["team_id"]: t["short_name"] for t in _rows(conn, "fpl_teams")}
    fixtures = _rows(conn, "fpl_fixtures")
    events = _rows(conn, "fpl_events")
    season = season_of(events) or "unknown"
    gw = target_gameweek(events, fixtures, now)
    by_team = team_fixtures(fixtures, teams, gw.gw) if gw.gw else {}
    target_event = next((e for e in events if e["event_id"] == gw.gw), None)
    deadline = parse_time(target_event["deadline_time"]) if target_event else None
    deadline_passed = deadline is not None and deadline <= now
    finished = [e["event_id"] for e in events if e["finished"]]
    last_finished = max(finished) if finished else None
    playing = live_gameweek(events, fixtures, now)
    # Planning the next week while this one is played: its stats, minutes and news aren't in yet.
    in_progress = ({"gameweek": playing, "matches_left": matches_left(fixtures, playing)}
                   if playing and playing != gw.gw else None)
    overrides = load_overrides(cfg.overrides_path)
    codes = set(teams.values())

    # Betting markets: fresh odds drive projections; stale ones are only displayed.
    quotes = latest_quotes(conn, "market_quotes")
    fodds, markets_unmatched = fixture_odds(quotes, fixtures, teams, cfg, now, overrides)
    all_odds = {}
    if any(f["fixture_id"] not in fodds for f in fixtures if gw.gw and f.get("event") == gw.gw):
        # Only needed to show stale prices for fixtures without fresh ones.
        any_age = SimpleNamespace(**{**vars(cfg), "market_stale_hours": 24 * 365 * 50})
        all_odds, _ = fixture_odds(quotes, fixtures, teams, any_age, now, overrides)
    team_lams = {}
    for o in fodds.values():
        team_lams.setdefault(o.home, []).append(o.lam_home)
        team_lams.setdefault(o.away, []).append(o.lam_away)
    baseline = {t: sum(v) / len(v) for t, v in team_lams.items()}
    goal_quotes = _goal_quotes(conn, cfg, now, codes, overrides, fixtures, teams)

    for p in players:
        p["positions"] = json.loads(p["positions_json"] or "[]")
    match = match_players(players, elements, [{"team_id": k, "short_name": v} for k, v in teams.items()],
                          overrides)
    el_by_id = {e["fpl_id"]: e for e in elements}
    trending = {t["player_id"]: t["count"] for t in _rows(conn, "sleeper_trending")}

    my_ids, reserve = set(mine["players"]), set(mine["reserve"])
    mins = (_MinutesInputs(conn, cfg, sleeper_season, last_finished, fixtures, teams, now, scoring)
            if use_sleeper else None)
    ctx = {}
    for p in players:
        sid = p["player_id"]
        if sid not in my_ids and (sid in taken or not p["team"] or not p["active"]):
            continue  # someone else's player, or not a usable free agent
        el = el_by_id.get(match.mapping.get(sid))
        fpl_id = el["fpl_id"] if el else None  # an id missing from FPL counts as unmatched
        club = teams.get(el["team_id"], p["team"]) if el else p["team"]
        pl = PlayerCtx(sleeper_id=sid, name=p["name"] or sid, positions=p["positions"], club=club,
                       fpl_id=fpl_id, on_roster=sid in my_ids, in_reserve=sid in reserve,
                       flag=_flag(p, el, deadline_passed), fixtures=by_team.get(club, []) if el else [],
                       trending_adds=trending.get(sid))
        if el:
            pl.ep_next = el["ep_next"]
            pos = FPL_POSITIONS.get(el["element_type"], "")
            weeks = sleeper_weeks(conn, sleeper_season, sid, last_finished) if use_sleeper else []
            if use_sleeper:
                pl.history = [sleeper_points(w, scoring) for w in weeks]
            else:
                pl.history = _history(conn, season, fpl_id, pos, rules, last_finished)
            pr = project(pl.history, [f.difficulty for f in pl.fixtures], pl.flag == "out",
                         cfg.difficulty_multipliers, cfg.avg_window)
            pl.avg, pl.n, pl.proj = pr.avg, pr.n, pr.proj
            pl.proj_v1 = pr.proj
            if use_sleeper:
                _apply_market(pl, pos, weeks, scoring, fodds, baseline, goal_quotes, cfg)
            if mins and mins.model:
                _apply_minutes(pl, pos, p, el, weeks, scoring, fodds, baseline, cfg, mins, deadline_passed)
        ctx[sid] = pl
    for sid in my_ids - set(ctx):
        # On the roster but absent from Sleeper's player map: show it, never hide it.
        ctx[sid] = PlayerCtx(sleeper_id=sid, name=f"Sleeper player {sid}", positions=[], club="",
                             fpl_id=None, on_roster=True, in_reserve=sid in reserve, flag="no data")

    roster = [pl for pl in ctx.values() if pl.on_roster]
    keep_unmatched = []
    for entry in cfg.undroppable or []:
        hits = [pl for pl in roster if entry == pl.sleeper_id or normalize_name(entry) == normalize_name(pl.name)]
        for pl in hits:
            pl.undroppable = True
        if not hits:
            keep_unmatched.append(entry)
    free_agents = [pl for pl in ctx.values() if not pl.on_roster]
    lineup = best_xi(roster, slots, cfg.slot_eligibility)
    swaps = swap_tips(roster, free_agents, slots, cfg.slot_eligibility, cfg.swap_min_gain, cfg.max_swaps)
    ranked = rank_free_agents(free_agents, cfg.free_agents_per_position)

    made_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if use_sleeper and gw.gw:
        listed = [pl for group in ranked.values() for pl in group]
        log_projections(conn, sleeper_season, gw.gw, roster + listed, made_at,
                        label_at=latest_fetched_at(conn, "fpl_elements"))
        first = gw.first_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ") if gw.first_kickoff else None
        log_recommendation(conn, sleeper_season, gw.gw, first, mine["roster_id"], lineup, swaps, made_at)
        log_matches(conn, sleeper_season, gw.gw, fodds, fixtures, made_at)
    card = (score_weeks(conn, sleeper_season, scoring, last_finished, cfg.category_low, cfg.category_haul)
            if use_sleeper else [])
    decisions = (score_decisions(conn, sleeper_season, scoring, last_finished, slots, cfg.slot_eligibility)
                 if use_sleeper else [])
    match_card = score_matches(conn, sleeper_season, last_finished) if use_sleeper else []
    club_of = {sid: teams.get(el_by_id[fid]["team_id"]) for sid, fid in match.mapping.items() if fid in el_by_id}
    live = _live_block(conn, cfg, now, events, fixtures, teams, codes, overrides, mine, ctx, slots,
                       {p["player_id"]: p for p in players}, sleeper_season, fodds, club_of, goal_quotes)
    target = sorted((f for f in fixtures if gw.gw and f.get("event") == gw.gw), key=lambda f: f.get("kickoff_time") or "")
    matches = [_match_entry(f, teams, fodds.get(f["fixture_id"]) or all_odds.get(f["fixture_id"]),
                            f["fixture_id"] not in fodds) for f in target]
    priced = sum(1 for f in target if f["fixture_id"] in fodds)
    note = "difficulty" if not use_sleeper or priced == 0 else "market" if priced == len(target) else "mixed"

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live": live,
        "report_card": {"weeks": [_rounded(w) for w in card[-8:]],
                        "decisions": [_rounded(d) for d in decisions[-8:]],
                        "matches": [_rounded(m) for m in match_card[-8:]],
                        "summary": summary(card, decisions),
                        "labels": [_rounded(x) for x in audit_rows(conn, sleeper_season)] if use_sleeper else []},
        "matches": matches,
        "markets_unmatched": markets_unmatched,
        "projection_note": note,
        "gameweek": gw.gw,
        "mode": gw.mode,
        "in_progress": in_progress,
        "first_kickoff": gw.first_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ") if gw.first_kickoff else None,
        "stats_source": "sleeper" if use_sleeper else "fpl",
        "undroppable_unmatched": keep_unmatched,
        "sources": {
            "sleeper_stats_week": meta_get(conn, SLEEPER_STATS_META),
            "sleeper_rosters": latest_fetched_at(conn, "sleeper_rosters"),
            "sleeper_players": latest_fetched_at(conn, "sleeper_players"),
            "sleeper_trending": latest_fetched_at(conn, "sleeper_trending"),
            "fpl_bootstrap": latest_fetched_at(conn, "fpl_elements"),
            "fpl_fixtures": latest_fetched_at(conn, "fpl_fixtures"),
            "fpl_history_gw": meta_get(conn, HISTORY_META),
        },
        "coverage": {"covered": covered, "missing": missing, "changed": changed},
        "xi": {
            "total": round(lineup.total, 1),
            "starters": [{"slot": s, "player": _player_dict(pl) if pl else None} for s, pl in lineup.starters],
            "bench": [_player_dict(pl) for pl in lineup.bench],
            "empty_slots": lineup.empty_slots,
        },
        "swaps": [{"drop": _player_dict(s.drop), "add": _player_dict(s.add), "gain": round(s.gain, 1),
                   "kind": s.kind} for s in swaps],
        "free_agents": {pos: [_player_dict(pl) for pl in group] for pos, group in sorted(ranked.items())},
        "unmatched_rostered": [_player_dict(pl) for pl in roster if pl.fpl_id is None],
        "unmatched_count": len(match.unmatched),
    }


def _rounded(value):
    """Floats to 2 places, through nested dicts and lists (report card rows)."""
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def _apply_market(pl, pos, weeks, scoring, fodds, baseline, goal_quotes, cfg):
    """Re-price the projection with betting odds and flag history/market disagreements."""
    if not weeks:
        return
    inp = v2_inputs(weeks, scoring, SLEEPER_PREFIX.get(pos, ""), cfg.avg_window)
    if pl.flag != "out" and pl.fixtures and inp:
        parts, used = [], False
        for f in pl.fixtures:
            o = fodds.get(f.fixture_id)
            if o:
                own, opp = (o.lam_home, o.lam_away) if f.home else (o.lam_away, o.lam_home)
                parts.append(project_v2(inp, own, opp, baseline.get(pl.club, cfg.league_avg_goals)))
                used = True
            else:  # no usable market for this fixture: v1 for it
                parts.append((pl.avg or 0.0) * cfg.difficulty_multipliers[f.difficulty])
        if used:
            pl.proj, pl.proj_source = sum(parts), "market"
            pl.proj_v2 = pl.proj

    first = fodds.get(pl.fixtures[0].fixture_id) if pl.fixtures else None
    if pos in ("GK", "DEF") and first:
        hist_cs = _share(weeks, lambda w: (w.get("cs") or 0) >= 1)
        mkt_cs = first.cs_home if pl.fixtures[0].home else first.cs_away
        if abs(hist_cs - mkt_cs) >= cfg.flag_cs_gap:
            pl.market_flags.append("cs")
    pl.p_goal_hist = _share(weeks, lambda w: (w.get("g") or 0) >= 1)
    pl.p_goal_market = (goal_quotes.get((normalize_name(pl.name), pl.club, pl.fixtures[0].fixture_id))
                        if pl.fixtures else None)
    if pl.p_goal_market is not None and abs(pl.p_goal_market - pl.p_goal_hist) >= cfg.flag_goal_gap:
        pl.market_flags.append("goal")


class _MinutesInputs:
    """What every player's minutes estimate needs, loaded once per build."""

    def __init__(self, conn, cfg, season, last_finished, fixtures, teams, now, scoring):
        self.model = load_model(conn)
        self.rows = season_rows(conn, season, last_finished) if self.model else {}
        self.rates = PositionRates(scoring)
        for rows in self.rows.values():
            self.rates.add(rows)
        self.regular = regular_weeks(self.rows)
        self.before = (last_finished or 0) + 1
        if self.model:
            rebuild_label_counts(conn, season, last_finished, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.table = LabelTable(load_counts(conn, season), k=cfg.label_prior_k)
        self.excluded = hard_out_weeks(conn, season)     # sleeper id -> weeks FPL had him out
        self._season, self._conn, self._last = season, conn, None
        self.club_last = {}
        for f in fixtures:
            k = f.get("kickoff_time")
            if k and parse_time(k) < now:
                for code in (teams.get(f["team_h"]), teams.get(f["team_a"])):
                    if code and k > self.club_last.get(code, ""):
                        self.club_last[code] = k

    def last_season(self):
        if self._last is None:
            prev = str(int(self._season) - 1) if self._season.isdigit() else ""
            self._last = season_rows(self._conn, prev) if prev else {}
        return self._last


def _apply_minutes(pl, pos, sp, el, weeks, scoring, fodds, baseline, cfg, mins, deadline_passed):
    """Shadow minutes projections; with minutes_model "on" they also replace proj."""
    model = mins.model
    hist = history(mins.rows, pl.sleeper_id, mins.before, mins.regular, mins.excluded.get(pl.sleeper_id, set()))
    share = last_season_start_share(mins.last_season(), pl.sleeper_id) if len(hist) < 3 else None
    pm = player_minutes(hist, pos, model, share)
    pl.fpl_bucket = fpl_bucket(el["status"], chance_for(el, deadline_passed))
    pl.slp_bucket = slp_bucket(sp.get("injury_status"))
    stale = fpl_stale(el["news_added"], mins.club_last.get(pl.club))
    pl.p_start_model, pl.p_app_model = pm.p_start, pm.p_app
    pl.p_start_fpl, pl.p_app_fpl, pl.p_start, pl.p_app = apply_labels(
        pm.p_start, pm.p_app, pl.fpl_bucket, pl.slp_bucket, stale, mins.table, cfg.sleeper_label_weight)
    fm = fixture_minutes(pl.p_start, pl.p_app, pm.start_buckets, pm.sub_buckets)
    pl.p60, pl.e_min = fm.p60, fm.e_min
    recent = weeks[:cfg.avg_window]
    prefix = SLEEPER_PREFIX.get(pos, "")
    prior = mins.rates.prior(prefix)
    k = cfg.minutes_shrink_k if prior else 0.0
    lin90, cs60 = minutes_rates(recent, scoring, prior.lin90 if prior else 0.0, k)
    mults = [cfg.difficulty_multipliers[f.difficulty] for f in pl.fixtures]
    pl.proj_v1m = project_minutes(lin90, cs60, [fm] * len(pl.fixtures), mults)
    inp = v2m_inputs(weeks, scoring, prefix, cfg.avg_window, (prior.att90, prior.other90) if prior else (0.0, 0.0), k)
    if pl.proj_source == "market" and inp:
        parts = []
        for f, mult in zip(pl.fixtures, mults):
            o = fodds.get(f.fixture_id)
            if o:
                own, opp = (o.lam_home, o.lam_away) if f.home else (o.lam_away, o.lam_home)
                parts.append(project_v2m(inp, fm, own, opp, baseline.get(pl.club, cfg.league_avg_goals)))
            else:
                parts.append(project_minutes(lin90, cs60, [fm], [mult]))
        pl.proj_v2m = sum(parts)
    if cfg.minutes_model == "on":
        pl.proj = pl.proj_v2m if pl.proj_v2m is not None else pl.proj_v1m


def _club_minutes_in(minutes, club_of):
    """{club: (players with minutes > 0, matched players)} from this week's stats."""
    played, size = {}, {}
    for sid, club in club_of.items():
        size[club] = size.get(club, 0) + 1
        if (minutes.get(sid) or 0) > 0:
            played[club] = played.get(club, 0) + 1
    return {club: (played.get(club, 0), n) for club, n in size.items()}


def _live_block(conn, cfg, now, events, fixtures, teams, codes, overrides, mine, ctx, slots,
                players_by_id, sleeper_season, fodds, club_of, goal_quotes):
    gw = live_gameweek(events, fixtures, now)
    if gw is None:
        return None
    # Minutes are only known once Sleeper's stats for this week were fetched after the match ended;
    # a player missing from earlier stats is "unknown", never "0 minutes".
    stats_at = parse_time(conn.execute("SELECT MAX(fetched_at) FROM sleeper_stats WHERE season = ? AND week = ?",
                                       (sleeper_season, gw)).fetchone()[0])
    started, finished_any, in_progress = set(), set(), set()
    live_fx = [f for f in fixtures if f.get("event") == gw]
    for f in live_fx:
        k = parse_time(f.get("kickoff_time"))
        for code in (teams.get(f["team_h"]), teams.get(f["team_a"])):
            if not k or k > now:
                continue
            started.add(code)
            if f.get("finished") or k + MATCH_LENGTH <= now:
                if stats_at and stats_at >= k + MATCH_LENGTH:
                    finished_any.add(code)
                else:
                    in_progress.add(code)   # over, but its minutes aren't in yet
            else:
                in_progress.add(code)
    minutes = {}
    for pid, raw in conn.execute("SELECT player_id, stats_json FROM sleeper_stats WHERE season = ? AND week = ?",
                                 (sleeper_season, gw)):
        minutes[pid] = json.loads(raw).get("min") or 0
    # Trust a club's minutes only once its players actually show up in the stats
    # (Sleeper can lag, and a delayed kickoff looks like a finished match).
    counts = _club_minutes_in(minutes, club_of)
    trusted = {c for c, (played, n) in counts.items() if played >= max(1, min(7, n // 2))}
    finished = (finished_any - in_progress) & trusted
    upcoming = set()
    for f in live_fx:
        k = parse_time(f.get("kickoff_time"))
        if k and k > now:
            upcoming |= {teams.get(f["team_h"]), teams.get(f["team_a"])}

    starter_ids = list(mine.get("starters") or [])
    starters = [(slot, ctx.get(sid)) for slot, sid in zip(slots, starter_ids)]
    bench = [pl for pl in ctx.values() if pl.on_roster and pl.sleeper_id not in starter_ids]
    cutoff = now - timedelta(hours=cfg.market_stale_hours)
    by_team = {}
    for q in latest_quotes(conn, "player_quotes"):
        fetched = parse_time(q["fetched_at"])
        team = team_code(q["source"], q["team"], codes, overrides)
        if fetched and fetched >= cutoff and team:
            by_team.setdefault(team, set()).add(normalize_name(q["player"]))
    injury = {sid: (players_by_id.get(sid) or {}).get("injury_status") for sid in starter_ids}
    known = {normalize_name(r[0]) for r in conn.execute("SELECT DISTINCT player FROM player_quotes")}
    unlikely = unlikely_to_play([s for s in starters if s[1] and s[1].club not in started], by_team,
                                {k: v for k, v in injury.items() if v}, known_names=known)
    reps = zero_minute_replacements(starters, bench, finished, started, minutes, cfg.slot_eligibility,
                                    upcoming_teams=upcoming)

    # Match-day goal odds for the report card, logged under the week being played.
    odds_rows = []
    for f in live_fx:
        k = parse_time(f.get("kickoff_time"))
        if not k or k <= now:
            continue
        for pl in ctx.values():
            if pl.club in (teams.get(f["team_h"]), teams.get(f["team_a"])):
                p = goal_quotes.get((normalize_name(pl.name), pl.club, f["fixture_id"]))
                if p is not None:
                    odds_rows.append((pl.sleeper_id, f.get("kickoff_time"), p))
    if odds_rows:
        log_goal_odds(conn, sleeper_season, gw, odds_rows, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return {
        "gameweek": gw,
        "starters": [{"slot": s, "player": _player_dict(pl) if pl else None} for s, pl in starters],
        "unlikely": unlikely,
        "replacements": [{"starter": _player_dict(x.starter), "slot": x.slot,
                          "bench": _player_dict(x.bench) if x.bench else None, "reason": x.reason} for x in reps],
        "fixtures": [_match_entry(f, teams, fodds.get(f["fixture_id"]), False)
                     for f in sorted(live_fx, key=lambda f: f.get("kickoff_time") or "")],
    }


# ---------------------------------------------------------------- html

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d1f23;--muted:#667;--line:#e3e5e8;--good:#2e9e5b;--mid:#888;--bad:#c0392b;--warn:#b35c00;--warnbg:#fff4e5}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1f2226;--fg:#e8e9eb;--muted:#9aa0a8;--line:#30343a;--warnbg:#3a2a12;--warn:#ffb35c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 8px}
.muted{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:600;cursor:pointer;white-space:nowrap;padding:4px 6px;border-bottom:1px solid var(--line)}
td{padding:6px;border-bottom:1px solid var(--line);white-space:nowrap}td.num,th.num{text-align:right}
small{color:var(--muted)}
.fx{display:inline-block;color:#fff;padding:1px 6px;border-radius:4px;font-size:12px;margin-right:3px}
.d1,.d2{background:var(--good)}.d3{background:var(--mid)}.d4,.d5{background:var(--bad)}
.flag{font-size:12px;color:var(--warn)}
.tip{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 10px;margin:6px 0}
.banner{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 10px}
"""

SORT_JS = """
document.querySelectorAll('table.sortable th').forEach(function(th,i){th.addEventListener('click',function(){
var tb=th.closest('table').tBodies[0],rows=Array.from(tb.rows),asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
rows.sort(function(a,b){var x=a.cells[i].dataset.v||a.cells[i].textContent,y=b.cells[i].dataset.v||b.cells[i].textContent;
var nx=parseFloat(x),ny=parseFloat(y);var c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y);return asc?c:-c;});
rows.forEach(function(r){tb.appendChild(r);});});});
"""


def _e(s):
    return html.escape("" if s is None else str(s))


def _fixtures_html(p):
    if not p["fixtures"]:
        return '<small>no match</small>'
    parts = []
    for f in p["fixtures"]:
        code = f["opponent"].upper() if f["home"] else f["opponent"].lower()
        parts.append(f'<span class="fx d{int(f["difficulty"])}">{_e(code)}</span>')
    return "".join(parts)


def _num(v):
    return "–" if v is None else f"{v:.1f}"


def _player_cells(p):
    flag = f' <span class="flag">{_e(p["flag"])}</span>' if p["flag"] else ""
    if p.get("undroppable"):
        flag += ' <span title="undroppable">🔒</span>'
    if p.get("market_flags"):
        why = "; ".join(_FLAG_TEXT[f](p) for f in p["market_flags"])
        flag += f' <span class="flag" title="{_e(why)}">⚑ {_e(why)}</span>'
    pos = "/".join(p["positions"])
    mkt = ' <small title="projection uses betting-market odds">mkt</small>' if p.get("proj_source") == "market" else ""
    return (f'<td>{_e(p["name"])} <small>{_e(pos)} {_e(p["club"])}</small>{flag}</td>'
            f'<td>{_fixtures_html(p)}</td>'
            f'<td class="num" data-v="{p["avg"] if p["avg"] is not None else -1}">{_num(p["avg"])} <small>({p["n"]})</small></td>'
            f'<td class="num" data-v="{p["proj"]}" title="difficulty-only projection: {_num(p.get("proj_v1"))}'
            f'{_e(_minutes_hint(p))}">'
            f'<b>{_num(p["proj"])}</b>{mkt}</td>')


def _minutes_hint(p):
    """Hover text for the minutes estimate, e.g. " · starts: 85% · plays: 92% · FPL: d75 (estimate)"."""
    if p.get("p_start") is None:
        return ""
    parts = [f"starts: {round(100 * p['p_start'])}%", f"plays: {round(100 * p['p_app'])}%"]
    if p.get("fpl_bucket") and p["fpl_bucket"] != "a100":
        parts.append(f"FPL: {p['fpl_bucket']}")
    if p.get("slp_bucket") and p["slp_bucket"] != "none":
        parts.append(f"Sleeper: {p['slp_bucket']}")
    return " · " + " · ".join(parts) + " (estimate)"


def _pct(x):
    return "–" if x is None else f"{round(100 * x)}%"


_FLAG_TEXT = {
    "cs": lambda p: "clean-sheet history and market disagree",
    "goal": lambda p: f"market {_pct(p.get('p_goal_market'))} to score vs {_pct(p.get('p_goal_hist'))} historically",
}


def _rank(v):
    return "–" if v is None else f"{v:.2f}"


def _report_card(r):
    rc = r.get("report_card") or {}
    decisions = {d["gw"]: d for d in rc.get("decisions", [])}
    show_xi = bool(decisions)
    show_min = any(w.get("n_v1m") for w in rc.get("weeks", []))
    rows = []
    for w in rc.get("weeks", []):
        d = decisions.get(w["gw"])
        minutes = ""
        if show_min:
            minutes = ("<td class=\"num\">–</td><td class=\"num\">–</td>" if not w.get("n_v1m") else
                       f'<td class="num">{_num(w["mae_v1m"])} / {_num(w["mae_v1_v1m"])} · {_rank(w["rank_v1m"])} / '
                       f'{_rank(w["rank_v1_v1m"])} <small>({w["n_v1m"]})</small></td>'
                       f'<td class="num">{"–" if w.get("brier_start") is None else format(w["brier_start"], ".3f")}'
                       f'</td>')
        xi = "".join(f'<td class="num">{_num(d[k]) if d else "–"}</td>' for k in ("set_pts", "tool_pts", "best_pts"))
        # v2 next to v1 on the same players only, never v1 over everyone
        v2 = ("–" if w["mae_v2"] is None else
              f'{_num(w["mae_v2"])} / {_num(w.get("mae_v1_paired"))} <small>({w.get("n_v2", 0)})</small>')
        rows.append(
            f'<tr><td>GW{w["gw"]}</td><td class="num">{w["n"]}</td><td class="num">{_num(w["mae_v1"])}</td>'
            f'<td class="num">{_num(w.get("mae_v1_played"))}</td><td class="num">{v2}</td>'
            f'<td class="num">{_rank(w.get("rank_v1"))}</td>'
            f'<td class="num">{"–" if w["brier_goal"] is None else format(w["brier_goal"], ".3f")} '
            f'<small>({w["n_goal"]})</small></td>{minutes}{xi if show_xi else ""}</tr>')
    head = ('<tr><th>Week</th><th class="num">Players</th><th class="num">Avg miss (all)</th>'
            '<th class="num">Avg miss (played)</th><th class="num">Avg miss v2 / v1 (same players)</th><th class="num">Order (v1)</th>'
            '<th class="num">Goal-odds score</th>'
            + ('<th class="num">With minutes (v1m / v1): miss · order</th><th class="num">Start-odds score</th>'
               if show_min else "")
            + ('<th class="num">Your XI</th><th class="num">Tool XI</th><th class="num">Best XI</th>' if show_xi else "")
            + '</tr>')
    table = f'<div class="scroll"><table><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>' if rows else ""
    matches = rc.get("matches") or []
    mtable = ""
    if matches:
        mrows = "".join(f'<tr><td>GW{m["gw"]}</td><td class="num">{m["n"]}</td>'
                        f'<td class="num">{m["log_loss"]:.3f}</td><td class="num">{m["rps"]:.3f}</td>'
                        f'<td class="num">{m["brier_cs"]:.3f}</td></tr>' for m in matches)
        mtable = ('<h2 style="margin-top:12px">Match odds</h2><div class="scroll"><table><thead><tr><th>Week</th>'
                  '<th class="num">Matches</th><th class="num">Log loss</th><th class="num">RPS</th>'
                  f'<th class="num">Clean-sheet score</th></tr></thead><tbody>{mrows}</tbody></table></div>')
    labels = rc.get("labels") or []
    ltable = ""
    if labels:
        lrows = "".join(
            f'<tr><td>{_e(_label_name(x))}</td><td class="num">{x["n"]}</td><td class="num">{_pct(x["start_rate"])}</td>'
            f'<td class="num">{_pct(x["app_rate"])}</td><td class="num">{round(100 * x["app_lo"])}–'
            f'{round(100 * x["app_hi"])}%</td><td class="num">{_pct(x["sixty_rate"])}</td></tr>' for x in labels)
        ltable = ('<h2 style="margin-top:12px">Labels: how often flagged players played</h2><div class="scroll"><table>'
                  '<thead><tr><th>Label</th><th class="num">Players</th><th class="num">Started</th>'
                  '<th class="num">Played</th><th class="num">Played range</th><th class="num">60+</th></tr></thead>'
                  f'<tbody>{lrows}</tbody></table></div>'
                  '<div class="muted">Ranges are 90% intervals. Few players means a wide range.</div>')
    notes = ['Avg miss = average points the projection was off by (lower is better); "all" counts players who '
             "didn't play as 0 points.",
             "Order = how well the projection ranked players within each position, from −1 to 1 (higher is better).",
             "Goal-odds score = how well market goal odds predicted scorers (Brier; lower is better)."]
    if show_xi:
        notes.append("Your XI / Tool XI / Best XI = real points of the lineup you set, the lineup this page suggested, "
                     "and the best lineup possible from your roster.")
    if matches:
        notes.append("Log loss, RPS and clean-sheet score grade the match odds against results (lower is better).")
    if show_min:
        notes.append("With minutes = the projection using expected minutes, beside today's on the same players. "
                     "Start-odds score = how well the chance of starting predicted who started (Brier; lower is "
                     "better).")
    return (f'<div class="card"><h2>Report card</h2>{table}{mtable}{ltable}<div class="muted" style="margin-top:6px">'
            f'{_e(rc.get("summary", "not enough weeks yet"))}. {_e(" ".join(notes))}</div></div>')


_LABEL_NAMES = {"a100+out": "Sleeper says Out, FPL says available", "a100+gtd": "Sleeper says GTD, FPL says available",
                "a100+sus": "Sleeper says suspended, FPL says available", "flagged+out": "Sleeper says Out, FPL flagged"}


def _label_name(x):
    if x["kind"] == "sleeper":
        return _LABEL_NAMES.get(x["cell"], f"Sleeper {x['cell']}")
    cell = x["cell"]
    if cell == "a100":
        return "FPL: available"
    if cell in ("s", "u", "n"):
        return {"s": "FPL: suspended", "u": "FPL: unavailable", "n": "FPL: not in squad"}[cell]
    return f"FPL: {'injured' if cell[0] == 'i' else 'doubtful'} {cell[1:]}%"


def _live_card(r):
    live = r.get("live")
    if not live:
        return ""
    items = []
    for x in live["replacements"]:
        if x["bench"]:
            items.append(f'<div class="tip">{_e(x["starter"]["name"])} got 0 minutes: bring in '
                         f'{_e(x["bench"]["name"])} at {_e(x["slot"])} before his match.</div>')
        else:
            items.append(f'<div class="tip">{_e(x["starter"]["name"])} got 0 minutes, but {_e(x["reason"])}.</div>')
    for s in live["starters"]:
        p = s["player"]
        if p and p["sleeper_id"] in live["unlikely"]:
            items.append(f'<div class="tip">{_e(p["name"])} may not play: {_e(live["unlikely"][p["sleeper_id"]])}.</div>')
    if not items:
        items.append('<div class="muted">No alerts for your starters.</div>')
    return f'<div class="card"><h2>This week (live) — GW{_e(live["gameweek"])}</h2>{"".join(items)}</div>'


def _matches_card(r):
    rows = []
    for m in r.get("matches", []):
        teams = f"{_e(m['home'])} v {_e(m['away'])}"
        when = _e((m.get("kickoff") or "")[:16].replace("T", " "))
        if m.get("market") is None:
            rows.append(f'<tr><td>{teams}</td><td colspan="4" class="muted">no market — using FPL difficulty</td>'
                        f'<td>{when}</td></tr>')
            continue
        age = " · <span class=\"flag\">stale</span>" if m["stale"] else ""
        rows.append(f'<tr><td>{teams}</td><td class="num">{_pct(m["p_home"])}/{_pct(m["p_draw"])}/{_pct(m["p_away"])}</td>'
                    f'<td class="num">{m["lam_home"]:.2f}–{m["lam_away"]:.2f}</td>'
                    f'<td class="num">{_pct(m["cs_home"])}/{_pct(m["cs_away"])}</td>'
                    f'<td><small>{_e("+".join(m["sources"]))} {_e(m["fetched_at"][5:16].replace("T", " "))}</small>{age}</td>'
                    f'<td>{when}</td></tr>')
    if not rows:
        return ""
    note = {"market": "Projections use betting-market odds.",
            "mixed": "Projections use betting odds where a market exists, FPL difficulty elsewhere.",
            "difficulty": "No usable betting odds: projections use FPL difficulty."}.get(r.get("projection_note"), "")
    head = ('<tr><th>Match</th><th class="num">Win/Draw/Win</th><th class="num">Exp. goals</th>'
            '<th class="num">Clean sheet</th><th>Source</th><th>Kickoff (UTC)</th></tr>')
    return (f'<div class="card"><h2>Matches</h2><div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div><div class="muted" style="margin-top:6px">{_e(note)}</div></div>')


HEAD = '<tr><th>Player</th><th>vs</th><th class="num">Avg5</th><th class="num">Proj</th>'


def _table(rows_html, extra_head=""):
    return (f'<div class="scroll"><table class="sortable"><thead>{HEAD}{extra_head}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table></div>')


def render_html(report):
    r = report
    src = r["sources"]
    asof = " · ".join(f"{_e(k.replace('_', ' '))}: {_e(v or 'never')}" for k, v in src.items())
    parts = [f'<h1>GW{_e(r["gameweek"] or "–")} · Best XI <span class="muted">projected {_num(r["xi"]["total"])}</span></h1>',
             f'<div class="muted">Generated {_e(r["generated_at"])} · first kickoff {_e(r["first_kickoff"] or "–")} · '
             f'pickups: {"waiver claims" if r["mode"] == "waiver" else "instant free agents"}</div>',
             f'<div class="muted">Points: ' + ("Sleeper stats (exact league scoring)" if r.get("stats_source") == "sleeper"
                                                else "FPL stats via scoring_map.json (approximate)") + '</div>',
             f'<div class="muted">Data as of — {asof}</div>']
    ip = r.get("in_progress")
    if ip and ip["matches_left"]:
        left = ip["matches_left"]
        parts.append(f'<div class="card banner">GW{_e(ip["gameweek"])} is still being played ({left} '
                     f'match{"es" if left != 1 else ""} left). Its points, minutes and injury news aren\'t in these '
                     f'numbers yet; rebuild after it finishes before you act on the tips.</div>')
    if r.get("undroppable_unmatched"):
        parts.append(f'<div class="card banner">undroppable in config.json matches no one on your roster: '
                     f'{_e(", ".join(r["undroppable_unmatched"]))}</div>')

    if r["coverage"]["missing"]:
        parts.append(f'<div class="card banner">Approximate: {len(r["coverage"]["missing"])} scoring rule(s) have no FPL '
                     f'stat and are left out: {_e(", ".join(r["coverage"]["missing"]))}. Edit scoring_map.json to map them.</div>')

    parts.append(_live_card(r))
    parts.append(_matches_card(r))
    if r["coverage"].get("changed"):
        parts.append(f'<div class="card banner">League points changed for: {_e(", ".join(r["coverage"]["changed"]))}. '
                     f'Update those rules in scoring_map.json.</div>')

    rows = []
    for s in r["xi"]["starters"]:
        if s["player"]:
            rows.append(f'<tr>{_player_cells(s["player"])}<td>{_e(s["slot"])}</td></tr>')
        else:
            rows.append(f'<tr><td colspan="4" class="flag">No eligible player</td><td>{_e(s["slot"])}</td></tr>')
    xi = [f'<div class="card"><h2>Best XI</h2>{_table(rows, "<th>Slot</th>")}']
    if r["xi"]["bench"]:
        xi.append(f'<h2 style="margin-top:12px">Bench</h2>{_table([f"<tr>{_player_cells(p)}</tr>" for p in r["xi"]["bench"]])}')
    xi.append('<div class="muted" style="margin-top:6px">Opponent in CAPS = home, lower case = away. '
              'Green = easy, grey = average, red = hard. Avg5 (n) = average league points over the last n matches played.</div></div>')
    parts.append("".join(xi))

    tips = []
    for s in r["swaps"]:
        prefix = "Waiver claim: " if r["mode"] == "waiver" else ""
        what = "projected" if s["kind"] == "xi" else "bench upgrade"
        tips.append(f'<div class="tip">{prefix}Drop {_e(s["drop"]["name"])}, add {_e(s["add"]["name"])}: '
                    f'+{_num(s["gain"])} {what}</div>')
    parts.append(f'<div class="card"><h2>Swap tips</h2>{"".join(tips) or "<div class=muted>No swap clears the threshold this week.</div>"}</div>')

    fa = ['<div class="card"><h2>Top free agents</h2>']
    for pos, group in r["free_agents"].items():
        rows = [f'<tr>{_player_cells(p)}<td class="num" data-v="{p["trending_adds"] or 0}">'
                f'{"▲ " + str(p["trending_adds"]) if p["trending_adds"] else "–"}</td></tr>' for p in group]
        fa.append(f'<h2 style="margin-top:10px">{_e(pos)}</h2>{_table(rows, "<th class=num>Trend</th>")}')
    fa.append("</div>")
    parts.append("".join(fa))

    parts.append(_report_card(r))
    um = r["unmatched_rostered"]
    um_list = "".join(f"<li>{_e(p['name'])} <small>{_e(p['club'])}</small></li>" for p in um)
    parts.append(f'<div class="card"><h2>Unmatched</h2><div class="muted">{r["unmatched_count"]} Sleeper players have no FPL match '
                 f'and no projection. Fix them in overrides.json.</div>'
                 + (f'<p>On your roster:</p><ul>{um_list}</ul>' if um else "")
                 + (f'<p>Betting markets we couldn\'t match to a fixture (add to "market_teams" in overrides.json):</p>'
                    f'<ul>{"".join(f"<li>{_e(u)}</li>" for u in r.get("markets_unmatched", []))}</ul>'
                    if r.get("markets_unmatched") else "") + "</div>")

    body = "\n".join(parts)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>GW{_e(r["gameweek"] or "")} lineup</title><style>{CSS}</style></head>'
            f'<body><main>{body}</main><script>{SORT_JS}</script></body></html>')


def _atomic_write(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_outputs(report, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_dir / "report.json", json.dumps(report, indent=2))
    _atomic_write(output_dir / "index.html", render_html(report))
