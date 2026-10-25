import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sleeperfpl import backtest
from sleeperfpl.__main__ import main
from sleeperfpl.backtest import METHODS, format_backtest, run_backtest
from sleeperfpl.db import connect

CFG = SimpleNamespace(difficulty_multipliers={1: 1.2, 2: 1.1, 3: 1.0, 4: 0.9, 5: 0.8}, avg_window=5,
                      category_low=3, category_haul=10, minutes_shrink_k=3, overrides_path=Path("/nonexistent/overrides.json"))


def seed_league(conn, season="2026"):
    conn.execute("INSERT INTO sleeper_league VALUES ('t', 'L1', ?)",
                 (json.dumps({"sport": "s", "season": season, "scoring_settings": {"pos_m_g": 5}}),))
    for i in range(1, 7):
        conn.execute("INSERT INTO sleeper_players VALUES ('t', ?, ?, 'ARS', '[\"M\"]', 'Active', NULL, 1)",
                     (f"p{i}", f"P{i}"))


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        seed_league(self.conn)

    def stats(self, week, pid, goals, season="2025", minutes=90):
        self.conn.execute("INSERT OR REPLACE INTO sleeper_stats VALUES (?, ?, ?, ?, 'x')",
                          (season, week, pid, json.dumps({"min": minutes, "pos_m_g": goals})))

    def teams(self, weeks, season="2025", gp=None):
        """One TEAM_ row per club per week, as Sleeper's weekly stats have."""
        gp = gp or {}
        for week in weeks:
            for t in range(1, 21):
                self.conn.execute("INSERT OR REPLACE INTO sleeper_stats VALUES (?, ?, ?, ?, 'x')",
                                  (season, week, f"TEAM_{t}", json.dumps({"gp": gp.get((week, t), 1)})))

    def spy(self):
        seen = []

        def method(inp, cfg):
            seen.append(inp)
            return 0.0
        return seen, mock.patch.dict(METHODS, {"spy": method})

    def week(self, result, gw, season="2025"):
        return next(w for w in result["seasons"][season]["weeks"] if w["gw"] == gw)

    def test_v1m_prior_from_earlier_weeks_only(self):
        self.teams(range(1, 4))
        for pid, goals in (("p1", 1), ("p2", 0)):
            for w in range(1, 4):
                self.conn.execute("INSERT OR REPLACE INTO sleeper_stats VALUES ('2025', ?, ?, ?, 'x')",
                                  (w, pid, json.dumps({"min": 90, "pos_m_min": 90, "pos_m_g": goals * w})))
        seen, patch = self.spy()
        with patch:
            run_backtest(self.conn, CFG, ["2025"])
        by_gw = {i.gw: i.prior for i in seen if i.sleeper_id == "p1"}
        self.assertAlmostEqual(by_gw[2].lin90, 5 / 2)             # week 1 only: 5 points over 180 minutes
        self.assertAlmostEqual(by_gw[3].lin90, (5 + 10) / 4)      # weeks 1-2, never week 3

    def test_no_future_leak(self):
        self.teams(range(1, 5))
        for w in range(1, 4):
            self.stats(w, "p1", 1)          # 5 points
        self.stats(4, "p1", 10)             # 50 points
        seen, patch = self.spy()
        with patch:
            result = run_backtest(self.conn, CFG, ["2025"])
        for inp in seen:
            self.assertTrue(all(week < inp.gw for week, _ in inp.weeks))
        week4 = next(inp for inp in seen if inp.gw == 4 and inp.sleeper_id == "p1")
        self.assertEqual(week4.history, [5.0, 5.0, 5.0])
        self.assertAlmostEqual(self.week(result, 4)["methods"]["v1"]["mae"], 45.0)

    def test_sample_requires_recent_played_week(self):
        self.teams(range(1, 8))
        for w in range(1, 8):
            self.stats(w, "p1", 1)
        self.stats(1, "p6", 1)
        result = run_backtest(self.conn, CFG, ["2025"])
        self.assertEqual(self.week(result, 3)["n"], 2)
        self.assertEqual(self.week(result, 7)["n"], 1)

    def test_team_rows_ignored_and_irregular_week_skipped(self):
        self.teams(range(1, 5), gp={(3, 3): 2})
        for w in range(1, 5):
            self.stats(w, "p1", 1)
        seen, patch = self.spy()
        with patch:
            result = run_backtest(self.conn, CFG, ["2025"])
        season = result["seasons"]["2025"]
        self.assertEqual(season["irregular_weeks"], [3])
        self.assertEqual([w["gw"] for w in season["weeks"]], [2, 4])
        self.assertFalse(any(inp.sleeper_id.startswith("TEAM_") for inp in seen))

    def test_dnp_scored_zero(self):
        self.teams(range(1, 4))
        for w in range(1, 4):
            self.stats(w, "p1", 1)
        self.stats(1, "p2", 1)
        self.stats(2, "p2", 1)               # no week-3 row for p2
        result = run_backtest(self.conn, CFG, ["2025"])
        self.assertEqual(self.week(result, 3)["methods"]["v1"]["by_cat"]["dnp"]["n"], 1)

    def test_seasons_not_pooled(self):
        self.teams(range(1, 4))
        for w in range(1, 4):
            self.stats(w, "p1", 1)
        for w in (1, 2):
            self.stats(w, "p1", 2, season="2026")
        self.conn.execute("INSERT INTO fpl_events VALUES ('t', 2, '2026-08-20T10:00:00Z', 1)")
        seen, patch = self.spy()
        with patch:
            result = run_backtest(self.conn, CFG)
        self.assertEqual(sorted(result["seasons"]), ["2025", "2026"])
        current = next(inp for inp in seen if inp.season == "2026" and inp.gw == 2)
        self.assertEqual(current.history, [10.0])

    def test_current_season_stops_at_last_finished_week(self):
        for w in (1, 2, 3):
            self.stats(w, "p1", 1, season="2026")
        self.conn.execute("INSERT INTO fpl_events VALUES ('t', 2, '2026-08-20T10:00:00Z', 1)")
        self.conn.execute("INSERT INTO fpl_events VALUES ('t', 3, '2026-08-27T10:00:00Z', 0)")
        result = run_backtest(self.conn, CFG, ["2026"])
        self.assertEqual([w["gw"] for w in result["seasons"]["2026"]["weeks"]], [2])

    def test_summary_counts(self):
        self.teams(range(1, 5))
        for w in range(1, 5):
            for i in range(1, 7):
                self.stats(w, f"p{i}", (i * w) % 5)
        result = run_backtest(self.conn, CFG, ["2025"])
        season = result["seasons"]["2025"]
        both = [w for w in season["weeks"]
                if w["methods"]["v1"]["rank"] is not None and w["methods"]["season_avg"]["rank"] is not None]
        self.assertEqual(season["summary"]["season_avg"]["weeks_compared"], len(both))
        self.assertIn("season_avg vs v1", format_backtest(result))
        self.assertIn("v2 and market numbers (no historical odds stored)", format_backtest(result))


class MinutesBacktestTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(backtest, "MIN_FIT_ROWS", 50)   # the test pool is small
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = connect(":memory:")
        seed_league(self.conn)
        for i in range(30):
            self.conn.execute("INSERT INTO sleeper_players VALUES ('t', ?, ?, 'ARS', '[\"M\"]', 'Active', NULL, 1)",
                              (f"q{i}", f"Q{i}"))

    def pool(self, weeks=range(1, 13), season="2025"):
        """q0-q14 always start and score; q15-q29 come off the bench in odd weeks."""
        for w in weeks:
            for t in range(1, 21):
                self.conn.execute("INSERT OR REPLACE INTO sleeper_stats VALUES (?, ?, ?, ?, 'x')",
                                  (season, w, f"TEAM_{t}", json.dumps({"gp": 1})))
            for i in range(30):
                if i < 15:
                    st = {"min": 90, "gs": 1, "pos_m_g": (i + w) % 3 == 0}
                else:
                    st = {"min": 20 if w % 2 else 0, "pos_m_g": 0}
                self.conn.execute("INSERT OR REPLACE INTO sleeper_stats VALUES (?, ?, ?, ?, 'x')",
                                  (season, w, f"q{i}", json.dumps(st)))

    def test_v1m_no_lookahead(self):
        self.pool()
        seen = []
        real = backtest.fit_rows

        def spy(rows, short, w0=None):
            seen.append(max((r.season, r.week) for r in rows) if rows else None)
            return real(rows, short, w0)
        with mock.patch.object(backtest, "fit_rows", spy), mock.patch.object(backtest, "load_default", lambda: None):
            result = run_backtest(self.conn, CFG, ["2025"])
        weeks = [w["gw"] for w in result["seasons"]["2025"]["weeks"]]
        self.assertEqual(len(seen), len(weeks))
        for latest_row, gw in zip(seen, weeks):
            if latest_row:
                self.assertLess(latest_row, ("2025", gw))
        self.assertTrue(any(w["model"] == "fit" for w in result["seasons"]["2025"]["weeks"]))
        fitted = next(w for w in result["seasons"]["2025"]["weeks"] if w["model"] == "fit")
        self.assertIsNotNone(fitted["methods"]["v1m"]["mae"])

    def test_v1m_skipped_without_model(self):
        self.pool()
        with mock.patch.object(backtest, "fit_rows", lambda *a, **k: None), \
             mock.patch.object(backtest, "load_default", lambda: None):
            result = run_backtest(self.conn, CFG, ["2025"])
        for w in result["seasons"]["2025"]["weeks"]:
            self.assertIsNone(w["methods"]["v1m"]["mae"])
            self.assertIsNotNone(w["methods"]["v1"]["mae"])
            self.assertIsNone(w["model"])

    def test_default_model_not_used_for_its_own_season(self):
        from sleeperfpl.minutes_fit import fit_model
        self.pool()
        self.pool(season="2026")
        self.conn.execute("INSERT INTO fpl_events VALUES ('t', 12, '2026-11-20T10:00:00Z', 1)")
        default = fit_model(self.conn, {f"q{i}": "MID" for i in range(30)}, ["2025"])
        with mock.patch.object(backtest, "fit_rows", lambda *a, **k: None), \
             mock.patch.object(backtest, "load_default", lambda: default):
            result = run_backtest(self.conn, CFG)
        self.assertEqual({w["model"] for w in result["seasons"]["2025"]["weeks"]}, {None})
        self.assertEqual({w["model"] for w in result["seasons"]["2026"]["weeks"]}, {"default"})

    def test_minutes_section_baselines(self):
        self.pool()
        with mock.patch.object(backtest, "load_default", lambda: None):
            result = run_backtest(self.conn, CFG, ["2025"], minutes=True)
        m = result["seasons"]["2025"]["minutes"]
        self.assertGreater(m["n"], 0)
        self.assertLess(m["brier"]["model"]["start"], 0.1)
        self.assertLess(m["brier"]["b2"]["start"], 0.1)
        self.assertIn("b1", m["brier"])
        self.assertTrue(m["reliability"])

    def test_paired_summary_and_format(self):
        self.pool()
        with mock.patch.object(backtest, "load_default", lambda: None):
            result = run_backtest(self.conn, CFG, ["2025"], minutes=True)
        season = result["seasons"]["2025"]
        self.assertEqual(set(season["paired"]), {"mae", "rank"})
        self.assertLessEqual({"n", "mean", "ci", "t", "df", "p_signflip"}, set(season["paired"]["mae"]))
        self.assertIn("mae_oracle", season["oracle"])
        text = format_backtest(result)
        self.assertIn("v1m vs v1", text)
        self.assertIn("minutes error share", text)
        self.assertIn("start-odds score", text)


class BacktestCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = self.tmp / "config.json"
        self.cfg.write_text(json.dumps({"league_id": "L1", "owner": "me", "db_path": "t.db"}))

    def tearDown(self):
        self._tmp.cleanup()

    def test_cli_json_and_no_stats_error(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["--config", str(self.cfg), "backtest"]), 1)
        self.assertIn("error: no Sleeper stats yet; run fetch-history first", err.getvalue())

        conn = connect(self.tmp / "t.db")
        seed_league(conn)
        for w in (1, 2):
            conn.execute("INSERT INTO sleeper_stats VALUES ('2025', ?, 'p1', ?, 'x')",
                         (w, json.dumps({"min": 90, "pos_m_g": 1})))
        conn.commit()
        conn.close()
        out = self.tmp / "bt.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(self.cfg), "backtest", "--json", str(out)]), 0)
        self.assertIn("seasons", json.loads(out.read_text()))


if __name__ == "__main__":
    unittest.main()
