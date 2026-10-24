import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sleeperfpl.config import Config, ConfigError
from sleeperfpl.db import connect, meta_set
from sleeperfpl.report import BuildError, build_report, render_html, write_outputs

NOW = datetime(2026, 10, 22, 20, tzinfo=timezone.utc)
TS = "2026-10-22T06:00:00Z"


def make_cfg(tmp, owner="me"):
    smap = tmp / "scoring_map.json"
    smap.write_text(json.dumps({"rules": {
        "goal": {"fpl_field": "goals_scored", "points": 5},
        "app_60": {"fpl_field": "minutes", "points": 2, "min": 60},
        "shots": {"fpl_field": None, "points": 1},
    }}))
    return Config(league_id="L1", owner=owner, db_path=tmp / "x.db", output_dir=tmp / "site",
                  scoring_map_path=smap, overrides_path=tmp / "overrides.json",
                  difficulty_multipliers={1: 1.2, 2: 1.1, 3: 1.0, 4: 0.9, 5: 0.8}, avg_window=5,
                  free_agents_per_position=10, swap_min_gain=1.0, max_swaps=3,
                  slot_eligibility={"GK": ["GK"], "DEF": ["DEF"], "MID": ["MID"], "FWD": ["FWD"]},
                  fpl_request_interval=0)


def seed(conn, ts=TS, roster=("s1", "s2", "s3", "s4"), names=None):
    names = names or {}
    league = {"sport": "s", "roster_positions": ["GK", "MID", "MID", "BN", "BN"], "scoring_settings": {}}
    conn.execute("INSERT INTO sleeper_league VALUES (?,?,?)", (ts, "L1", json.dumps(league)))
    conn.executemany("INSERT INTO sleeper_users VALUES (?,?,?)", [(ts, "u1", "me"), (ts, "u2", "rival")])
    players = [("s1", "David Raya", "ARS", ["GK"], None), ("s2", "Bukayo Saka", "ARS", ["MID"], None),
               ("s3", "Declan Rice", "ARS", ["MID"], None), ("s4", "New Signing", "ARS", ["MID"], None),
               ("s5", "Cole Palmer", "CHE", ["MID"], None), ("s6", "Marc Cucurella", "CHE", ["DEF"], None)]
    conn.executemany("INSERT INTO sleeper_players VALUES (?,?,?,?,?,?,?,?)",
                     [(ts, pid, names.get(pid, n), t, json.dumps(pos), "Active", inj, 1)
                      for pid, n, t, pos, inj in players])
    conn.executemany("INSERT INTO sleeper_rosters VALUES (?,?,?,?,?,?)",
                     [(ts, 1, "u1", json.dumps(list(roster)), "[]", "[]"), (ts, 2, "u2", "[]", "[]", "[]")])
    conn.execute("INSERT INTO sleeper_trending VALUES (?,?,?,?)", (ts, "add", "s5", 40))
    elements = [(1, "David", "Raya", "Raya", 1, 1), (2, "Bukayo", "Saka", "Saka", 1, 3),
                (3, "Declan", "Rice", "Rice", 1, 3), (5, "Cole", "Palmer", "Palmer", 2, 3),
                (6, "Marc", "Cucurella", "Cucurella", 2, 2)]
    conn.executemany("INSERT INTO fpl_elements VALUES (?,?,?,?,?,?,?,?,?,?)",
                     [(ts, fid, f, s, w, t, et, "a", None, 900) for fid, f, s, w, t, et in elements])
    conn.executemany("INSERT INTO fpl_teams VALUES (?,?,?,?)", [(ts, 1, "ARS", "Arsenal"), (ts, 2, "CHE", "Chelsea")])
    conn.executemany("INSERT INTO fpl_events VALUES (?,?,?,?)", [(ts, 1, "2026-08-15T10:00:00Z", 1), (ts, 8, "2026-10-17T10:00:00Z", 1),
                                                                 (ts, 9, "2026-10-24T10:00:00Z", 0)])
    conn.execute("INSERT INTO fpl_fixtures VALUES (?,?,?,?,?,?,?,?,?)",
                 (ts, 50, 9, 1, 2, 4, 3, "2026-10-24T11:30:00Z", 0))
    # goals per match: Saka 1 (5+2=7), Rice 0 (2), Palmer 2 (12), Raya 0 (2), Cucurella 0 (2)
    goals = {1: 0, 2: 1, 3: 0, 5: 2, 6: 0}
    rows = []
    for fid, g in goals.items():
        for k in range(3):
            stats = {"goals_scored": g, "minutes": 90}
            rows.append(("2026", fid, 100 + k, 6 + k, f"2026-10-{10 + k}T14:00:00Z", 90, json.dumps(stats), ts))
    rows.append(("2026", 2, 99, 5, "2026-10-01T14:00:00Z", 0, json.dumps({"goals_scored": 0, "minutes": 0}), ts))
    conn.executemany("INSERT OR REPLACE INTO fpl_history VALUES (?,?,?,?,?,?,?,?)", rows)
    meta_set(conn, "history_last_gw", "8")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.conn = connect(":memory:")

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_data_raises(self):
        with self.assertRaisesRegex(BuildError, "fetch-daily"):
            build_report(self.conn, self.cfg, NOW)

    def test_owner_not_found_lists_names(self):
        seed(self.conn)
        with self.assertRaisesRegex(BuildError, "rival"):
            build_report(self.conn, make_cfg(self.tmp, owner="nobody"), NOW)

    def test_unknown_slot_raises_config_error(self):
        seed(self.conn)
        self.conn.execute("UPDATE sleeper_league SET data_json = ?",
                          (json.dumps({"roster_positions": ["GK", "SUPER_FLEX"], "scoring_settings": {}}),))
        with self.assertRaisesRegex(ConfigError, "SUPER_FLEX"):
            build_report(self.conn, self.cfg, NOW)

    def test_report_shape(self):
        seed(self.conn)
        r = build_report(self.conn, self.cfg, NOW)
        self.assertEqual((r["gameweek"], r["mode"]), (9, "instant"))
        xi = {s["slot"] + str(i): s["player"]["name"] for i, s in enumerate(r["xi"]["starters"])}
        self.assertEqual(xi["GK0"], "David Raya")
        starters = [s["player"]["name"] for s in r["xi"]["starters"]]
        self.assertIn("Bukayo Saka", starters)
        saka = next(s["player"] for s in r["xi"]["starters"] if s["player"]["name"] == "Bukayo Saka")
        self.assertEqual((saka["avg"], saka["n"]), (7.0, 3))   # 0-minute match skipped
        self.assertEqual(saka["proj"], 6.3)                    # 7 * 0.9 (home vs difficulty 4)
        self.assertEqual(saka["fixtures"][0], {"opponent": "CHE", "home": True, "difficulty": 4,
                                               "kickoff": "2026-10-24T11:30:00Z"})
        self.assertEqual(len(r["unmatched_rostered"]), 1)
        self.assertEqual(r["unmatched_rostered"][0]["flag"], "no data")
        self.assertEqual(r["coverage"]["missing"], ["shots"])
        self.assertEqual(r["free_agents"]["MID"][0]["name"], "Cole Palmer")
        self.assertEqual(r["free_agents"]["MID"][0]["trending_adds"], 40)
        self.assertEqual(r["swaps"][0]["add"]["name"], "Cole Palmer")
        self.assertEqual(r["sources"]["fpl_history_gw"], "8")
        json.dumps(r)

    def test_uses_latest_snapshot_after_failed_fetch(self):
        seed(self.conn, ts="2026-10-21T06:00:00Z", roster=("s1", "s6"))
        seed(self.conn, ts=TS)
        r = build_report(self.conn, self.cfg, NOW)
        names = [s["player"]["name"] for s in r["xi"]["starters"] if s["player"]]
        self.assertNotIn("Marc Cucurella", names)
        self.assertEqual(r["sources"]["sleeper_rosters"], TS)

    def test_injured_player_flagged_out(self):
        seed(self.conn)
        self.conn.execute("UPDATE fpl_elements SET status = 'i' WHERE fpl_id = 2")
        r = build_report(self.conn, self.cfg, NOW)
        everyone = [s["player"] for s in r["xi"]["starters"] if s["player"]] + r["xi"]["bench"]
        saka = next(p for p in everyone if p["name"] == "Bukayo Saka")
        self.assertEqual((saka["flag"], saka["proj"]), ("out", 0.0))

    def test_html_escapes_and_labels_waiver(self):
        seed(self.conn, names={"s4": "<b>New</b>"})
        r = build_report(self.conn, self.cfg, NOW)
        r["mode"] = "waiver"
        page = render_html(r)
        self.assertNotIn("<b>New</b>", page)
        self.assertIn("&lt;b&gt;New&lt;/b&gt;", page)
        self.assertIn("Waiver claim:", page)
        self.assertIn('name="viewport"', page)

    def test_approximate_banner(self):
        seed(self.conn)
        page = render_html(build_report(self.conn, self.cfg, NOW))
        self.assertIn("Approximate", page)
        self.assertIn("shots", page)

    def test_write_outputs_creates_both_files(self):
        seed(self.conn)
        r = build_report(self.conn, self.cfg, NOW)
        write_outputs(r, self.tmp / "site")
        self.assertEqual(json.loads((self.tmp / "site" / "report.json").read_text())["gameweek"], 9)
        self.assertIn("<html", (self.tmp / "site" / "index.html").read_text())


class ReviewFixReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.conn = connect(":memory:")

    def tearDown(self):
        self._tmp.cleanup()

    def test_rostered_id_missing_from_players_is_listed(self):
        seed(self.conn, roster=("s1", "s2", "s3", "s9"))
        r = build_report(self.conn, self.cfg, NOW)
        ids = [p["sleeper_id"] for p in r["unmatched_rostered"]]
        self.assertIn("s9", ids)
        self.assertEqual(next(p for p in r["unmatched_rostered"] if p["sleeper_id"] == "s9")["flag"], "no data")

    def test_inactive_or_clubless_free_agent_hidden(self):
        seed(self.conn)
        self.conn.execute("UPDATE sleeper_players SET active = 0 WHERE player_id = 's5'")
        r = build_report(self.conn, self.cfg, NOW)
        self.assertNotIn("Cole Palmer", [p["name"] for p in r["free_agents"].get("MID", [])])

    def test_override_to_missing_fpl_id_is_unmatched_not_dropped(self):
        seed(self.conn)
        self.cfg.overrides_path.write_text('{"players": {"s3": 999}}')
        r = build_report(self.conn, self.cfg, NOW)
        self.assertIn("s3", [p["sleeper_id"] for p in r["unmatched_rostered"]])
        self.assertNotIn("s3", [s["drop"]["sleeper_id"] for s in r["swaps"]])

    def test_previous_season_history_ignored(self):
        seed(self.conn)
        self.conn.execute("INSERT INTO fpl_history VALUES ('2025', 2, 100, 30, '2026-05-01T14:00:00Z', 90, ?, ?)",
                          (json.dumps({"goals_scored": 9, "minutes": 90}), TS))
        r = build_report(self.conn, self.cfg, NOW)
        saka = next(s["player"] for s in r["xi"]["starters"] if s["player"]["name"] == "Bukayo Saka")
        self.assertEqual((saka["avg"], saka["n"]), (7.0, 3))

    def test_league_scoring_drift_reported(self):
        seed(self.conn)
        league = {"sport": "s", "roster_positions": ["GK", "MID", "MID"],
                  "scoring_settings": {"goal": 6, "app_60": 2, "shots": 1, "tackle": 1}}
        self.conn.execute("UPDATE sleeper_league SET data_json = ?", (json.dumps(league),))
        r = build_report(self.conn, self.cfg, NOW)
        self.assertIn("tackle", r["coverage"]["missing"])
        self.assertEqual(r["coverage"]["changed"], ["goal"])
        self.assertIn("goal", render_html(r))


if __name__ == "__main__":
    unittest.main()
