import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sleeperfpl.markets import find_fixture, mid, team_code, usable
from sleeperfpl.matching import load_overrides

CFG = SimpleNamespace(market_max_spread=0.10, market_min_depth={"kalshi": 50, "polymarket": 1000})
TEAMS = {1: "ARS", 2: "CHE"}


def q(**kw):
    base = {"source": "kalshi", "bid": 0.40, "ask": 0.44, "depth": 500}
    return {**base, **kw}


class MarketsTests(unittest.TestCase):
    def test_team_code_aliases_and_overrides(self):
        codes = {"BHA", "CHE", "ARS"}
        self.assertEqual(team_code("kalshi", "cfc", codes, {}), "CHE")
        self.assertEqual(team_code("kalshi", "ARS", codes, {}), "ARS")
        self.assertIsNone(team_code("kalshi", "XYZ", codes, {}))
        self.assertEqual(team_code("polymarket", "bri", codes, {"market_teams": {"polymarket:BRI": "BHA"}}), "BHA")

    def test_usable_filters_spread_and_depth(self):
        self.assertTrue(usable(q(), CFG))
        self.assertFalse(usable(q(bid=0.30, ask=0.45), CFG))
        self.assertFalse(usable(q(depth=10), CFG))
        self.assertFalse(usable(q(bid=0.0), CFG))
        self.assertFalse(usable(q(source="polymarket", depth=500), CFG))
        self.assertAlmostEqual(mid(q()), 0.42)

    def test_find_fixture_requires_codes_and_time(self):
        fixtures = [{"fixture_id": 7, "team_h": 1, "team_a": 2, "kickoff_time": "2026-10-31T15:00:00Z"}]
        self.assertEqual(find_fixture("ARS", "CHE", "2026-11-01T21:00:00Z", fixtures, TEAMS)["fixture_id"], 7)
        self.assertIsNone(find_fixture("ARS", "CHE", "2026-11-02T08:00:00Z", fixtures, TEAMS))
        self.assertIsNone(find_fixture("CHE", "ARS", "2026-10-31T15:00:00Z", fixtures, TEAMS))

    def test_overrides_keep_market_teams(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "o.json"
            self.assertEqual(load_overrides(p)["market_teams"], {})
            p.write_text(json.dumps({"market_teams": {"kalshi:NFF": "NFO"}}))
            self.assertEqual(load_overrides(p)["market_teams"], {"kalshi:NFF": "NFO"})


if __name__ == "__main__":
    unittest.main()
