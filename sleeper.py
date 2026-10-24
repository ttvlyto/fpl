#!/usr/bin/env python3
"""
sleeper.py - map Sleeper player IDs to real players and export free agents.

Usage:
    python sleeper.py rosters      <league_id>
    python sleeper.py free-agents  <league_id> [-o free_agents.csv]
    python sleeper.py player       <league_id> 1011 2047 ...
    python sleeper.py refresh      <league_id>

The ~5MB /players payload is cached on disk for 24h, so only the first
run of the day hits that endpoint. Everything else is small and cheap.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.sleeper.app/v1"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "sleeper"
MAX_AGE_SECONDS = 24 * 60 * 60


# ---------------------------------------------------------------- http

def get(path):
    """GET a Sleeper endpoint and return parsed JSON."""
    url = f"{API}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "sleeper-cli/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {url}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach {url}: {e.reason}")


# ---------------------------------------------------------------- players cache

def load_players(sport, force=False):
    """Return the full {player_id: {...}} map, cached for 24h."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"players_{sport}.json"

    if cache.exists() and not force:
        age = time.time() - cache.stat().st_mtime
        if age < MAX_AGE_SECONDS:
            return json.loads(cache.read_text())

    sys.stderr.write(f"Downloading /players/{sport} (this one is large)...\n")
    data = get(f"players/{sport}")
    cache.write_text(json.dumps(data))
    sys.stderr.write(f"Cached {len(data)} players at {cache}\n")
    return data


# ---------------------------------------------------------------- helpers

def player_name(p):
    """Best-effort display name. Soccer entries vary in which fields are set."""
    if not p:
        return None
    full = p.get("full_name")
    if full:
        return full
    parts = [p.get("first_name"), p.get("last_name")]
    joined = " ".join(x for x in parts if x)
    return joined or p.get("last_name") or p.get("player_id")


def positions(p):
    fp = p.get("fantasy_positions") or []
    if fp:
        return "/".join(fp)
    return p.get("position") or ""


def league_sport(league_id):
    """Read the sport slug straight off the league so we never guess it."""
    league = get(f"league/{league_id}")
    if not league:
        sys.exit(f"No league found with id {league_id}")
    sport = league.get("sport")
    if not sport:
        sys.exit("League object had no 'sport' field; can't pick a /players endpoint.")
    return sport, league


def team_names(league_id):
    """owner_id -> team name (falls back to the user's display name)."""
    out = {}
    for u in get(f"league/{league_id}/users") or []:
        meta = u.get("metadata") or {}
        out[u["user_id"]] = meta.get("team_name") or u.get("display_name") or u["user_id"]
    return out


def rostered_ids(rosters):
    """Union of every roster's players list (already covers reserve + taxi)."""
    ids = set()
    for r in rosters:
        for pid in (r.get("players") or []):
            ids.add(pid)
    return ids


# ---------------------------------------------------------------- commands

def cmd_rosters(args):
    sport, _ = league_sport(args.league_id)
    players = load_players(sport, force=args.refresh)
    rosters = get(f"league/{args.league_id}/rosters") or []
    names = team_names(args.league_id)

    for r in sorted(rosters, key=lambda x: x.get("roster_id", 0)):
        owner = names.get(r.get("owner_id"), "(unclaimed)")
        s = r.get("settings") or {}
        record = f"{s.get('wins', 0)}-{s.get('losses', 0)}-{s.get('ties', 0)}"
        print(f"\n=== roster {r.get('roster_id')} | {owner} | {record} ===")

        starters = r.get("starters") or []
        bench = [p for p in (r.get("players") or []) if p not in starters]
        reserve = set(r.get("reserve") or [])

        for label, group in (("XI", starters), ("BENCH", bench)):
            for pid in group:
                p = players.get(pid)
                tag = " [IR]" if pid in reserve else ""
                club = (p or {}).get("team") or "FA"
                print(f"  {label:<5} {pid:<7} {player_name(p) or '??':<28} "
                      f"{positions(p or {}):<8} {club}{tag}")


def cmd_player(args):
    sport, _ = league_sport(args.league_id)
    players = load_players(sport, force=args.refresh)
    for pid in args.player_ids:
        p = players.get(pid)
        if not p:
            print(f"{pid}: not found in /players/{sport}")
            continue
        print(f"{pid}: {player_name(p)} | {positions(p)} | {p.get('team') or 'FA'} "
              f"| status={p.get('status')} | rank={p.get('search_rank')}")


def cmd_free_agents(args):
    sport, _ = league_sport(args.league_id)
    players = load_players(sport, force=args.refresh)
    rosters = get(f"league/{args.league_id}/rosters") or []
    taken = rostered_ids(rosters)

    rows = []
    for pid, p in players.items():
        if pid in taken:
            continue
        if args.active_only and not p.get("active"):
            continue
        if args.clubbed_only and not p.get("team"):
            continue
        rows.append({
            "player_id": pid,
            "name": player_name(p),
            "position": positions(p),
            "club": p.get("team") or "",
            "status": p.get("status") or "",
            "injury_status": p.get("injury_status") or "",
            "age": p.get("age") or "",
            "search_rank": p.get("search_rank") if p.get("search_rank") is not None else 9999999,
        })

    rows.sort(key=lambda r: r["search_rank"])

    fields = ["player_id", "name", "position", "club", "status",
              "injury_status", "age", "search_rank"]

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} free agents -> {args.out} "
              f"({len(taken)} players rostered across {len(rosters)} teams)")
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def cmd_refresh(args):
    sport, _ = league_sport(args.league_id)
    load_players(sport, force=True)


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description="Sleeper roster / free-agent helper")
    ap.add_argument("--refresh", action="store_true",
                    help="force re-download of the player map")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rosters", help="print every team with real player names")
    p.add_argument("league_id")
    p.set_defaults(func=cmd_rosters)

    p = sub.add_parser("free-agents", help="everyone not on a roster")
    p.add_argument("league_id")
    p.add_argument("-o", "--out", help="write CSV here instead of stdout")
    p.add_argument("--all", dest="active_only", action="store_false", default=True,
                   help="include inactive players")
    p.add_argument("--include-clubless", dest="clubbed_only", action="store_false",
                   default=True, help="include players with no club")
    p.set_defaults(func=cmd_free_agents)

    p = sub.add_parser("player", help="look up one or more player ids")
    p.add_argument("league_id")
    p.add_argument("player_ids", nargs="+")
    p.set_defaults(func=cmd_player)

    p = sub.add_parser("refresh", help="re-download the player map")
    p.add_argument("league_id")
    p.set_defaults(func=cmd_refresh)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
