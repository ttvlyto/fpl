import sqlite3
import tempfile
import unittest
from pathlib import Path

from sleeperfpl.db import connect, latest, latest_fetched_at, meta_get, meta_set, now_iso


class DbTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conn = connect(self.tmp / "sub" / "t.db")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_latest_returns_only_newest_snapshot(self):
        self.conn.execute("INSERT INTO sleeper_trending VALUES ('2026-01-01T00:00:00Z','add','1',5)")
        self.conn.execute("INSERT INTO sleeper_trending VALUES ('2026-01-02T00:00:00Z','add','2',9)")
        rows = latest(self.conn, "sleeper_trending")
        self.assertEqual([r["player_id"] for r in rows], ["2"])
        self.assertEqual(latest_fetched_at(self.conn, "sleeper_trending"), "2026-01-02T00:00:00Z")

    def test_latest_empty_table(self):
        self.assertEqual(latest(self.conn, "sleeper_rosters"), [])
        self.assertIsNone(latest_fetched_at(self.conn, "sleeper_rosters"))

    def test_latest_rejects_unknown_table(self):
        with self.assertRaises(ValueError):
            latest(self.conn, "sleeper_rosters; DROP TABLE meta")

    def test_meta_roundtrip(self):
        self.assertIsNone(meta_get(self.conn, "k"))
        meta_set(self.conn, "k", "v1")
        meta_set(self.conn, "k", "v2")
        self.assertEqual(meta_get(self.conn, "k"), "v2")

    def test_old_database_migrated(self):
        path = self.tmp / "old.db"
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE fpl_elements (fetched_at, fpl_id, first_name, second_name, web_name, team_id, "
                    "element_type, status, chance_next, total_minutes)")
        old.execute("CREATE TABLE projection_log (made_at TEXT, season TEXT, gw INTEGER, sleeper_id TEXT, "
                    "kickoff TEXT, v1 REAL, v2 REAL, p_goal_market REAL)")
        old.execute("INSERT INTO projection_log VALUES ('t', '2026', 9, 'a', NULL, 4, NULL, NULL)")
        old.commit()
        old.close()
        connect(path).close()
        conn = connect(path)
        cols = lambda t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        self.assertIn("ep_next", cols("fpl_elements"))
        self.assertIn("position", cols("projection_log"))
        self.assertIn("ep_next", cols("projection_log"))
        conn.execute("INSERT INTO fpl_elements VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("t", 1, "", "", "", 1, 3, "a", None, 0, 4.5))
        row = conn.execute("SELECT v1, position FROM projection_log").fetchone()
        self.assertEqual((row[0], row[1]), (4, None))
        conn.close()

    def test_connect_twice_is_idempotent(self):
        connect(self.tmp / "sub" / "t.db").close()

    def test_now_iso_format(self):
        self.assertRegex(now_iso(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


if __name__ == "__main__":
    unittest.main()
