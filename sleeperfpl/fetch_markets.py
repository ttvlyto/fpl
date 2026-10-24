"""Kalshi and Polymarket fetchers: match and player market quote snapshots.

Team codes are stored as the market lists them; mapping to FPL codes happens
when quotes are priced, so a fixed alias never requires a refetch.
Both fetchers are non-fatal: a failure warns and writes nothing.
"""

import json
import re
import sys
from datetime import timedelta

from .db import now_iso
from .gameweek import parse_time
from .http import FetchError, get_json

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY = "https://gamma-api.polymarket.com"
POLY_EPL_SERIES = "10188"
KALSHI_MATCH_SERIES = {"KXEPLGAME": "1x2", "KXEPLTOTAL": "total", "KXEPLTEAMTOTAL": "team_total", "KXEPLBTTS": "btts"}
KALSHI_PLAYER_SERIES = ("KXEPLGOAL", "KXEPLSOA")
# Kalshi gives an expected settlement time, not kickoff; matches settle ~2 h after kickoff.
KALSHI_KICKOFF_OFFSET = timedelta(hours=2)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kalshi_markets(series, get):
    out, cursor = [], ""
    while True:
        url = f"{KALSHI}/markets?series_ticker={series}&status=open&limit=1000"
        page = get(url + (f"&cursor={cursor}" if cursor else ""))
        out.extend(page.get("markets") or [])
        cursor = page.get("cursor") or ""
        if not cursor:
            return out


def _kalshi_teams(event_ticker):
    """KXEPLGAME-26OCT25SUNLEE -> ("SUN", "LEE")."""
    code = event_ticker.split("-")[1][7:]
    return code[:3], code[3:6]


def _kalshi_common(m):
    home, away = _kalshi_teams(m["event_ticker"])
    end = parse_time(m.get("expected_expiration_time"))
    kickoff = _iso(end - KALSHI_KICKOFF_OFFSET) if end else None
    depth = (_f(m.get("yes_bid_size_fp")) or 0) + (_f(m.get("yes_ask_size_fp")) or 0)
    return home, away, kickoff, _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), depth


def fetch_kalshi(conn, *, players=False, get=get_json):
    """Snapshot open EPL match markets (and player markets when `players`). Returns rows written."""
    quotes, props = [], []
    try:
        for series, market in KALSHI_MATCH_SERIES.items():
            for m in _kalshi_markets(series, get):
                try:
                    home, away, kickoff, bid, ask, depth = _kalshi_common(m)
                except (KeyError, IndexError, TypeError, ValueError):
                    continue  # unexpected market shape: skip it, keep the rest
                last = m["ticker"].rsplit("-", 1)[-1]
                line, side = m.get("floor_strike"), None
                if market == "1x2":
                    side, line = ("draw" if last == "TIE" else last), None
                elif market == "team_total":
                    side = re.sub(r"\d+$", "", last)
                elif market == "total":
                    side = "over"
                else:
                    side, line = "yes", None
                quotes.append(("kalshi", home, away, kickoff, market, line, side, bid, ask, depth, m["ticker"]))
        if players:
            for series in KALSHI_PLAYER_SERIES:
                for m in _kalshi_markets(series, get):
                    try:
                        home, away, kickoff, bid, ask, depth = _kalshi_common(m)
                        team = m["ticker"].split("-")[2][:3]
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    if series == "KXEPLGOAL":
                        market = {0.5: "goal1", 1.5: "goal2"}.get(m.get("floor_strike"))
                        if market is None:
                            continue
                    else:
                        market = "soa"
                    player = (m.get("title") or "").split(":", 1)[0].strip()
                    props.append(("kalshi", home, away, kickoff, player, team, market, bid, ask,
                                  _f(m.get("last_price_dollars")), depth, m["ticker"]))
    except FetchError as e:
        sys.stderr.write(f"warning: Kalshi unavailable ({e}); keeping previous odds\n")
        return 0
    ts = now_iso()
    with conn:
        conn.executemany("INSERT INTO market_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(ts, *q) for q in quotes])
        conn.executemany("INSERT INTO player_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(ts, *p) for p in props])
    return len(quotes) + len(props)


def _poly_kickoff(s):
    # "2026-10-25 14:00:00+00" -> "2026-10-25T14:00:00Z"
    if not s:
        return None
    s = s.replace(" ", "T")
    if s.endswith("+00"):
        s = s[:-3] + "+00:00"
    return _iso(parse_time(s))


def fetch_polymarket(conn, get=get_json):
    """Snapshot open EPL moneyline and total-goals markets. Returns rows written."""
    try:
        events = get(f"{POLY}/events?series_id={POLY_EPL_SERIES}&closed=false&limit=500") or []
    except FetchError as e:
        sys.stderr.write(f"warning: Polymarket unavailable ({e}); keeping previous odds\n")
        return 0
    quotes = []
    for e in events:
        title = e.get("title") or ""
        if " - " in title and not title.endswith(" - More Markets"):
            continue  # player props are untraded placeholders; other side markets unused
        parts = (e.get("slug") or "").split("-")
        if len(parts) < 3 or parts[0] != "epl":
            continue
        home, away = parts[1].upper(), parts[2].upper()
        for m in e.get("markets") or []:
            kind = m.get("sportsMarketType")
            bid, ask, depth = _f(m.get("bestBid")), _f(m.get("bestAsk")), _f(m.get("liquidityNum"))
            kickoff = _poly_kickoff(m.get("gameStartTime"))
            if kind == "moneyline":
                side = "draw" if "draw" in (m.get("question") or "").lower() else (m.get("slug") or "").rsplit("-", 1)[-1].upper()
                quotes.append(("polymarket", home, away, kickoff, "1x2", None, side, bid, ask, depth, m.get("slug")))
            elif kind == "totals":
                try:
                    outcomes = json.loads(m.get("outcomes") or "[]")
                except (TypeError, ValueError):
                    continue
                if outcomes[:1] != ["Over"]:
                    continue
                quotes.append(("polymarket", home, away, kickoff, "total", _f(m.get("line")), "over",
                               bid, ask, depth, m.get("slug")))
    ts = now_iso()
    with conn:
        conn.executemany("INSERT INTO market_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(ts, *q) for q in quotes])
    return len(quotes)
