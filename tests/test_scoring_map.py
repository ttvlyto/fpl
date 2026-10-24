import json
import tempfile
import unittest
from pathlib import Path

from sleeperfpl.config import ConfigError
from sleeperfpl.http import FetchError
from sleeperfpl.scoring_map import (ScoringRule, coverage, ensure_scoring_map, generate_scoring_map,
                                    load_scoring_map, probe_sleeper_stats, score_match)


class ScoringMapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_generate_suggests_fields(self):
        m = generate_scoring_map({"goal_def": 6, "own_goal": -2, "yc": -1, "weird_stat": 1, "cs_gk": 4})
        self.assertEqual(m["rules"]["goal_def"], {"fpl_field": "goals_scored", "points": 6, "positions": ["DEF"]})
        self.assertEqual(m["rules"]["own_goal"]["fpl_field"], "own_goals")
        self.assertEqual(m["rules"]["yc"]["fpl_field"], "yellow_cards")
        self.assertEqual(m["rules"]["cs_gk"], {"fpl_field": "clean_sheets", "points": 4, "positions": ["GK"]})
        self.assertIsNone(m["rules"]["weird_stat"]["fpl_field"])

    def test_two_letter_tokens_need_whole_token(self):
        # "cs" inside "focus" must not map to clean_sheets
        self.assertIsNone(generate_scoring_map({"focus": 1})["rules"]["focus"]["fpl_field"])

    def test_ensure_never_overwrites(self):
        path = self.dir / "scoring_map.json"
        self.assertTrue(ensure_scoring_map(path, {"goal": 5}))
        path.write_text('{"rules": {"custom": {"fpl_field": null, "points": 0}}}')
        self.assertFalse(ensure_scoring_map(path, {"goal": 5}))
        self.assertIn("custom", path.read_text())

    def test_load_bad_json_names_file(self):
        path = self.dir / "scoring_map.json"
        path.write_text("{nope")
        with self.assertRaisesRegex(ConfigError, "scoring_map.json"):
            load_scoring_map(path)

    def test_load_unknown_field_names_key(self):
        path = self.dir / "scoring_map.json"
        path.write_text(json.dumps({"rules": {"g": {"fpl_field": "goalz", "points": 1}}}))
        with self.assertRaisesRegex(ConfigError, r"(?s)'g'.*goalz"):
            load_scoring_map(path)

    def test_load_roundtrip(self):
        path = self.dir / "scoring_map.json"
        ensure_scoring_map(path, {"goal_def": 6, "app_60": 2})
        rules = {r.key: r for r in load_scoring_map(path)}
        self.assertEqual(rules["goal_def"].positions, ["DEF"])
        self.assertEqual(rules["goal_def"].points, 6)

    def test_score_match(self):
        rules = [ScoringRule("g", "goals_scored", 5), ScoringRule("app", "minutes", 2, min=60),
                 ScoringRule("gc", "goals_conceded", -1, per=2, positions=["DEF"]),
                 ScoringRule("x", None, 9)]
        self.assertEqual(score_match({"goals_scored": 1, "minutes": 90, "goals_conceded": 3}, "DEF", rules), 5 + 2 - 1)
        self.assertEqual(score_match({"goals_scored": 1, "minutes": 59, "goals_conceded": 3}, "MID", rules), 5)

    def test_coverage_splits_keys(self):
        rules = [ScoringRule("g", "goals_scored", 5), ScoringRule("shots", None, 1), ScoringRule("off", None, 0)]
        self.assertEqual(coverage(rules), (["g", "off"], ["shots"]))

    def test_probe_never_raises(self):
        def failing(url, **kw):
            raise FetchError("nope")
        lines = probe_sleeper_stats("clubsoccer:epl", "2026", 9, get=failing)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("nope" in line for line in lines))

    def test_probe_reports_counts(self):
        lines = probe_sleeper_stats("s", "2026", 9, get=lambda url, **kw: {"a": {}, "b": {}})
        self.assertTrue(all("2 items" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
