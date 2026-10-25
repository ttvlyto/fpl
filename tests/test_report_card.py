import json
import unittest

from sleeperfpl.db import connect
from sleeperfpl.lineup import Lineup, Swap
from sleeperfpl.models import Fixture, PlayerCtx
from sleeperfpl.pricing import FixtureOdds
from sleeperfpl.report_card import (log_goal_odds, log_matches, log_projections, log_recommendation, score_decisions,
                                    score_matches, score_weeks, summary)

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

    def test_zero_minute_and_missing_players_scored(self):
        log_projections(self.conn, "2026", 9, [pl("a", 4), pl("b", 4), pl("c", 4)], "2026-10-23T10:00:00Z")
        self.stats("a", {"min": 0})
        self.stats("c", {"min": 90, "pos_m_g": 1})          # 5; b has no stats row at all
        week = score_weeks(self.conn, "2026", SC, 9)[0]
        self.assertEqual((week["n"], week["n_played"]), (3, 1))
        self.assertAlmostEqual(week["mae_v1"], (4 + 4 + 1) / 3)
        self.assertAlmostEqual(week["mae_v1_played"], 1.0)
        self.assertEqual(week["by_cat_v1"]["dnp"]["n"], 2)

    def test_categories_use_cutoffs(self):
        log_projections(self.conn, "2026", 9, [pl("a", 4)], "2026-10-23T10:00:00Z")
        self.stats("a", {"min": 90, "pos_m_g": 2.4})        # 12
        self.assertEqual(score_weeks(self.conn, "2026", SC, 9)[0]["by_cat_v1"]["haul"]["n"], 1)
        self.assertEqual(score_weeks(self.conn, "2026", SC, 9, haul=15)[0]["by_cat_v1"]["mid"]["n"], 1)

    def test_rank_v1(self):
        log_projections(self.conn, "2026", 9, [pl(f"p{i}", i) for i in range(1, 6)], "2026-10-23T10:00:00Z")
        for i in range(1, 6):
            self.stats(f"p{i}", {"min": 90, "pos_m_g": i})
        self.assertAlmostEqual(score_weeks(self.conn, "2026", SC, 9)[0]["rank_v1"], 1.0)
        self.conn.execute("DELETE FROM projection_log WHERE sleeper_id = 'p5'")
        self.assertIsNone(score_weeks(self.conn, "2026", SC, 9)[0]["rank_v1"])

    def test_rank_ep_paired(self):
        players = []
        for i in range(1, 6):
            p = pl(f"p{i}", i)
            p.ep_next = 6 - i
            players.append(p)
            self.stats(f"p{i}", {"min": 90, "pos_m_g": i})
        log_projections(self.conn, "2026", 9, players, "2026-10-23T10:00:00Z")
        week = score_weeks(self.conn, "2026", SC, 9)[0]
        self.assertEqual(week["n_ep"], 5)
        self.assertAlmostEqual(week["rank_ep"], -1.0)
        self.assertAlmostEqual(week["rank_v1_ep"], 1.0)

    def test_old_rows_without_position_still_score(self):
        self.conn.execute("INSERT INTO projection_log (made_at, season, gw, sleeper_id, kickoff, v1, v2, p_goal_market) "
                          "VALUES ('2026-10-23T10:00:00Z', '2026', 9, 'a', ?, 4, NULL, NULL)", (KO,))
        self.stats("a", {"min": 90, "pos_m_g": 1})
        week = score_weeks(self.conn, "2026", SC, 9)[0]
        self.assertEqual(week["n"], 1)
        self.assertIsNone(week["rank_v1"])

    def test_week_without_stats_not_scored(self):
        log_projections(self.conn, "2026", 9, [pl("a", 4)], "2026-10-23T10:00:00Z")
        self.assertEqual(score_weeks(self.conn, "2026", SC, 9), [])     # stats not fetched yet: not all-zero

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


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def test_log_projections_position_and_ep_next(self):
        p = pl("a", 4)
        p.ep_next = 3.5
        log_projections(self.conn, "2026", 9, [p], "2026-10-23T10:00:00Z")
        row = self.conn.execute("SELECT position, ep_next FROM projection_log").fetchone()
        self.assertEqual((row[0], row[1]), ("MID", 3.5))

    def test_log_projections_minutes_columns(self):
        p = pl("a", 4)
        p.proj_v1m, p.p_start, p.p_app, p.p60, p.e_min = 3.2, 0.7, 0.8, 0.6, 60.0
        p.fpl_bucket, p.slp_bucket, p.p_start_model, p.p_app_model = "a100", "out", 0.875, 0.9
        p.p_start_fpl, p.p_app_fpl = 0.875, 0.9
        log_projections(self.conn, "2026", 9, [p], "2026-10-23T10:00:00Z", label_at="2026-10-23T06:00:00Z")
        row = self.conn.execute("SELECT v1m, v2m, p_start, fpl_bucket, slp_bucket, p_start_model, p_app_fpl, label_at "
                                "FROM projection_log").fetchone()
        self.assertEqual(tuple(row), (3.2, None, 0.7, "a100", "out", 0.875, 0.9, "2026-10-23T06:00:00Z"))

    def test_log_recommendation(self):
        lineup = Lineup([("M", pl("a", 4)), ("M", None)], [], 4.0, ["M"])
        swaps = [Swap(pl("b", 1), pl("c", 5), 4.0, "xi")]
        log_recommendation(self.conn, "2026", 9, KO, 3, lineup, swaps, "2026-10-23T10:00:00Z")
        row = self.conn.execute("SELECT first_kickoff, roster_id, xi_json, swaps_json FROM recommendation_log").fetchone()
        self.assertEqual((row[0], row[1]), (KO, 3))
        self.assertEqual(json.loads(row[2]), ["a"])
        self.assertEqual(json.loads(row[3]), [["b", "c"]])

    def test_log_matches_target_week_only(self):
        odds = FixtureOdds("ARS", "CHE", 1.8, 1.0, 0.5, 0.3, 0.2, 0.37, 0.17, n_quotes=4, avg_spread=0.02)
        fodds = {50: odds, 51: odds}
        fixtures = [{"fixture_id": 50, "event": 9, "kickoff_time": KO},
                    {"fixture_id": 51, "event": 10, "kickoff_time": "2026-10-31T14:00:00Z"}]
        self.assertEqual(log_matches(self.conn, "2026", 9, fodds, fixtures, "2026-10-23T10:00:00Z"), 1)
        row = self.conn.execute("SELECT fixture_id, kickoff, method, lam_home, cs_away, n_quotes, avg_spread "
                                "FROM match_log").fetchone()
        self.assertEqual(tuple(row), (50, KO, "market", 1.8, 0.17, 4, 0.02))


class DecisionTests(unittest.TestCase):
    SLOTS, ELIG = ["M", "M"], {"M": ["M"]}

    def setUp(self):
        self.conn = connect(":memory:")
        for pid, g in (("a", 0.4), ("b", 1), ("c", 1.6), ("d", 0.6)):     # 2, 5, 8 and 3 points
            self.conn.execute("INSERT INTO sleeper_players VALUES ('t', ?, ?, 'ARS', '[\"M\"]', 'Active', NULL, 1)",
                              (pid, pid.upper()))
            self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 9, ?, ?, 'x')",
                              (pid, json.dumps({"min": 90, "pos_m_g": g})))

    def roster(self, starters, fetched_at="2026-10-23T20:00:00Z"):
        self.conn.execute("INSERT INTO sleeper_rosters VALUES (?, 3, 'u1', ?, ?, '[]')",
                          (fetched_at, json.dumps(["a", "b", "c"]), json.dumps(starters)))

    def recommend(self, xi, swaps=(), made_at="2026-10-23T10:00:00Z"):
        self.conn.execute("INSERT INTO recommendation_log VALUES (?, '2026', 9, ?, 3, ?, ?)",
                          (made_at, KO, json.dumps(xi), json.dumps([list(s) for s in swaps])))

    def score(self):
        return score_decisions(self.conn, "2026", SC, 9, self.SLOTS, self.ELIG)

    def test_decisions_compare_set_tool_best(self):
        self.roster(["a", "b"])
        self.recommend(["b", "c"], [("a", "d")])
        d = self.score()[0]
        self.assertEqual(d["gw"], 9)
        self.assertAlmostEqual(d["set_pts"], 7)
        self.assertAlmostEqual(d["tool_pts"], 13)
        self.assertAlmostEqual(d["best_pts"], 13)
        swap = d["swaps"][0]
        self.assertEqual((swap["drop"], swap["add"]), ("A", "D"))
        self.assertAlmostEqual(swap["diff"], 1)

    def test_recommendation_after_kickoff_ignored(self):
        self.roster(["a", "b"])
        self.recommend(["b", "c"], made_at="2026-10-24T15:00:00Z")
        self.assertEqual(self.score(), [])

    def test_no_roster_snapshot_before_kickoff(self):
        self.roster(["a", "b"], fetched_at="2026-10-24T15:00:00Z")
        self.recommend(["b", "c"])
        self.assertEqual(self.score(), [])

    def test_week_without_stats_skipped(self):
        self.conn.execute("DELETE FROM sleeper_stats")
        self.roster(["a", "b"])
        self.recommend(["b", "c"])
        self.assertEqual(self.score(), [])

    def test_empty_slot_id_ignored(self):
        self.roster(["a", "0"])
        self.recommend(["b", "c"])
        self.assertAlmostEqual(self.score()[0]["set_pts"], 2)

    def test_summary_prefers_decisions(self):
        decisions = [{"gw": 8, "set_pts": 5, "tool_pts": 9}, {"gw": 9, "set_pts": 9, "tool_pts": 5}]
        self.assertEqual(summary([], decisions), "the tool's XI beat your lineup in 1 of 2 weeks")
        self.assertEqual(summary([], decisions[:1]), "not enough weeks yet")


class MatchScoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def fixture(self, h=1, a=0, finished=1):
        self.conn.execute("INSERT INTO fpl_fixtures VALUES ('t', 50, 9, 1, 2, 3, 3, ?, ?, ?, ?)", (KO, finished, h, a))

    def log(self, made_at="2026-10-23T10:00:00Z"):
        self.conn.execute("INSERT INTO match_log VALUES (?, '2026', 9, 50, ?, 'market', 1.2, 0.9, 0.5, 0.3, 0.2, "
                          "0.4, 0.3, 4, 0.02)", (made_at, KO))

    def test_score_matches(self):
        self.fixture()
        self.log()
        m = score_matches(self.conn, "2026", 9)[0]
        self.assertEqual((m["gw"], m["n"]), (9, 1))
        self.assertAlmostEqual(m["log_loss"], 0.6931, places=4)
        self.assertAlmostEqual(m["rps"], 0.145)
        self.assertAlmostEqual(m["brier_cs"], 0.225)

    def test_finished_without_scores_skipped(self):
        self.fixture(None, None)
        self.log()
        self.assertEqual(score_matches(self.conn, "2026", 9), [])

    def test_log_after_kickoff_ignored(self):
        self.fixture()
        self.log("2026-10-24T15:00:00Z")
        self.assertEqual(score_matches(self.conn, "2026", 9), [])


class PairedComparisonTests(unittest.TestCase):
    def test_v1_compared_on_same_players_as_v2(self):
        conn = connect(":memory:")
        log_projections(conn, "2026", 9, [pl("a", 4, 5), pl("b", 0)], "2026-10-23T10:00:00Z")   # b: no v2
        for pid, g in (("a", 1), ("b", 2)):
            conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 9, ?, ?, 'x')",
                         (pid, json.dumps({"min": 90, "pos_m_g": g})))
        week = score_weeks(conn, "2026", SC, 9)[0]
        self.assertAlmostEqual(week["mae_v1"], 5.5)          # all players
        self.assertAlmostEqual(week["mae_v1_paired"], 1.0)   # only players with v2
        self.assertAlmostEqual(week["mae_v2"], 0.0)

    def test_score_weeks_v1m_paired(self):
        conn = connect(":memory:")
        a, b, c = pl("a", 4), pl("b", 0), pl("c", 2)
        a.proj_v1m, b.proj_v1m = 6.0, 1.0           # c: no minutes estimate
        log_projections(conn, "2026", 9, [a, b, c], "2026-10-23T10:00:00Z")
        for pid, g in (("a", 1), ("b", 0), ("c", 0)):
            conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 9, ?, ?, 'x')",
                         (pid, json.dumps({"min": 90, "pos_m_g": g})))
        week = score_weeks(conn, "2026", SC, 9)[0]
        self.assertEqual(week["n_v1m"], 2)
        self.assertAlmostEqual(week["mae_v1m"], (1.0 + 1.0) / 2)
        self.assertAlmostEqual(week["mae_v1_v1m"], (1.0 + 0.0) / 2)   # v1 on the same two players
        self.assertEqual(week["n_v2m"], 0)

    def test_brier_start_counts_dnp(self):
        conn = connect(":memory:")
        a = pl("a", 4)
        a.p_start, a.p_app = 0.9, 0.95
        log_projections(conn, "2026", 9, [a], "2026-10-23T10:00:00Z")
        conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 9, 'other', '{}', 'x')")   # week fetched; a absent
        week = score_weeks(conn, "2026", SC, 9)[0]
        self.assertAlmostEqual(week["brier_start"], 0.81)
        self.assertAlmostEqual(week["brier_app"], 0.9025)
        self.assertEqual(week["n_minutes"], 1)

    def test_summary_uses_paired(self):
        weeks = [{"gw": 8, "mae_v1": 9.0, "mae_v1_paired": 1.0, "mae_v2": 2.0, "n_v2": 5},
                 {"gw": 9, "mae_v1": 9.0, "mae_v1_paired": 1.0, "mae_v2": 2.0, "n_v2": 5}]
        self.assertEqual(summary(weeks), "v2 (market) beat v1 (difficulty) in 0 of 2 weeks")

    def test_goal_odds_row_without_projection(self):
        conn = connect(":memory:")
        log_projections(conn, "2026", 9, [pl("a", 4)], "2026-10-23T10:00:00Z")
        log_goal_odds(conn, "2026", 9, [("a", KO, 0.6)], "2026-10-24T11:00:00Z")
        conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 9, 'a', ?, 'x')",
                     (json.dumps({"min": 90, "g": 1, "pos_m_g": 1}),))
        week = score_weeks(conn, "2026", SC, 9)[0]
        self.assertAlmostEqual(week["mae_v1"], 1.0)
        self.assertAlmostEqual(week["brier_goal"], 0.16)


if __name__ == "__main__":
    unittest.main()
