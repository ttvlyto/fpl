import json
import tempfile
import unittest
from pathlib import Path

from sleeperfpl.config import ConfigError
from sleeperfpl.http import FetchError
from sleeperfpl.scoring_map import (ScoringRule, coverage, ensure_scoring_map, flatten_scoring_settings,
                                    generate_scoring_map, league_drift,
                                    load_scoring_map, probe_sleeper_stats, rule_lines, score_match)


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


class ReviewFixScoringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_minutes_rules_always_get_threshold(self):
        r = generate_scoring_map({"appearance": 1, "min_60": 2, "min_played": 1})["rules"]
        self.assertEqual((r["appearance"]["fpl_field"], r["appearance"]["min"]), ("minutes", 1))
        self.assertEqual((r["min_60"]["fpl_field"], r["min_60"]["min"]), ("minutes", 60))
        self.assertEqual(r["min_played"]["min"], 1)

    def test_goal_lookalikes_not_goals_scored(self):
        r = generate_scoring_map({"goals_against": -1, "shot_on_goal": 1, "error_lead_to_goal": -1,
                                  "goalkeeper_save": 1, "pk_save": 5})["rules"]
        self.assertEqual(r["goals_against"]["fpl_field"], "goals_conceded")
        self.assertIsNone(r["shot_on_goal"]["fpl_field"])
        self.assertIsNone(r["error_lead_to_goal"]["fpl_field"])
        self.assertEqual(r["goalkeeper_save"]["fpl_field"], "saves")
        self.assertEqual(r["pk_save"]["fpl_field"], "penalties_saved")

    def test_rule_lines_show_guesses(self):
        lines = rule_lines([ScoringRule("goal", "goals_scored", 5), ScoringRule("shots", None, 1)])
        self.assertEqual(lines, ["goal -> goals_scored (5)", "shots -> (no FPL stat) (1)"])

    def write(self, data):
        path = self.dir / "scoring_map.json"
        path.write_text(json.dumps(data))
        return path

    def test_bad_shapes_raise_config_error(self):
        cases = [([1, 2], "scoring_map.json"),
                 ({"rules": {"goals": 6}}, "'goals'"),
                 ({"rules": {"app": {"fpl_field": "minutes", "points": 1, "min": "abc"}}}, "'app'"),
                 ({"rules": {"g": {"fpl_field": "goals_scored", "points": 1, "positions": "DEF"}}}, "'g'")]
        for data, needle in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(ConfigError, needle):
                    load_scoring_map(self.write(data))

    def test_numeric_strings_converted(self):
        rules = load_scoring_map(self.write({"rules": {"app": {"fpl_field": "minutes", "points": "2", "min": "60"}}}))
        self.assertEqual(score_match({"minutes": 90}, "MID", rules), 2)

    def test_league_drift(self):
        rules = [ScoringRule("goal", "goals_scored", 5), ScoringRule("assist", "assists", 3)]
        not_in_map, changed = league_drift(rules, {"goal": 6, "assist": 3, "shots": 1})
        self.assertEqual(not_in_map, ["shots"])
        self.assertEqual(changed, ["goal"])


class PositionalScoringTests(unittest.TestCase):
    NESTED = {"forward": {"goals": 9, "shots_on_target": 2}, "goalkeeper": {"saves": 2, "penalty_kick_saves": 8}}

    def test_flatten_nested_settings(self):
        self.assertEqual(flatten_scoring_settings(self.NESTED),
                         {"goals_fwd": 9, "shots_on_target_fwd": 2, "saves_gk": 2, "penalty_kick_saves_gk": 8})
        self.assertEqual(flatten_scoring_settings({"goal": 5}), {"goal": 5})

    def test_generate_from_nested(self):
        r = generate_scoring_map(self.NESTED)["rules"]
        self.assertEqual(r["goals_fwd"], {"fpl_field": "goals_scored", "points": 9, "positions": ["FWD"]})
        self.assertEqual(r["penalty_kick_saves_gk"]["fpl_field"], "penalties_saved")
        self.assertEqual(r["saves_gk"]["fpl_field"], "saves")

    def test_sleeper_soccer_names(self):
        r = generate_scoring_map({"midfielder": {"penalty_kicks_missed": -4, "second_yellow_card": -5,
                                                 "penalty_kicks_drawn": 2, "shots_on_target": 2,
                                                 "tackles_won": 1, "yellow_cards": -2}})["rules"]
        self.assertEqual(r["penalty_kicks_missed_mid"]["fpl_field"], "penalties_missed")
        self.assertIsNone(r["second_yellow_card_mid"]["fpl_field"])
        self.assertIsNone(r["penalty_kicks_drawn_mid"]["fpl_field"])
        self.assertIsNone(r["shots_on_target_mid"]["fpl_field"])
        self.assertEqual(r["tackles_won_mid"]["fpl_field"], "tackles")
        self.assertEqual(r["yellow_cards_mid"]["fpl_field"], "yellow_cards")

    def test_one_fpl_stat_counted_once_per_position(self):
        r = generate_scoring_map({"defender": {"interceptions": 1, "blocked_shots": 1, "effective_clearances": 0.25}})["rules"]
        mapped = [k for k, v in r.items() if v["fpl_field"] == "clearances_blocks_interceptions"]
        self.assertEqual(len(mapped), 1)

    def test_league_drift_with_nested(self):
        rules = [ScoringRule("goals_fwd", "goals_scored", 9, positions=["FWD"])]
        self.assertEqual(league_drift(rules, {"forward": {"goals": 9}}), ([], []))


class SleeperCodeTests(unittest.TestCase):
    def test_sleeper_position_prefix_and_codes(self):
        r = generate_scoring_map({"pos_d_g": 10, "pos_f_at": 6, "pos_gk_sv": 2, "pos_m_cs": 1, "pos_d_ga": -2,
                                  "pos_f_og": -5, "pos_m_tkw": 1, "pos_d_int": 1, "pos_d_bs": 1, "pos_d_clr": 0.25,
                                  "pos_f_sot": 2, "pos_f_yc2": -5, "pos_m_pkd": 2, "pos_gk_pks": 8, "pos_d_min": 0,
                                  "pos_m_yc": -2, "pos_m_rc": -7, "pos_f_pkm": -4})["rules"]
        self.assertEqual(r["pos_d_g"], {"fpl_field": "goals_scored", "points": 10, "positions": ["DEF"]})
        expected = {"pos_f_at": ("assists", ["FWD"]), "pos_gk_sv": ("saves", ["GK"]),
                    "pos_m_cs": ("clean_sheets", ["MID"]), "pos_d_ga": ("goals_conceded", ["DEF"]),
                    "pos_f_og": ("own_goals", ["FWD"]), "pos_m_tkw": ("tackles", ["MID"]),
                    "pos_d_int": ("clearances_blocks_interceptions", ["DEF"]),
                    "pos_gk_pks": ("penalties_saved", ["GK"]), "pos_m_yc": ("yellow_cards", ["MID"]),
                    "pos_m_rc": ("red_cards", ["MID"]), "pos_f_pkm": ("penalties_missed", ["FWD"])}
        for key, (field, positions) in expected.items():
            with self.subTest(key=key):
                self.assertEqual((r[key]["fpl_field"], r[key]["positions"]), (field, positions))
        for key in ("pos_d_bs", "pos_d_clr", "pos_f_sot", "pos_f_yc2", "pos_m_pkd"):
            with self.subTest(key=key):
                self.assertIsNone(r[key]["fpl_field"])
        self.assertEqual((r["pos_d_min"]["fpl_field"], r["pos_d_min"]["min"]), ("minutes", 1))


if __name__ == "__main__":
    unittest.main()
