"""Backtest: replay finished game weeks to compare projection methods.

For week g, a player's inputs come only from his Sleeper stats in earlier
weeks of the same season; nothing from week g or later. Seasons are replayed
separately and never pooled. Methods live in METHODS, so the next projection
idea is one entry there, judged against "v1" (what the page uses today).

"v1m" needs the minutes model, refitted for every week on earlier weeks only
(this season's and earlier seasons'). A method may return None when it can't
project a week; those rows are left out of its scores.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from .db import latest
from .gameweek import team_fixtures
from .matching import load_overrides, match_players
from .metrics import (Scored, brier, category, error_summary, mean, mean_ci, paired_t, position_group,
                      rank_by_position, reliability, signflip_p)
from .minutes import (FixtureMinutes, fixture_minutes, history, last_season_start_share, player_minutes,
                      regular_weeks)
from .minutes_fit import all_rows, earlier, fit_rows, load_default, pos_of_players
from .projection import PositionRates, minutes_rates, project, project_minutes
from .projection_v2 import SLEEPER_PREFIX
from .scoring_map import flatten_scoring_settings
from .sleeper_stats import irregular_week, sleeper_points

NEUTRAL = 0           # difficulty meaning "no fixture data": v1 uses multiplier 1.0
SAMPLE_WEEKS = 5      # scored only if he played in one of the 5 weeks before
MIN_FIT_ROWS = 1500    # fewer training rows than this: the shipped default fit, if it's from an earlier season
POPULATION_WINDOW = 10  # minutes scores cover players with a start in their last 10 weeks
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
    minutes: list | None = None   # one FixtureMinutes per week-g fixture; None without a minutes model
    prior: object = None          # projection.Prior for his position from earlier weeks; None = no shrinkage


def _v1(inp, cfg):
    multipliers = {**cfg.difficulty_multipliers, NEUTRAL: 1.0}
    return project(inp.history, inp.difficulties, False, multipliers, cfg.avg_window).proj


def _season_avg(inp, cfg):
    avg = mean(inp.history)
    return avg * len(inp.difficulties) if avg is not None else 0.0


def _mults(inp, cfg):
    multipliers = {**cfg.difficulty_multipliers, NEUTRAL: 1.0}
    return [multipliers[d] for d in inp.difficulties]


def _rates(inp, cfg):
    k = cfg.minutes_shrink_k if inp.prior else 0.0
    return minutes_rates([s for _, s in inp.weeks if _played(s)][:cfg.avg_window], inp.scoring,
                         inp.prior.lin90 if inp.prior else 0.0, k)


def _v1m(inp, cfg):
    if inp.minutes is None:
        return None
    lin90, cs60 = _rates(inp, cfg)
    return project_minutes(lin90, cs60, inp.minutes, _mults(inp, cfg))


METHODS: dict[str, Callable[[BacktestInput, object], float | None]] = {
    "v1": _v1, "season_avg": _season_avg, "v1m": _v1m}


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


def _week_model(rows, short, season, gw, prev, default):
    """(model, source) for replaying (season, gw): a fit on earlier weeks, else an earlier season's default."""
    model = fit_rows([r for r in rows if earlier(r, (season, gw))],
                     [r for r in short if earlier(r, (season, gw))], w0=prev.weights if prev else None)
    if model is not None and model.n_rows >= MIN_FIT_ROWS:
        return model, "fit"
    if default is not None and all(s < season for s in default.trained_on):
        return default, "default"
    return None, None


def _clip(p):
    return min(0.95, max(0.05, p))


def _minutes_scores(entries):
    """Brier for start / appear / 60+ for the model and two baselines, plus reliability for P(start)."""
    out = {"n": len(entries), "brier": {}}
    for name in ("model", "b1", "b2"):
        out["brier"][name] = {t: mean(brier(e[name][i], e["y"][i]) for e in entries) if entries else None
                              for i, t in enumerate(("start", "app", "sixty"))}
    out["reliability"] = reliability([(e["model"][0], e["y"][0]) for e in entries])
    return out


def _paired(weeks, key):
    d = [w["methods"]["v1m"][key] - w["methods"]["v1"][key] for w in weeks
         if w["methods"]["v1m"][key] is not None and w["methods"]["v1"][key] is not None]
    t, df = paired_t(d)
    return {"n": len(d), "mean": mean(d), "ci": mean_ci(d), "t": t, "df": df, "p_signflip": signflip_p(d)}


def run_backtest(conn, cfg, seasons=None, minutes=False):
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

    stored = [r[0] for r in conn.execute("SELECT DISTINCT season FROM sleeper_stats ORDER BY season")]
    by_season = {s: {} for s in stored}
    for s, week, pid, raw in conn.execute("SELECT season, week, player_id, stats_json FROM sleeper_stats"):
        by_season[s].setdefault(week, {})[pid] = json.loads(raw)
    train_pos = pos_of_players(conn, by_season)
    default = load_default()
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

        weeks, minute_entries, oracle = [], [], []
        regular = regular_weeks(stats)
        prev_rows = by_season.get(str(int(season) - 1), {}) if season.isdigit() else {}
        train, train_short = all_rows(conn, train_pos, [s for s in stored if s <= season])
        prev_fit = None
        rates, pooled = PositionRates(scoring), 0
        for gw in range(2, last + 1):
            for w in range(pooled + 1, gw):
                rates.add(stats.get(w, {}))
            pooled = gw - 1
            rows = stats.get(gw)
            if not rows:
                continue
            if not is_current and irregular_week(rows):
                irregular.append(gw)
                continue
            model, source = _week_model(train, train_short, season, gw, prev_fit, default)
            if source == "fit":
                prev_fit = model   # warm start for next week's fit
            by_club = fixtures_for(gw) if fixtures_for else {}
            recent = range(max(1, gw - SAMPLE_WEEKS), gw)
            sample = sorted({pid for w in recent for pid, s in stats.get(w, {}).items()
                             if not pid.startswith("TEAM_") and pos_of.get(pid) and _played(s)})
            scored = {name: [] for name in METHODS}
            scored_logged = {name: [] for name in METHODS}
            week_oracle = []
            for pid in sample:
                prior = [(w, stats[w][pid]) for w in range(gw - 1, 0, -1) if pid in stats.get(w, {})]
                c = club.get(pid)
                difficulties = [f.difficulty for f in by_club.get(c, [])] if c else [NEUTRAL]
                inp = BacktestInput(pid, pos_of[pid], prior,
                                    [sleeper_points(s, scoring) for _, s in prior if _played(s)],
                                    difficulties, scoring, season, gw,
                                    prior=rates.prior(SLEEPER_PREFIX.get(pos_of[pid], "")))
                actual_stats = rows.get(pid)
                pts = sleeper_points(actual_stats, scoring) if actual_stats else 0.0
                cat = category(actual_stats, pts, cfg.category_low, cfg.category_haul)
                hist = []
                if model is not None:
                    hist = history(stats, pid, gw, regular)
                    share = last_season_start_share(prev_rows, pid) if len(hist) < 3 else None
                    pm = player_minutes(hist, inp.position, model, share)
                    fm = fixture_minutes(pm.p_start, pm.p_app, pm.start_buckets, pm.sub_buckets)
                    inp.minutes = [fm] * len(difficulties)
                for name, method in METHODS.items():
                    proj = method(inp, cfg)
                    if proj is None:
                        continue
                    row = Scored(inp.position, proj, pts, cat)
                    scored[name].append(row)
                    if pid in logged.get(gw, ()):
                        scored_logged[name].append(row)
                if model is None:
                    continue
                st = actual_stats or {}
                minutes_played = float(st.get("min") or 0)
                if len(difficulties) == 1:
                    certain = FixtureMinutes([(minutes_played, 1.0)], 0.0, 0.0, minutes_played)
                    lin90, cs60 = _rates(inp, cfg)
                    week_oracle.append((abs(_v1m(inp, cfg) - pts),
                                        abs(project_minutes(lin90, cs60, [certain], _mults(inp, cfg)) - pts)))
                recent = hist[-POPULATION_WINDOW:]
                if minutes and gw in regular and any(h.started for h in recent):
                    last5 = hist[-5:]
                    y = ((st.get("gs") or 0) >= 1, minutes_played > 0, minutes_played >= 60)
                    minute_entries.append({
                        "y": y, "model": (pm.p_start, pm.p_app, fm.p60),
                        "b1": tuple(_clip(float(v)) for v in (hist[-1].started, hist[-1].minutes > 0,
                                                              hist[-1].minutes >= 60)),
                        "b2": tuple((sum(1 for h in last5 if test(h)) + 0.5) / 6 for test in (
                            lambda h: h.started, lambda h: h.minutes > 0, lambda h: h.minutes >= 60))})
            oracle.extend(week_oracle)
            entry = {"gw": gw, "n": len(sample), "methods": {name: _score(r) for name, r in scored.items()},
                     "model": source,
                     "oracle_mae": mean(o for _, o in week_oracle)}
            if any(scored_logged.values()):
                entry["logged"] = {name: _score(r) for name, r in scored_logged.items()}
            weeks.append(entry)
        out["seasons"][season] = {"weeks": weeks, "summary": _summary(weeks), "irregular_weeks": irregular,
                                  "notes": notes,
                                  "paired": {"mae": _paired(weeks, "mae"), "rank": _paired(weeks, "rank")},
                                  "oracle": {"n": len(oracle), "mae_v1m": mean(a for a, _ in oracle),
                                             "mae_oracle": mean(o for _, o in oracle)}}
        if minutes:
            out["seasons"][season]["minutes"] = _minutes_scores(minute_entries)
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


def _paired_text(p, fmt):
    if not p["n"]:
        return "–"
    ci = f"95% {format(p['ci'][0], fmt)} to {format(p['ci'][1], fmt)}, " if p["ci"] else ""
    return f"{format(p['mean'], '+' + fmt)} ({ci}p {_f(p['p_signflip'], '.2f')}, {p['n']} weeks)"


def _minutes_lines(s):
    lines = [f"  v1m minus v1, per week: miss {_paired_text(s['paired']['mae'], '.2f')}; "
             f"order {_paired_text(s['paired']['rank'], '.2f')}"]
    o = s["oracle"]
    if o["n"]:
        lines.append(f"  minutes error share: average miss v1m {o['mae_v1m']:.2f}, "
                     f"with actual minutes {o['mae_oracle']:.2f} ({o['n']} player-weeks)")
    m = s.get("minutes")
    if m:
        lines.append(f"  start-odds score (Brier, lower is better; {m['n']} player-weeks):")
        lines.append(f"    {'':12}{'start':>8}{'plays':>8}{'60+':>8}")
        for name, label in (("model", "model"), ("b1", "last week"), ("b2", "last 5")):
            b = m["brier"][name]
            lines.append(f"    {label:12}" + "".join(f"{_f(b[t], '.3f'):>8}" for t in ("start", "app", "sixty")))
        lines.append("    reliability (predicted → observed start rate, n): " + ", ".join(
            f"{r['mean_p']:.2f}→{r['observed']:.2f} ({r['n']})" for r in m["reliability"]))
    return lines


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
        lines.extend(_minutes_lines(s))
        for note in s["notes"]:
            lines.append(f"  note: {note}")
        if s["irregular_weeks"]:
            lines.append(f"  skipped (blank or double weeks): {', '.join(f'GW{w}' for w in s['irregular_weeks'])}")
    lines.append("Can't replay: " + "; ".join(result["cannot_replay"]))
    return "\n".join(lines)
