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


if __name__ == "__main__":
    unittest.main()
