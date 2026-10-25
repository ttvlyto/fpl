import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sleeperfpl import second_opinion as so
from sleeperfpl.db import connect
from sleeperfpl.report import BuildError
from tests.test_report import NOW, TS, make_cfg, seed

LEAGUE = {"sport": "s", "season": "2026", "roster_positions": ["GK", "MID", "MID", "BN", "BN"],
          "scoring_settings": {"pos_m_g": 5}}


def seed_sleeper(conn):
    """The report test data plus a 2026 league and Sleeper stats for weeks 6-8 (Saka misses week 7)."""
    seed(conn)
    conn.execute("INSERT INTO sleeper_league VALUES (?,?,?)", ("2026-10-22T07:00:00Z", "L1", json.dumps(LEAGUE)))
    rows = []
    for pid, goals in (("s1", 0), ("s2", 1), ("s3", 0), ("s5", 2), ("s6", 0)):
        for week in (6, 7, 8):
            if pid == "s2" and week == 7:
                continue
            rows.append(("2026", week, pid, json.dumps({"min": 90, "pos_m_g": goals}), TS))
    conn.executemany("INSERT INTO sleeper_stats VALUES (?,?,?,?,?)", rows)


class PacketTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self._tmp.name))
        self.cfg.stats_source = "sleeper"
        self.conn = connect(":memory:")
        seed_sleeper(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_packet_has_no_tool_projections_or_picks(self):
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        text = json.dumps(packet)
        for word in ('"proj', '"p_start', '"e_min', '"ep_next', '"avg', '"p60', '"value'):
            self.assertNotIn(word, text)
        self.assertNotIn("xi", packet)
        self.assertNotIn("swaps", packet)
        self.assertEqual(packet["gameweek"], 9)
        self.assertEqual(packet["adds_are"], "free agent pickups")
        self.assertEqual(packet["slots"], ["GK", "MID", "MID"])

    def test_recent_weeks_include_zero_minute_weeks(self):
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        saka = next(p for p in packet["roster"] if p["id"] == "s2")
        self.assertIn({"gw": 7, "pts": 0, "min": 0}, saka["recent"])
        self.assertIn({"gw": 8, "pts": 5.0, "min": 90}, saka["recent"])
        self.assertEqual(saka["season"], {"apps": 2, "minutes": 180, "pts": 10.0})

    def test_trending_free_agent_always_included(self):
        with mock.patch.object(so, "FA_PER_POSITION", 0):
            packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        self.assertEqual([p["id"] for p in packet["free_agents"]], ["s5"])
        self.assertEqual(packet["free_agents"][0]["trending_adds"], 40)

    def test_free_agents_ranked_by_recent_points(self):
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        self.assertEqual({p["id"] for p in packet["free_agents"]}, {"s5", "s6"})

    def test_roster_flags_and_unmatched_left_out(self):
        self.cfg.undroppable = ["Bukayo Saka"]
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        ids = [p["id"] for p in packet["roster"]]
        self.assertNotIn("s4", ids)   # no FPL match
        saka = next(p for p in packet["roster"] if p["id"] == "s2")
        self.assertTrue(saka["undroppable"])
        self.assertFalse(saka["ir"])

    def test_fixture_carries_head_to_head(self):
        self.conn.execute("INSERT INTO match_results VALUES (?,?,?,?,?,?)", ("2025", "2025-12-01", "Arsenal", "Chelsea", 3, 0))
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        f = packet["fixtures"][0]
        self.assertEqual((f["home"], f["away"]), ("ARS", "CHE"))
        self.assertEqual(len(f["head_to_head"]["meetings"]), 1)
        self.assertEqual(f["head_to_head"]["a_wins"], 1)
        self.assertIsNone(f["p_home"])

    def test_no_target_week_raises_build_error(self):
        self.conn.execute("UPDATE fpl_events SET finished = 1")
        with self.assertRaisesRegex(BuildError, "game week"):
            so.build_packet(self.conn, self.cfg, NOW)

    def test_no_sleeper_stats_raises_build_error(self):
        self.conn.execute("DELETE FROM sleeper_stats")
        with self.assertRaisesRegex(BuildError, "Sleeper stats"):
            so.build_packet(self.conn, self.cfg, NOW)


if __name__ == "__main__":
    unittest.main()
