import unittest

from sleeperfpl.db import connect, latest
from sleeperfpl.fetch_markets import fetch_kalshi, fetch_polymarket
from sleeperfpl.http import FetchError


def km(ticker, event, **kw):
    base = {"ticker": ticker, "event_ticker": event, "expected_expiration_time": "2026-10-25T19:30:00Z",
            "yes_bid_dollars": "0.0900", "yes_ask_dollars": "0.1200",
            "yes_bid_size_fp": "1731.00", "yes_ask_size_fp": "1380.41", "last_price_dollars": "0.1000"}
    return {**base, **kw}


GAME = [km("KXEPLGAME-26OCT25SUNLEE-SUN", "KXEPLGAME-26OCT25SUNLEE"),
        km("KXEPLGAME-26OCT25SUNLEE-TIE", "KXEPLGAME-26OCT25SUNLEE")]
TEAMTOT = [km("KXEPLTEAMTOTAL-26OCT25SUNLEE-SUN3", "KXEPLTEAMTOTAL-26OCT25SUNLEE", floor_strike=2.5)]
TOTAL = [km("KXEPLTOTAL-26OCT25SUNLEE-3", "KXEPLTOTAL-26OCT25SUNLEE", floor_strike=2.5)]
BTTS = [km("KXEPLBTTS-26OCT25SUNLEE-BTTS", "KXEPLBTTS-26OCT25SUNLEE")]
GOAL = [km("KXEPLGOAL-26OCT24CFCTOT-TOTBDAVIE33-1", "KXEPLGOAL-26OCT24CFCTOT", floor_strike=0.5,
           title="Ben Davies: 1+ goals")]


def kalshi_get(pages=None, fail=False):
    pages = pages or {}
    calls = []

    def get(url, **kw):
        calls.append(url)
        if fail:
            raise FetchError("down")
        series = url.split("series_ticker=")[1].split("&")[0]
        cursor = url.split("cursor=")[1].split("&")[0] if "cursor=" in url else ""
        return pages.get((series, cursor), {"markets": [], "cursor": ""})
    get.calls = calls
    return get


class KalshiTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def rows(self, table="market_quotes"):
        return [dict(r) for r in latest(self.conn, table)]

    def test_kalshi_game_market_parsed(self):
        fetch_kalshi(self.conn, get=kalshi_get({("KXEPLGAME", ""): {"markets": GAME[:1], "cursor": ""}}))
        r = self.rows()[0]
        self.assertEqual((r["source"], r["market"], r["home"], r["away"], r["side"]), ("kalshi", "1x2", "SUN", "LEE", "SUN"))
        self.assertEqual((r["bid"], r["ask"]), (0.09, 0.12))
        self.assertAlmostEqual(r["depth"], 3111.41)
        self.assertEqual(r["kickoff"], "2026-10-25T17:30:00Z")

    def test_kalshi_tie_team_total_total_btts(self):
        fetch_kalshi(self.conn, get=kalshi_get({("KXEPLGAME", ""): {"markets": GAME[1:]},
                                                ("KXEPLTEAMTOTAL", ""): {"markets": TEAMTOT},
                                                ("KXEPLTOTAL", ""): {"markets": TOTAL},
                                                ("KXEPLBTTS", ""): {"markets": BTTS}}))
        got = {(r["market"], r["side"], r["line"]) for r in self.rows()}
        self.assertEqual(got, {("1x2", "draw", None), ("team_total", "SUN", 2.5), ("total", "over", 2.5),
                               ("btts", "yes", None)})

    def test_kalshi_pagination_follows_cursor(self):
        g = kalshi_get({("KXEPLGAME", ""): {"markets": GAME[:1], "cursor": "c2"},
                        ("KXEPLGAME", "c2"): {"markets": GAME[1:], "cursor": ""}})
        fetch_kalshi(self.conn, get=g)
        self.assertEqual(len([r for r in self.rows() if r["market"] == "1x2"]), 2)

    def test_kalshi_players_parsed(self):
        fetch_kalshi(self.conn, players=True, get=kalshi_get({("KXEPLGOAL", ""): {"markets": GOAL}}))
        r = self.rows("player_quotes")[0]
        self.assertEqual((r["player"], r["team"], r["market"], r["home"], r["away"]),
                         ("Ben Davies", "TOT", "goal1", "CFC", "TOT"))
        self.assertEqual(r["last"], 0.1)

    def test_fetch_failure_non_fatal(self):
        self.assertEqual(fetch_kalshi(self.conn, get=kalshi_get(fail=True)), 0)
        self.assertEqual(self.rows(), [])


PM_EVENTS = [
    {"title": "Crystal Palace FC vs. Newcastle United FC", "slug": "epl-cry-new-2026-10-25", "markets": [
        {"question": "Will Crystal Palace FC win on 2026-10-25?", "slug": "epl-cry-new-2026-10-25-cry",
         "sportsMarketType": "moneyline", "bestBid": 0.32, "bestAsk": 0.33, "liquidityNum": 258774.1,
         "gameStartTime": "2026-10-25 14:00:00+00", "outcomes": '["Yes", "No"]'},
        {"question": "Will Crystal Palace FC vs. Newcastle United FC end in a draw?", "slug": "epl-cry-new-2026-10-25-draw",
         "sportsMarketType": "moneyline", "bestBid": 0.26, "bestAsk": 0.27, "liquidityNum": 1e5,
         "gameStartTime": "2026-10-25 14:00:00+00", "outcomes": '["Yes", "No"]'},
        {"question": "Will Newcastle United FC win on 2026-10-25?", "slug": "epl-cry-new-2026-10-25-new",
         "sportsMarketType": "moneyline", "bestBid": 0.41, "bestAsk": 0.42, "liquidityNum": 1e5,
         "gameStartTime": "2026-10-25 14:00:00+00", "outcomes": '["Yes", "No"]'}]},
    {"title": "Crystal Palace FC vs. Newcastle United FC - More Markets", "slug": "epl-cry-new-2026-10-25-more-markets",
     "markets": [
        {"question": "Crystal Palace FC vs. Newcastle United FC: O/U 2.5", "slug": "x", "sportsMarketType": "totals",
         "line": 2.5, "bestBid": 0.44, "bestAsk": 0.45, "liquidityNum": 2e5,
         "gameStartTime": "2026-10-25 14:00:00+00", "outcomes": '["Over", "Under"]'},
        {"question": "Spread: Crystal Palace FC (-1.5)", "slug": "y", "sportsMarketType": "spreads",
         "bestBid": 0.1, "bestAsk": 0.12, "liquidityNum": 1e4, "gameStartTime": "2026-10-25 14:00:00+00",
         "outcomes": '["Yes", "No"]'}]},
    {"title": "Crystal Palace FC vs. Newcastle United FC - Player Props", "slug": "z", "markets": [
        {"question": "X: Anytime Goalscorer", "slug": "q", "sportsMarketType": "props", "bestBid": 0.1,
         "bestAsk": 0.2, "liquidityNum": 0, "gameStartTime": "2026-10-25 14:00:00+00", "outcomes": '["Yes", "No"]'}]},
]


class PolymarketTests(unittest.TestCase):
    def test_polymarket_moneyline_and_totals(self):
        conn = connect(":memory:")
        n = fetch_polymarket(conn, get=lambda url, **kw: PM_EVENTS)
        rows = [dict(r) for r in latest(conn, "market_quotes")]
        self.assertEqual(n, 4)
        got = {(r["market"], r["side"], r["line"]) for r in rows}
        self.assertEqual(got, {("1x2", "CRY", None), ("1x2", "draw", None), ("1x2", "NEW", None),
                               ("total", "over", 2.5)})
        r = next(r for r in rows if r["side"] == "CRY")
        self.assertEqual((r["home"], r["away"], r["kickoff"], r["source"]), ("CRY", "NEW", "2026-10-25T14:00:00Z", "polymarket"))

    def test_polymarket_failure_non_fatal(self):
        conn = connect(":memory:")
        def boom(url, **kw):
            raise FetchError("down")
        self.assertEqual(fetch_polymarket(conn, get=boom), 0)


class MalformedTests(unittest.TestCase):
    def test_malformed_markets_skipped(self):
        conn = connect(":memory:")
        bad = [{"ticker": "KXEPLGAME-X", "event_ticker": "KXEPLGAME"}] + GAME[:1]
        n = fetch_kalshi(conn, get=kalshi_get({("KXEPLGAME", ""): {"markets": bad}}))
        self.assertEqual(n, 1)
        events = PM_EVENTS + [{"title": "A vs B", "slug": "epl-aaa-bbb-2026", "markets": [{"sportsMarketType": "totals",
                                                                                      "outcomes": "not json"}]}]
        self.assertEqual(fetch_polymarket(conn, get=lambda url, **kw: events), 4)


if __name__ == "__main__":
    unittest.main()
