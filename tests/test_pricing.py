import math
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from sleeperfpl.db import connect
from sleeperfpl.markets import latest_quotes
from sleeperfpl.pricing import fit_fixture, fixture_odds, poisson_probs

CFG = SimpleNamespace(market_max_spread=0.10, market_min_depth={"kalshi": 50, "polymarket": 1000},
                      market_stale_hours=24)
NOW = datetime(2026, 10, 29, 20, tzinfo=timezone.utc)
FRESH = "2026-10-29T18:00:00Z"
TEAMS = {1: "ARS", 2: "HUL"}
FIXTURES = [{"fixture_id": 9, "team_h": 1, "team_a": 2, "kickoff_time": "2026-10-31T15:00:00Z"}]


def quote(market, side, p, line=None, source="kalshi", fetched_at=FRESH, home="ARS", away="HUL", spread=0.02):
    return {"fetched_at": fetched_at, "source": source, "home": home, "away": away,
            "kickoff": "2026-10-31T15:00:00Z", "market": market, "line": line, "side": side,
            "bid": p - spread / 2, "ask": p + spread / 2, "depth": 5000}


def model_quotes(lh, la, **kw):
    pr = poisson_probs(lh, la)
    return [quote("1x2", "ARS", pr["home"], **kw), quote("1x2", "draw", pr["draw"], **kw),
            quote("1x2", "HUL", pr["away"], **kw), quote("total", "over", pr["over"][2.5], 2.5, **kw),
            quote("team_total", "ARS", pr["home_over"][1.5], 1.5, **kw),
            quote("team_total", "HUL", pr["away_over"][0.5], 0.5, **kw), quote("btts", "yes", pr["btts"], **kw)]


class PricingTests(unittest.TestCase):
    def test_fit_recovers_known_lambdas(self):
        o = fit_fixture(model_quotes(1.8, 0.9), "ARS", "HUL")
        self.assertAlmostEqual(o.lam_home, 1.8, delta=0.1)
        self.assertAlmostEqual(o.lam_away, 0.9, delta=0.1)
        self.assertAlmostEqual(o.cs_home, math.exp(-0.9), delta=0.05)
        self.assertAlmostEqual(o.p_home + o.p_draw + o.p_away, 1.0, places=6)

    def test_avg_spread(self):
        qs = model_quotes(1.8, 0.9)[:2] + [{**q, "bid": q["bid"] - 0.01, "ask": q["ask"] + 0.01}
                                            for q in model_quotes(1.8, 0.9)[2:4]]
        self.assertAlmostEqual(fit_fixture(qs, "ARS", "HUL").avg_spread, 0.03)

    def test_fit_with_only_1x2(self):
        o = fit_fixture(model_quotes(2.0, 0.7)[:3], "ARS", "HUL")
        self.assertGreater(o.p_home, o.p_away)
        self.assertGreater(o.lam_home, o.lam_away)

    def test_no_quotes_returns_none(self):
        self.assertIsNone(fit_fixture([], "ARS", "HUL"))

    def test_stale_and_thin_quotes_dropped(self):
        stale = model_quotes(1.5, 1.0, fetched_at="2026-10-28T12:00:00Z")
        thin = model_quotes(1.5, 1.0, spread=0.2)
        odds, _ = fixture_odds(stale + thin, FIXTURES, TEAMS, CFG, NOW, {})
        self.assertEqual(odds, {})

    def test_unmatched_quote_reported(self):
        odds, unmatched = fixture_odds(model_quotes(1.5, 1.0, home="ARS", away="XYZ"), FIXTURES, TEAMS, CFG, NOW, {})
        self.assertEqual(odds, {})
        self.assertTrue(any("XYZ" in u for u in unmatched))

    def test_two_sources_both_used(self):
        qs = model_quotes(1.5, 1.0) + [q | {"source": "polymarket"} for q in model_quotes(1.5, 1.0)]
        for q in qs:
            if q["source"] == "polymarket":
                q["depth"] = 50000
        odds, _ = fixture_odds(qs, FIXTURES, TEAMS, CFG, NOW, {})
        self.assertEqual(odds[9].sources, ["kalshi", "polymarket"])

    def test_latest_quotes_per_source(self):
        conn = connect(":memory:")
        for ts, src in (("2026-10-29T10:00:00Z", "kalshi"), ("2026-10-29T11:00:00Z", "polymarket"),
                        ("2026-10-29T09:00:00Z", "kalshi")):
            conn.execute("INSERT INTO market_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ts, src, "ARS", "HUL", "k", "1x2", None, "ARS", 0.5, 0.52, 100, "r"))
        rows = latest_quotes(conn, "market_quotes")
        self.assertEqual(sorted((r["source"], r["fetched_at"]) for r in rows),
                         [("kalshi", "2026-10-29T10:00:00Z"), ("polymarket", "2026-10-29T11:00:00Z")])


class IdentifiabilityTests(unittest.TestCase):
    def test_underdetermined_quotes_give_no_fit(self):
        qs = model_quotes(1.6, 1.1)
        by = {(q["market"], q["side"]): q for q in qs}
        for only in (["total"], ["home"], ["btts"], ["team_home"]):
            pick = {"total": [by[("total", "over")]], "home": [by[("1x2", "ARS")]],
                    "btts": [by[("btts", "yes")]], "team_home": [by[("team_total", "ARS")]]}[only[0]]
            with self.subTest(only=only):
                self.assertIsNone(fit_fixture(pick, "ARS", "HUL"))

    def test_enough_quotes_fit(self):
        qs = model_quotes(1.6, 1.1)
        by = {(q["market"], q["side"]): q for q in qs}
        self.assertIsNotNone(fit_fixture([by[("1x2", "ARS")], by[("total", "over")]], "ARS", "HUL"))
        self.assertIsNotNone(fit_fixture([by[("team_total", "ARS")], by[("team_total", "HUL")]], "ARS", "HUL"))
        self.assertIsNotNone(fit_fixture([by[("1x2", "ARS")], by[("1x2", "HUL")]], "ARS", "HUL"))

    def test_in_play_quotes_dropped(self):
        late = model_quotes(1.5, 1.0, fetched_at="2026-10-31T16:00:00Z")   # after the 15:00 kickoff
        odds, _ = fixture_odds(late, FIXTURES, TEAMS, CFG, datetime(2026, 10, 31, 17, tzinfo=timezone.utc), {})
        self.assertEqual(odds, {})


if __name__ == "__main__":
    unittest.main()
