import json
import unittest

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
