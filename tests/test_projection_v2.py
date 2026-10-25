import math
import unittest

from sleeperfpl.minutes import FixtureMinutes
from sleeperfpl.projection_v2 import (V2Inputs, V2mInputs, project_v2, project_v2m, split_points, v2_inputs,
                                      v2m_inputs)

SC = {"pos_d_cs": 6, "pos_d_ga": -2, "pos_d_g": 10, "pos_d_tkw": 1, "pos_f_g": 9, "pos_f_sot": 2}


class ProjectionV2Tests(unittest.TestCase):
    def test_split_points(self):
        self.assertEqual(split_points({"pos_d_cs": 1, "pos_d_ga": 0, "pos_d_g": 1, "pos_d_tkw": 3}, SC), (6, 10, 3))
        self.assertEqual(split_points({"pos_d_ga": 2}, SC), (-4, 0, 0))

    def test_v2_inputs(self):
        weeks = [{"min": 90, "pos_d_cs": 1, "pos_d_tkw": 2}, {"min": 45, "pos_d_g": 1, "pos_d_ga": 1}]
        inp = v2_inputs(weeks, SC, "pos_d_", 5)
        self.assertEqual((inp.p60, inp.att_avg, inp.other_avg, inp.cs_points, inp.ga_points, inp.n),
                         (0.5, 5.0, 1.0, 6, -2, 2))

    def test_v2_inputs_empty(self):
        self.assertIsNone(v2_inputs([], SC, "pos_d_", 5))

    def test_project_v2_defender(self):
        inp = V2Inputs(att_avg=1.0, other_avg=3.0, p60=1.0, cs_points=6, ga_points=-2, n=5)
        self.assertAlmostEqual(project_v2(inp, 1.35, 0.5, 1.35), 6 * math.exp(-0.5) - 1.0 + 1.0 + 3.0, places=4)

    def test_project_v2_attacker_scales_with_team_goals(self):
        inp = V2Inputs(att_avg=4.0, other_avg=1.0, p60=1.0, cs_points=0, ga_points=0, n=5)
        self.assertAlmostEqual(project_v2(inp, 2.7, 1.0, 1.35), 9.0)

    def test_p60_scales_clean_sheet(self):
        inp = V2Inputs(att_avg=0, other_avg=0, p60=0.5, cs_points=8, ga_points=0, n=4)
        self.assertAlmostEqual(project_v2(inp, 1.0, 1.0, 1.35), 4 * math.exp(-1.0))


if __name__ == "__main__":
    unittest.main()


class V2mTests(unittest.TestCase):
    def test_v2m_inputs_per_90(self):
        weeks = [{"min": 90, "pos_d_cs": 1, "pos_d_tkw": 2}, {"min": 45, "pos_d_g": 1, "pos_d_ga": 1}]
        inp = v2m_inputs(weeks, SC, "pos_d_", 5)
        self.assertAlmostEqual(inp.att90, 10 / 135 * 90)
        self.assertAlmostEqual(inp.other90, 2 / 135 * 90)
        self.assertEqual((inp.cs_points, inp.ga_points), (6, -2))
        self.assertIsNone(v2m_inputs([], SC, "pos_d_", 5))

    def test_project_v2m_defender(self):
        full = FixtureMinutes([(90, 1.0)], 1.0, 1.0, 90.0)
        m = V2mInputs(att90=1.0, other90=3.0, cs_points=6, ga_points=-2)
        v2 = project_v2(V2Inputs(att_avg=1.0, other_avg=3.0, p60=1.0, cs_points=6, ga_points=-2, n=5), 1.35, 0.5, 1.35)
        self.assertAlmostEqual(project_v2m(m, full, 1.35, 0.5, 1.35), v2)
        half = FixtureMinutes([(45, 1.0)], 1.0, 0.0, 45.0)
        self.assertAlmostEqual(project_v2m(m, half, 1.35, 0.5, 1.35), -2 * 0.5 * 0.5 + 0.5 + 1.5)
