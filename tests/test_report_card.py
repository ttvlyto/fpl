import json
import unittest

from sleeperfpl.db import connect
from sleeperfpl.models import Fixture, PlayerCtx
from sleeperfpl.report_card import log_projections, score_weeks, summary

SC = {"pos_m_g": 5}
KO = "2026-10-24T14:00:00Z"


def pl(sid, v1, v2=None, p_goal=None):
    p = PlayerCtx(sleeper_id=sid, name=sid, positions=["M"], club="ARS", fpl_id=1, proj=v2 if v2 is not None else v1,
                  proj_v1=v1, proj_source="market" if v2 is not None else "difficulty",
                  fixtures=[Fixture("CHE", True, 3, KO, 50)])
    p.p_goal_market = p_goal
    return p


class ReportCardTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def stats(self, pid, st, week=9):
        self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', ?, ?, ?, 'x')", (week, pid, json.dumps(st)))

    def test_log_and_score(self):
        log_projections(self.conn, "2026", 9, [pl("a", 4, 5), pl("b", 6, 6)], "2026-10-23T10:00:00Z")
        self.stats("a", {"min": 90, "pos_m_g": 1})            # 5
        self.stats("b", {"min": 90, "pos_m_g": 1.6})          # 8
        week = score_weeks(self.conn, "2026", SC, 9)[0]
        self.assertEqual((week["gw"], week["n"], week["n_v2"]), (9, 2, 2))
        self.assertAlmostEqual(week["mae_v1"], 1.5)
        self.assertAlmostEqual(week["mae_v2"], 1.0)

    def test_uses_last_row_before_kickoff(self):
        log_projections(self.conn, "2026", 9, [pl("a", 1)], "2026-10-23T10:00:00Z")
        log_projections(self.conn, "2026", 9, [pl("a", 3)], "2026-10-24T09:00:00Z")
        log_projections(self.conn, "2026", 9, [pl("a", 99)], "2026-10-24T15:00:00Z")   # after kickoff
        self.stats("a", {"min": 90, "pos_m_g": 1})
        self.assertAlmostEqual(score_weeks(self.conn, "2026", SC, 9)[0]["mae_v1"], 2.0)

    def test_zero_minute_players_excluded(self):
        log_projections(self.conn, "2026", 9, [pl("a", 4), pl("b", 4)], "2026-10-23T10:00:00Z")
        self.stats("a", {"min": 0})
        self.stats("b", {"min": 90, "pos_m_g": 1})
        self.assertEqual(score_weeks(self.conn, "2026", SC, 9)[0]["n"], 1)

    def test_brier(self):
        log_projections(self.conn, "2026", 9, [pl("a", 4, p_goal=0.6)], "2026-10-23T10:00:00Z")
        self.stats("a", {"min": 90, "g": 1, "pos_m_g": 1})
        week = score_weeks(self.conn, "2026", SC, 9)[0]
        self.assertAlmostEqual(week["brier_goal"], 0.16)
        self.assertEqual(week["n_goal"], 1)

    def test_unfinished_week_not_scored(self):
        log_projections(self.conn, "2026", 10, [pl("a", 4)], "2026-10-30T10:00:00Z")
        self.stats("a", {"min": 90}, week=10)
        self.assertEqual(score_weeks(self.conn, "2026", SC, 9), [])

    def test_summary(self):
        weeks = [{"gw": 8, "mae_v1": 3.0, "mae_v2": 2.0, "n_v2": 5}, {"gw": 9, "mae_v1": 2.0, "mae_v2": 2.5, "n_v2": 5}]
        self.assertEqual(summary(weeks), "v2 (market) beat v1 (difficulty) in 1 of 2 weeks")
        self.assertEqual(summary(weeks[:1]), "not enough weeks yet")


if __name__ == "__main__":
    unittest.main()
