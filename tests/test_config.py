import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sleeperfpl.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, data, name="config.json"):
        path = self.tmpdir / name
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_second_opinion_defaults_and_partial_merge(self):
        cfg = load_config(str(self.write({"league_id": "1", "owner": "me"})))
        self.assertEqual(cfg.second_opinion, {"enabled": False, "model": "claude-opus-5-5", "effort": "high",
                                              "lead_hours": 48})
        cfg = load_config(str(self.write({"league_id": "1", "owner": "me", "second_opinion": {"enabled": True}})))
        self.assertTrue(cfg.second_opinion["enabled"])
        self.assertEqual(cfg.second_opinion["lead_hours"], 48)

    def test_second_opinion_bad_values_rejected(self):
        for bad in ({"effort": "huge"}, {"lead_hours": 0}, {"lead_hours": "soon"}, {"enabled": "false"}):
            path = self.write({"league_id": "1", "owner": "me", "second_opinion": bad})
            with self.assertRaisesRegex(ConfigError, "second_opinion"):
                load_config(str(path))

    def test_defaults_and_relative_paths(self):
        path = self.write({"league_id": "123", "owner": "me"})
        cfg = load_config(str(path))
        self.assertEqual(cfg.db_path, self.tmpdir / "data/fpl.db")
        self.assertEqual(cfg.output_dir, self.tmpdir / "site")
        self.assertEqual(cfg.difficulty_multipliers[1], 1.2)
        self.assertEqual(cfg.difficulty_multipliers[5], 0.8)
        self.assertEqual(cfg.avg_window, 5)
        self.assertEqual(cfg.free_agents_per_position, 10)
        self.assertEqual(cfg.slot_eligibility["GK"], ["GK"])

    def test_category_cutoffs(self):
        cfg = load_config(str(self.write({"league_id": "1", "owner": "me"})))
        self.assertEqual((cfg.category_low, cfg.category_haul), (3, 10))
        with self.assertRaises(ConfigError):
            load_config(str(self.write({"league_id": "1", "owner": "me", "category_low": 10, "category_haul": 5})))

    def test_env_var_path(self):
        path = self.write({"league_id": "999", "owner": "me"}, "other.json")
        with mock.patch.dict(os.environ, {"SLEEPER_FPL_CONFIG": str(path)}):
            cfg = load_config(None)
        self.assertEqual(cfg.league_id, "999")

    def test_missing_league_id_raises(self):
        path = self.write({"owner": "me"})
        with self.assertRaisesRegex(ConfigError, "league_id"):
            load_config(str(path))

    def test_invalid_json_names_file(self):
        path = self.write("{not json")
        with self.assertRaisesRegex(ConfigError, str(path)):
            load_config(str(path))

    def test_override_tunable(self):
        path = self.write({"league_id": "1", "owner": "me", "swap_min_gain": 2.5,
                           "difficulty_multipliers": {"1": 1.5}})
        cfg = load_config(str(path))
        self.assertEqual(cfg.swap_min_gain, 2.5)
        self.assertEqual(cfg.difficulty_multipliers[1], 1.5)


class SleeperSlotDefaultsTests(unittest.TestCase):
    def test_default_slots_cover_sleeper_soccer(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"league_id": "1", "owner": "me"}))
            elig = load_config(str(path)).slot_eligibility
        for slot in ("F", "M", "D", "GK", "FM_FLEX", "MD_FLEX", "FMD_FLEX"):
            self.assertIn(slot, elig)
        self.assertEqual(sorted(elig["FMD_FLEX"]), ["D", "DEF", "F", "M"])
        self.assertIn("DEF", elig["D"])


class NewSettingsTests(unittest.TestCase):
    def load(self, extra):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"league_id": "1", "owner": "me", **extra}))
            return load_config(str(path))

    def test_defaults(self):
        cfg = self.load({})
        self.assertEqual((cfg.stats_source, cfg.undroppable), ("sleeper", []))

    def test_bad_stats_source(self):
        with self.assertRaisesRegex(ConfigError, "stats_source"):
            self.load({"stats_source": "espn"})

    def test_minutes_defaults_and_validation(self):
        cfg = self.load({})
        self.assertEqual((cfg.minutes_model, cfg.label_prior_k, cfg.sleeper_label_weight), ("shadow", 20, 1.0))
        self.assertEqual(self.load({"minutes_model": "on"}).minutes_model, "on")
        with self.assertRaisesRegex(ConfigError, "minutes_model"):
            self.load({"minutes_model": "maybe"})
        with self.assertRaisesRegex(ConfigError, "sleeper_label_weight"):
            self.load({"sleeper_label_weight": 1.5})

    def test_undroppable_must_be_list(self):
        with self.assertRaisesRegex(ConfigError, "undroppable"):
            self.load({"undroppable": "Bukayo Saka"})


class MarketConfigTests(unittest.TestCase):
    def test_config_market_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"league_id": "1", "owner": "me"}))
            cfg = load_config(str(path))
        self.assertEqual(cfg.market_max_spread, 0.10)
        self.assertEqual(cfg.market_min_depth, {"kalshi": 50, "polymarket": 1000})
        self.assertEqual((cfg.market_stale_hours, cfg.matchday_window_hours, cfg.league_avg_goals), (24, 3, 1.35))
        self.assertEqual((cfg.flag_cs_gap, cfg.flag_goal_gap), (0.25, 0.20))


if __name__ == "__main__":
    unittest.main()
