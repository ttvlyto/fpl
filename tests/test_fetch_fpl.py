import json
import unittest

from sleeperfpl.db import connect, latest, meta_get, meta_set
from sleeperfpl.fetch_fpl import fetch_bootstrap, fetch_fixtures, fetch_history
from sleeperfpl.http import FetchError


def bootstrap(finished_up_to=9):
    return {
        "elements": [
            {"id": 1, "first_name": "Bukayo", "second_name": "Saka", "web_name": "Saka", "team": 1,
             "element_type": 3, "status": "a", "chance_of_playing_next_round": None, "minutes": 700},
            {"id": 2, "first_name": "Bench", "second_name": "Warmer", "web_name": "Warmer", "team": 1,
             "element_type": 2, "status": "a", "chance_of_playing_next_round": None, "minutes": 0},
        ],
        "teams": [{"id": 1, "short_name": "ARS", "name": "Arsenal"}],
        "events": [{"id": i, "deadline_time": f"2026-08-{i:02d}T10:00:00Z", "finished": i <= finished_up_to}
                   for i in range(1, 12)],
    }


def history_row(fixture, minutes=90, rnd=9):
    return {"element": 1, "fixture": fixture, "round": rnd, "kickoff_time": "2026-10-18T14:00:00Z",
            "minutes": minutes, "goals_scored": 1}


class Recorder:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        path = url.split("/api/", 1)[1]
        value = self.responses.get(path)
        if value is None or isinstance(value, Exception):
            raise value or FetchError(path)
        return value


class FetchFplTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def seed(self, finished_up_to=9):
        fetch_bootstrap(self.conn, get=Recorder({"bootstrap-static/": bootstrap(finished_up_to)}))

    def test_bootstrap_stores_teams_and_events(self):
        self.seed()
        self.assertEqual(latest(self.conn, "fpl_teams")[0]["short_name"], "ARS")
        self.assertEqual(len(latest(self.conn, "fpl_events")), 11)
        self.assertEqual(len(latest(self.conn, "fpl_elements")), 2)

    def test_fixtures_stored(self):
        n = fetch_fixtures(self.conn, get=Recorder({"fixtures/": [
            {"id": 5, "event": 10, "team_h": 1, "team_a": 2, "team_h_difficulty": 2,
             "team_a_difficulty": 4, "kickoff_time": "2026-10-25T14:00:00Z", "finished": False}]}))
        self.assertEqual(n, 1)
        self.assertEqual(latest(self.conn, "fpl_fixtures")[0]["team_a_difficulty"], 4)

    def test_history_skipped_when_no_new_finished_gw(self):
        self.seed(finished_up_to=8)
        meta_set(self.conn, "history_last_gw", "8")
        rec = Recorder({})
        self.assertEqual(fetch_history(self.conn, interval=0, get=rec), 0)
        self.assertEqual(rec.calls, [])

    def test_history_runs_and_advances_meta(self):
        self.seed(finished_up_to=9)
        meta_set(self.conn, "history_last_gw", "8")
        rec = Recorder({"element-summary/1/": {"history": [history_row(100)]}})
        self.assertEqual(fetch_history(self.conn, interval=0, get=rec), 1)
        self.assertEqual(len(rec.calls), 1)
        self.assertEqual(meta_get(self.conn, "history_last_gw"), "9")
        row = self.conn.execute("SELECT * FROM fpl_history").fetchone()
        self.assertEqual(json.loads(row["stats_json"]), history_row(100))

    def test_history_partial_failure_does_not_advance(self):
        b = bootstrap(9)
        b["elements"][1]["minutes"] = 50
        fetch_bootstrap(self.conn, get=Recorder({"bootstrap-static/": b}))
        meta_set(self.conn, "history_last_gw", "8")
        rec = Recorder({"element-summary/1/": {"history": [history_row(100)]},
                        "element-summary/2/": FetchError("boom")})
        fetch_history(self.conn, interval=0, get=rec)
        self.assertEqual(meta_get(self.conn, "history_last_gw"), "8")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fpl_history").fetchone()[0], 1)

    def test_history_upsert_replaces_row(self):
        self.seed()
        fetch_history(self.conn, force=True, interval=0,
                      get=Recorder({"element-summary/1/": {"history": [history_row(100, minutes=10)]}}))
        fetch_history(self.conn, force=True, interval=0,
                      get=Recorder({"element-summary/1/": {"history": [history_row(100, minutes=90)]}}))
        rows = self.conn.execute("SELECT minutes FROM fpl_history").fetchall()
        self.assertEqual([r[0] for r in rows], [90])


class SeasonTests(unittest.TestCase):
    def test_history_rows_kept_per_season(self):
        conn = connect(":memory:")
        fetch_bootstrap(conn, get=Recorder({"bootstrap-static/": bootstrap(9)}))
        conn.execute("INSERT INTO fpl_history VALUES ('2025', 1, 100, 9, '2025-10-18T14:00:00Z', 45, '{}', 'x')")
        fetch_history(conn, force=True, interval=0,
                      get=Recorder({"element-summary/1/": {"history": [history_row(100)]}}))
        rows = conn.execute("SELECT season, minutes FROM fpl_history ORDER BY season").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("2025", 45), ("2026", 90)])


if __name__ == "__main__":
    unittest.main()
