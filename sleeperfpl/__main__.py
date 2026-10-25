"""python3 -m sleeperfpl [--config PATH] <command>"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .availability import audit_rows, format_audit, rebuild_label_counts
from .backtest import format_backtest, run_backtest
from .config import ConfigError, load_config
from .db import connect, latest
from .fetch_fpl import fetch_bootstrap, fetch_fixtures, fetch_history
from .fetch_markets import fetch_kalshi, fetch_polymarket
from .gameweek import live_gameweek, matchday_due
from .minutes import regular_weeks, season_rows
from .minutes_fit import fit_model, holdout_brier, last_fit_key, pos_of_players, save_model, training_rows
from .fetch_sleeper import fetch_league, fetch_players, fetch_rosters, fetch_trending
from .http import FetchError
from .report import BuildError, build_report, write_outputs
from .sleeper_stats import fetch_sleeper_stats
from .scoring_map import (coverage, ensure_scoring_map, league_drift, load_scoring_map, probe_sleeper_stats,
                          rule_lines)


def _league(conn):
    rows = latest(conn, "sleeper_league")
    if not rows:
        raise BuildError("no league data yet; run fetch-daily first")
    return json.loads(rows[0]["data_json"])


def cmd_fetch_daily(cfg, conn, args):
    sport = fetch_league(conn, cfg.league_id)
    print(f"league: sport={sport}")
    print(f"sleeper players: {fetch_players(conn, sport)}")
    print(f"fpl players: {fetch_bootstrap(conn)}")
    print(f"fpl fixtures: {fetch_fixtures(conn)}")
    if ensure_scoring_map(cfg.scoring_map_path, _league(conn).get("scoring_settings") or {}):
        print(f"created {cfg.scoring_map_path}; review it, then run `coverage`")


def cmd_fetch_status(cfg, conn, args):
    # Injury flags and chances only: cheap enough to run around press conferences.
    print(f"fpl players: {fetch_bootstrap(conn)}")


def cmd_fetch_rosters(cfg, conn, args):
    print(f"rosters: {fetch_rosters(conn, cfg.league_id)}")
    print(f"trending: {fetch_trending(conn, _league(conn)['sport'])}")


PAST_SEASON_WEEKS = 38


def cmd_fetch_history(cfg, conn, args):
    league = _league(conn)
    season = getattr(args, "season", None)
    if season:
        # A past season, once, for the backtest: Sleeper stats only (FPL serves only the current season).
        if season == str(league.get("season", "")):
            raise BuildError("--season is for past seasons; run fetch-history without it")
        weeks = fetch_sleeper_stats(conn, league["sport"], season, PAST_SEASON_WEEKS, force=args.force,
                                    update_meta=False)
        print(f"sleeper stats {season}: {weeks} week(s) fetched")
        return
    finished = [r["event_id"] for r in latest(conn, "fpl_events") if r["finished"]]
    weeks = fetch_sleeper_stats(conn, league["sport"], str(league.get("season", "")),
                                max(finished) if finished else None, force=args.force)
    print(f"sleeper stats: {weeks} week(s) fetched")
    # FPL history stays as the backup source (stats_source "fpl", or no Sleeper stats).
    n = fetch_history(conn, force=args.force, interval=cfg.fpl_request_interval)
    print(f"fpl history: {n} players fetched" if n else "fpl history: up to date (use --force to refetch)")


def cmd_coverage(cfg, conn, args):
    rules = load_scoring_map(cfg.scoring_map_path)
    league = _league(conn)
    print(f"Rules in {cfg.scoring_map_path} (check each guess):")
    for line in rule_lines(rules):
        print(f"  {line}")
    covered, missing = coverage(rules)
    not_in_map, changed = league_drift(rules, league.get("scoring_settings") or {})
    print(f"covered by FPL ({len(covered)}): {', '.join(covered) or '-'}")
    print(f"NOT covered ({len(missing)}): {', '.join(missing) or '-'}")
    if not_in_map:
        print(f"league keys missing from the map (add them): {', '.join(not_in_map)}")
    if changed:
        print(f"league points differ from the map: {', '.join(changed)}")
    finished = [r["event_id"] for r in latest(conn, "fpl_events") if r["finished"]]
    week = max(finished) if finished else 1
    print("Sleeper per-match stats probe:")
    for line in probe_sleeper_stats(league["sport"], str(league.get("season", "")), week):
        print(f"  {line}")


def cmd_fetch_markets(cfg, conn, args):
    print(f"kalshi: {fetch_kalshi(conn)} quotes")
    print(f"polymarket: {fetch_polymarket(conn)} quotes")


def cmd_matchday(cfg, conn, args):
    now = datetime.now(timezone.utc)
    fixtures = [dict(r) for r in latest(conn, "fpl_fixtures")]
    if not args.force and not matchday_due(fixtures, now, cfg.matchday_window_hours):
        print(f"matchday: nothing kicks off within {cfg.matchday_window_hours:g} h or just finished; skipped")
        return
    league = _league(conn)

    def step(label, fn):
        # One failed source must not stop the rebuild (and its 0-minute alerts).
        try:
            print(f"{label}: {fn()}")
        except Exception as e:  # noqa: BLE001 - unattended cron: warn and carry on
            sys.stderr.write(f"warning: {label} failed: {e}\n")

    step("rosters", lambda: fetch_rosters(conn, cfg.league_id))
    step("fpl status", lambda: fetch_bootstrap(conn))
    step("fpl fixtures", lambda: fetch_fixtures(conn))
    step("kalshi (with players)", lambda: fetch_kalshi(conn, players=True))
    step("polymarket", lambda: fetch_polymarket(conn))
    events = [dict(r) for r in latest(conn, "fpl_events")]
    gw = live_gameweek(events, [dict(r) for r in latest(conn, "fpl_fixtures")], now)
    if gw:
        step(f"sleeper stats (live GW{gw})",
             lambda: fetch_sleeper_stats(conn, league["sport"], str(league.get("season", "")), gw))
    cmd_build(cfg, conn, args)


def cmd_build(cfg, conn, args):
    report = build_report(conn, cfg, datetime.now(timezone.utc))
    write_outputs(report, cfg.output_dir)
    print(f"wrote {cfg.output_dir / 'index.html'} (GW{report['gameweek']}, {len(report['swaps'])} swap tips)")


def cmd_backtest(cfg, conn, args):
    if conn.execute("SELECT 1 FROM sleeper_stats LIMIT 1").fetchone() is None:
        raise BuildError("no Sleeper stats yet; run fetch-history first")
    result = run_backtest(conn, cfg, args.season or None)
    print(format_backtest(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
        print(f"wrote {args.json}")


def cmd_label_audit(cfg, conn, args):
    season = str(_league(conn).get("season") or "")
    finished = [r["event_id"] for r in latest(conn, "fpl_events") if r["finished"]]
    rebuild_label_counts(conn, season, max(finished) if finished else None,
                         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    rows = audit_rows(conn, season)
    if not rows:
        print("no label history yet: it builds up as weeks finish after builds have logged labels")
        return
    print(format_audit(rows))


HOLDOUT_WEEKS = 4


def cmd_fit_minutes(cfg, conn, args):
    seasons = [r[0] for r in conn.execute("SELECT DISTINCT season FROM sleeper_stats ORDER BY season")]
    if not seasons:
        raise BuildError("no Sleeper stats yet; run fetch-history first")
    newest = seasons[-1]
    last_week = conn.execute("SELECT MAX(week) FROM sleeper_stats WHERE season = ?", (newest,)).fetchone()[0]
    key = f"{newest}:{last_week}"
    if not args.force and last_fit_key(conn) == key:
        print("fit-minutes: no new weeks since last fit")
        return
    pos_of = pos_of_players(conn, {s: season_rows(conn, s) for s in seasons})
    holdout = None
    weeks = sorted(regular_weeks(season_rows(conn, newest)))
    if len(weeks) > HOLDOUT_WEEKS:
        cut = weeks[-HOLDOUT_WEEKS]
        early = fit_model(conn, pos_of, seasons, before=(newest, cut))
        test = [r for r in training_rows(conn, pos_of, [newest]) if r.week >= cut]
        if early and test:
            holdout = holdout_brier(early, test)
    model = fit_model(conn, pos_of, seasons)
    if model is None:
        raise BuildError("not enough stored weeks to fit the minutes model")
    save_model(conn, model, key, holdout)
    shown = "–" if holdout is None else f"{holdout:.3f}"
    print(f"fit-minutes: {model.n_rows} rows from {', '.join(model.trained_on)}; "
          f"holdout Brier (start) {shown}; version {model.version}")
    if args.export:
        Path(args.export).write_text(json.dumps(model.to_dict(), indent=1))
        print(f"wrote {args.export}")


COMMANDS = {
    "fetch-daily": (cmd_fetch_daily, "league, players, FPL bootstrap + fixtures"),
    "fetch-rosters": (cmd_fetch_rosters, "rosters + trending adds"),
    "fetch-status": (cmd_fetch_status, "FPL bootstrap only: injury flags and chances"),
    "fetch-history": (cmd_fetch_history, "Sleeper weekly stats + FPL match history (after a game week finishes)"),
    "coverage": (cmd_coverage, "how much of the league's scoring FPL covers"),
    "fetch-markets": (cmd_fetch_markets, "Kalshi + Polymarket match odds"),
    "matchday": (cmd_matchday, "before kickoffs / after matches: odds, player markets, live stats, rebuild"),
    "build": (cmd_build, "write report.json and index.html"),
    "backtest": (cmd_backtest, "replay past weeks to compare projection methods"),
    "fit-minutes": (cmd_fit_minutes, "refit the start/minutes model from stored Sleeper weeks"),
    "label-audit": (cmd_label_audit, "how often players with each injury label actually played"),
}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sleeperfpl", description="Sleeper EPL rankings")
    ap.add_argument("--config", help="path to config.json (default: $SLEEPER_FPL_CONFIG or ./config.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, (_, help_text) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        if name == "matchday":
            p.add_argument("--force", action="store_true", help="run even if no match is near")
        if name == "backtest":
            p.add_argument("--season", action="append", help="season to replay (repeatable; default: all stored)")
            p.add_argument("--json", help="also write the full results to this file")
        if name == "fit-minutes":
            p.add_argument("--force", action="store_true", help="refit even if no new week is stored")
            p.add_argument("--export", help="also write the fitted model to this JSON file")
        if name == "fetch-history":
            p.add_argument("--force", action="store_true", help="refetch even if no new game week finished")
            p.add_argument("--season", help="fetch a past season's Sleeper stats (e.g. 2025), for backtest")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
        conn = connect(cfg.db_path)
        COMMANDS[args.cmd][0](cfg, conn, args)
    except (ConfigError, BuildError, FetchError) as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
