import io
import unittest
from contextlib import redirect_stderr

from sleeperfpl import projection
from sleeperfpl.minutes import FixtureMinutes
from sleeperfpl.projection import minutes_rates, project, project_minutes

M = {1: 1.2, 2: 1.1, 3: 1.0, 4: 0.9, 5: 0.8}


class ProjectionTests(unittest.TestCase):
    def test_basic(self):
        p = project([10, 8, 6, 4, 2, 100], [2], False, M)
        self.assertEqual((p.avg, p.n), (6.0, 5))
        self.assertAlmostEqual(p.proj, 6.6)

    def test_zero_appearances(self):
        p = project([], [3], False, M)
        self.assertEqual((p.avg, p.n, p.proj), (None, 0, 0.0))

    def test_fewer_than_window(self):
        p = project([4, 2], [3], False, M)
        self.assertEqual((p.avg, p.n), (3.0, 2))

    def test_blank_gw(self):
        p = project([5, 5], [], False, M)
        self.assertEqual((p.avg, p.proj), (5.0, 0.0))

    def test_double_gw(self):
        self.assertAlmostEqual(project([5] * 5, [1, 5], False, M).proj, 10.0)

    def test_ruled_out(self):
        self.assertEqual(project([9, 9], [1], True, M).proj, 0.0)

    def test_custom_window(self):
        self.assertEqual(project([10, 0, 0], [3], False, M, window=1).avg, 10.0)


if __name__ == "__main__":
    unittest.main()


class MinutesProjectionTests(unittest.TestCase):
    SC = {"pos_d_cs": 6, "pos_d_ga": -2, "pos_d_g": 10, "pos_d_tkw": 1}

    def fm(self, scen):
        return FixtureMinutes(scen, sum(p for m, p in scen if m > 0), sum(p for m, p in scen if m >= 60),
                              sum(m * p for m, p in scen))

    def test_minutes_rates_ratio_of_sums(self):
        weeks = [{"min": 90, "pos_d_tkw": 3, "pos_d_cs": 1}, {"min": 30, "pos_d_g": 1}]
        lin90, cs60 = minutes_rates(weeks, self.SC)
        self.assertAlmostEqual(lin90, 13 / 120 * 90)
        self.assertAlmostEqual(cs60, 6.0)
        self.assertEqual(minutes_rates([], self.SC), (0.0, 0.0))

    def test_project_minutes_threshold(self):
        f = self.fm([(90, 0.5), (20, 0.3), (0, 0.2)])
        self.assertAlmostEqual(project_minutes(9, 6, [f], [1.0]), 0.5 * (9 + 6) + 0.3 * (9 * 20 / 90))

    def test_project_minutes_blank_and_double(self):
        f = self.fm([(90, 0.5), (20, 0.3), (0, 0.2)])
        self.assertEqual(project_minutes(9, 6, [], []), 0.0)
        one = project_minutes(9, 6, [f], [1.0])
        self.assertAlmostEqual(project_minutes(9, 6, [f, f], [1.2, 0.8]), 2 * one)

    def test_unknown_code_warns_once(self):
        projection._warned.clear()
        err = io.StringIO()
        with redirect_stderr(err):
            minutes_rates([{"min": 90, "pos_d_zzz": 1}], {"pos_d_zzz": 1})
            minutes_rates([{"min": 90, "pos_d_zzz": 1}], {"pos_d_zzz": 1})
        self.assertEqual(err.getvalue().count("zzz"), 1)
