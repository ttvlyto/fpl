import io
import json
import unittest
from contextlib import redirect_stdout

from sleeperfpl.db import connect, meta_get
from sleeperfpl.http import FetchError
from sleeperfpl.sleeper_stats import fetch_sleeper_stats, sleeper_history, sleeper_points, sleeper_weeks

SCORING = {"pos_f_g": 9, "pos_f_sot": 2, "pos_m_g": 9, "pos_f_min": 0}


def getter(weeks, fail=()):
    calls = []

    def get(url, **kw):
        week = int(url.rsplit("/", 1)[1])
        calls.append(week)
        if week in fail:
            raise FetchError("down")
        return weeks.get(week, {})
    get.calls = calls
    return get


class SleeperStatsTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def test_points_use_league_scoring_exactly(self):
        self.assertEqual(sleeper_points({"pos_f_g": 2, "pos_f_sot": 3, "g": 2, "min": 90}, SCORING), 24)
        self.assertEqual(sleeper_points({}, SCORING), 0)

    def test_fetch_all_finished_weeks_then_only_new_and_latest(self):
        weeks = {1: {"p1": {"pos_f_g": 1, "min": 90}}, 2: {"p1": {"pos_f_g": 0, "min": 90}},
                 3: {"p1": {"pos_f_g": 2, "min": 90}}}
        g = getter(weeks)
        self.assertEqual(fetch_sleeper_stats(self.conn, "s", "2026", 2, get=g), 2)
        self.assertEqual(g.calls, [1, 2])
        g2 = getter(weeks)
        fetch_sleeper_stats(self.conn, "s", "2026", 3, get=g2)
        self.assertEqual(g2.calls, [2, 3])   # latest stored week refreshed for stat corrections, plus the new one
        self.assertEqual(meta_get(self.conn, "sleeper_stats_week"), "3")

    def test_failed_week_is_retried_next_run(self):
        weeks = {1: {"p1": {"min": 90}}, 2: {"p1": {"min": 90}}}
        fetch_sleeper_stats(self.conn, "s", "2026", 2, get=getter(weeks, fail={2}))
        g = getter(weeks)
        fetch_sleeper_stats(self.conn, "s", "2026", 2, get=g)
        self.assertIn(2, g.calls)

    def test_history_most_recent_first_and_skips_unplayed(self):
        weeks = {1: {"p1": {"pos_f_g": 1, "min": 90}}, 2: {"p1": {"min": 0}}, 3: {"p1": {"pos_f_sot": 1, "min": 30}}}
        fetch_sleeper_stats(self.conn, "s", "2026", 3, get=getter(weeks))
        self.assertEqual(sleeper_history(self.conn, "2026", "p1", SCORING), [2, 9])
        self.assertEqual(sleeper_history(self.conn, "2025", "p1", SCORING), [])


class MaxWeekTests(unittest.TestCase):
    def test_history_and_weeks_respect_max_week(self):
        conn = connect(":memory:")
        weeks = {7: {"p1": {"pos_f_g": 1, "min": 90}}, 8: {"p1": {"pos_f_g": 2, "min": 90}}}
        fetch_sleeper_stats(conn, "s", "2026", 8, get=getter(weeks))
        self.assertEqual(sleeper_history(conn, "2026", "p1", SCORING, max_week=7), [9])
        self.assertEqual(len(sleeper_history(conn, "2026", "p1", SCORING)), 2)
        self.assertEqual(sleeper_weeks(conn, "2026", "p1", 7), [{"pos_f_g": 1, "min": 90}])


if __name__ == "__main__":
    unittest.main()


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def test_backfill_leaves_meta(self):
        from sleeperfpl.db import meta_set
        meta_set(self.conn, "sleeper_stats_week", "8")
        get = getter({1: {"p1": {"min": 90}}, 2: {"p1": {"min": 45}}})
        self.assertEqual(fetch_sleeper_stats(self.conn, "s", "2025", 2, update_meta=False, get=get), 2)
        n = self.conn.execute("SELECT COUNT(DISTINCT week) FROM sleeper_stats WHERE season = '2025'").fetchone()[0]
        self.assertEqual(n, 2)
        self.assertEqual(meta_get(self.conn, "sleeper_stats_week"), "8")

    def _league(self, season="2026"):
        self.conn.execute("INSERT INTO sleeper_league VALUES ('t', 'L1', ?)",
                          (json.dumps({"sport": "s", "season": season}),))

    def test_cli_season_skips_fpl(self):
        from unittest import mock
        import sleeperfpl.__main__ as cli
        self._league()
        stats, fpl = mock.Mock(return_value=38), mock.Mock()
        with mock.patch.object(cli, "fetch_sleeper_stats", stats), mock.patch.object(cli, "fetch_history", fpl), \
             redirect_stdout(io.StringIO()):
            cli.cmd_fetch_history(mock.Mock(), self.conn, mock.Mock(force=False, season="2025"))
        fpl.assert_not_called()
        args, kwargs = stats.call_args
        self.assertEqual((args[2], args[3], kwargs.get("update_meta")), ("2025", 38, False))

    def test_cli_current_season_rejected(self):
        from unittest import mock
        import sleeperfpl.__main__ as cli
        from sleeperfpl.report import BuildError
        self._league()
        with self.assertRaisesRegex(BuildError, "--season is for past seasons"):
            cli.cmd_fetch_history(mock.Mock(), self.conn, mock.Mock(force=False, season="2026"))
