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

    def test_connect_twice_is_idempotent(self):
        connect(self.tmp / "sub" / "t.db").close()

    def test_now_iso_format(self):
        self.assertRegex(now_iso(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


if __name__ == "__main__":
    unittest.main()
