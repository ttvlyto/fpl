import io
import json
import math
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from sleeperfpl import minutes_fit
from sleeperfpl.__main__ import main
from sleeperfpl.db import connect
from sleeperfpl.minutes_fit import (fit_gd, fit_irls, fit_model, load_model, measure_priors, short_rows, solve,
                                    training_rows)


def seed_stats(conn, season="2025", weeks=range(1, 9), players=8, teams=True):
    """Players p0..p7: even ones always start (90), odd ones come on in odd weeks (20)."""
    for w in weeks:
        if teams:
            for t in range(1, 21):
                conn.execute("INSERT INTO sleeper_stats VALUES (?, ?, ?, ?, 'x')",
                             (season, w, f"TEAM_{t}", json.dumps({"gp": 1})))
        for i in range(players):
            st = {"min": 90, "gs": 1} if i % 2 == 0 else ({"min": 20} if w % 2 else {"min": 0})
            st["pos_m_min"] = st["min"]      # Sleeper's per-position keys give the position
            conn.execute("INSERT INTO sleeper_stats VALUES (?, ?, ?, ?, 'x')",
                         (season, w, f"p{i}", json.dumps(st)))


POS = {f"p{i}": ("MID" if i % 4 else "DEF") for i in range(8)}


class FitTests(unittest.TestCase):
    def test_solve_3x3(self):
        x = solve([[2.0, 1.0, -1.0], [-3.0, -1.0, 2.0], [-2.0, 1.0, 2.0]], [8.0, -11.0, -3.0])
        for got, want in zip(x, [2.0, 3.0, -1.0]):
            self.assertAlmostEqual(got, want)

    def test_irls_matches_gd(self):
        rnd = random.Random(3)
        x, y = [], []
        for _ in range(400):
            a, b = rnd.gauss(0, 1), rnd.gauss(0, 1)
            p = 1 / (1 + math.exp(-(0.5 + 1.5 * a - 1.0 * b)))
            x.append([a, b])
            y.append(rnd.random() < p)
        w_irls, _, _ = fit_irls(x, y)
        w_gd = fit_gd(x, y)
        for a, b in zip(w_irls, w_gd):
            self.assertAlmostEqual(a, b, delta=1e-3)

    def test_training_rows_no_lookahead(self):
        conn = connect(":memory:")
        seed_stats(conn)
        rows = training_rows(conn, POS, ["2025"], before=("2025", 5))
        self.assertTrue(rows)
        self.assertTrue(all(r.week < 5 for r in rows))
        self.assertTrue(all(len(r.hist) >= 3 for r in rows))
        self.assertEqual({r.week for r in training_rows(conn, POS, ["2025"])}, {4, 5, 6, 7, 8})

    def test_training_rows_require_recent_play(self):
        conn = connect(":memory:")
        seed_stats(conn, players=2)
        for w in range(1, 9):   # p9 plays weeks 1-2 only, then nothing
            conn.execute("INSERT INTO sleeper_stats VALUES ('2025', ?, 'p9', ?, 'x')",
                         (w, json.dumps({"min": 90, "gs": 1} if w <= 2 else {"min": 0})))
        weeks = {r.week for r in training_rows(conn, {**POS, "p9": "FWD"}, ["2025"]) if r.pid == "p9"}
        self.assertEqual(weeks, {4, 5, 6, 7})     # last played week 2; week 8's last 5 are 3-7

    def test_measure_priors_buckets_sum_to_one(self):
        conn = connect(":memory:")
        seed_stats(conn)
        pri = measure_priors(training_rows(conn, POS, ["2025"]), short_rows(conn, POS, ["2025"]))
        for pos in ("GK", "DEF", "MID", "FWD"):
            self.assertAlmostEqual(sum(p for _, p in pri["start_buckets"][pos]), 1.0)
            self.assertIn(pos, pri["q_prior"])
            self.assertIn(pos, pri["short_prior"])
        self.assertAlmostEqual(sum(p for _, p in pri["sub_buckets"]), 1.0)
        self.assertAlmostEqual(pri["start_buckets"]["MID"][0][1], 1.0)   # every start went 90

    def test_fit_model_learns_starters(self):
        conn = connect(":memory:")
        seed_stats(conn, weeks=range(1, 21), players=40)
        m = fit_model(conn, {f"p{i}": "MID" for i in range(40)}, ["2025"])
        self.assertEqual(m.trained_on, ["2025"])
        self.assertGreater(m.n_rows, 50)
        starter = training_rows(conn, {"p0": "MID"}, ["2025"])[-1].x
        bencher = training_rows(conn, {"p1": "MID"}, ["2025"])[-1].x
        self.assertGreater(m.p_start(starter), 0.9)
        self.assertLess(m.p_start(bencher), 0.1)
        self.assertIsNone(fit_model(conn, {}, ["2025"]))

    def test_load_model_prefers_db_then_file(self):
        conn = connect(":memory:")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "coef.json"
            with mock.patch.object(minutes_fit, "DEFAULT_PATH", path):
                self.assertIsNone(load_model(conn))
                seed_stats(conn, weeks=range(1, 21), players=40)
                m = fit_model(conn, {f"p{i}": "MID" for i in range(40)}, ["2025"])
                path.write_text(json.dumps({**m.to_dict(), "version": "file"}))
                self.assertEqual(load_model(conn).version, "file")
                minutes_fit.save_model(conn, m, "2025:20", None)
                self.assertEqual(load_model(conn).version, m.version)


class FitCliTests(unittest.TestCase):
    def test_fit_minutes_skips_when_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            cfg.write_text(json.dumps({"league_id": "L1", "owner": "me", "db_path": "t.db"}))
            conn = connect(Path(d) / "t.db")
            seed_stats(conn, weeks=range(1, 21), players=40)
            conn.commit()
            conn.close()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["--config", str(cfg), "fit-minutes", "--export", str(Path(d) / "m.json")]), 0)
                self.assertEqual(main(["--config", str(cfg), "fit-minutes"]), 0)
            self.assertIn("rows", out.getvalue())
            self.assertIn("no new weeks since last fit", out.getvalue())
            self.assertEqual(json.loads((Path(d) / "m.json").read_text())["trained_on"], ["2025"])


if __name__ == "__main__":
    unittest.main()
