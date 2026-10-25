import math
import unittest

from sleeperfpl.metrics import (Scored, brier, category, error_summary, log_loss, mean, mean_ci, paired_t,
                                position_group, rank_by_position, reliability, rps, signflip_p, spearman, wilson)


class MetricsTests(unittest.TestCase):
    def test_mean(self):
        self.assertIsNone(mean([]))
        self.assertAlmostEqual(mean([1, 2]), 1.5)

    def test_category(self):
        self.assertEqual(category(None, 0, 3, 10), "dnp")
        self.assertEqual(category({"min": 0}, 0, 3, 10), "dnp")
        self.assertEqual(category({"min": 90}, -1, 3, 10), "low")
        self.assertEqual(category({"min": 90}, 2.9, 3, 10), "low")
        self.assertEqual(category({"min": 90}, 3, 3, 10), "mid")
        self.assertEqual(category({"min": 90}, 10, 3, 10), "haul")

    def test_position_group(self):
        self.assertEqual(position_group(["D", "M"]), "DEF")
        self.assertEqual(position_group(["FWD"]), "FWD")
        self.assertIsNone(position_group([]))

    def test_error_summary(self):
        s = error_summary([Scored("MID", 4, 0, "dnp"), Scored("MID", 4, 5, "mid"), Scored("FWD", 2, 12, "haul")])
        self.assertEqual((s["n"], s["n_played"]), (3, 2))
        self.assertAlmostEqual(s["mae"], 5.0)
        self.assertAlmostEqual(s["mae_played"], 5.5)
        self.assertEqual(s["by_cat"]["dnp"], {"n": 1, "mae": 4.0})
        self.assertEqual(s["by_cat"]["low"], {"n": 0, "mae": None})

    def test_spearman(self):
        self.assertAlmostEqual(spearman([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertAlmostEqual(spearman([1, 1, 2], [1, 2, 3]), 0.8660, places=4)
        self.assertIsNone(spearman([0, 0, 0], [1, 2, 3]))     # all-equal projections
        self.assertIsNone(spearman([1], [1]))

    def test_rank_by_position(self):
        mids = [Scored("MID", i, i, "mid") for i in range(5)]
        fwds = [Scored("FWD", i, -i, "mid") for i in range(5)]
        gks = [Scored("GK", i, i, "mid") for i in range(4)]       # too few: ignored
        self.assertAlmostEqual(rank_by_position(mids + fwds + gks), 0.0)
        self.assertIsNone(rank_by_position(gks))
        self.assertIsNone(rank_by_position([Scored(None, i, i, "mid") for i in range(6)]))

    def test_match_scores(self):
        self.assertAlmostEqual(log_loss(0.5, 0.3, 0.2, "draw"), 1.2040, places=4)
        self.assertAlmostEqual(log_loss(0.0, 0.0, 1.0, "home"), 13.8155, places=4)   # clamped, no domain error
        self.assertAlmostEqual(rps(0.5, 0.3, 0.2, "home"), 0.145)
        self.assertAlmostEqual(brier(0.3, True), 0.49)


if __name__ == "__main__":
    unittest.main()


class StatsTests(unittest.TestCase):
    def test_wilson(self):
        lo, hi = wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2693, places=3)   # z = 1.645: (0.6353 - 0.2932) / 1.2706
        self.assertAlmostEqual(hi, 0.7307, places=3)
        self.assertEqual(wilson(0, 0), (0.0, 1.0))

    def test_paired_t(self):
        t, df = paired_t([1, 2, 3])
        self.assertAlmostEqual(t, 3.4641, places=3)
        self.assertEqual(df, 2)
        self.assertIsNone(paired_t([1, 1])[0])
        self.assertIsNone(paired_t([1])[0])

    def test_mean_ci(self):
        lo, hi = mean_ci([1, 2, 3])
        half = 4.303 / math.sqrt(3)
        self.assertAlmostEqual(lo, 2 - half, places=3)
        self.assertAlmostEqual(hi, 2 + half, places=3)
        self.assertIsNone(mean_ci([1]))

    def test_signflip(self):
        self.assertGreater(signflip_p([1, -1, 1, -1]), 0.9)
        self.assertLess(signflip_p([5] * 10), 0.01)
        self.assertIsNone(signflip_p([]))

    def test_reliability(self):
        bins = reliability([(0.05, False), (0.95, True), (1.0, True)])
        self.assertEqual(len(bins), 2)
        self.assertEqual((bins[-1]["n"], bins[-1]["observed"]), (2, 1.0))
        self.assertAlmostEqual(bins[0]["mean_p"], 0.05)
