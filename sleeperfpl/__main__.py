"""python3 -m sleeperfpl [--config PATH] <command>"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .backtest import format_backtest, run_backtest
from .config import ConfigError, load_config
from .db import connect, latest
from .fetch_fpl import fetch_bootstrap, fetch_fixtures, fetch_history
from .fetch_markets import fetch_kalshi, fetch_polymarket
from .gameweek import live_gameweek, matchday_due
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


COMMANDS = {
    "fetch-daily": (cmd_fetch_daily, "league, players, FPL bootstrap + fixtures"),
    "fetch-rosters": (cmd_fetch_rosters, "rosters + trending adds"),
    "fetch-history": (cmd_fetch_history, "Sleeper weekly stats + FPL match history (after a game week finishes)"),
    "coverage": (cmd_coverage, "how much of the league's scoring FPL covers"),
    "fetch-markets": (cmd_fetch_markets, "Kalshi + Polymarket match odds"),
    "matchday": (cmd_matchday, "before kickoffs / after matches: odds, player markets, live stats, rebuild"),
    "build": (cmd_build, "write report.json and index.html"),
    "backtest": (cmd_backtest, "replay past weeks to compare projection methods"),
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
