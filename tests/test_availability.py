import unittest

from sleeperfpl.availability import (LabelTable, apply_labels, chance_for, fpl_bucket, fpl_stale, is_hard_out,
                                    slp_bucket)


class BucketTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(fpl_bucket("a", None), "a100")
        self.assertEqual(fpl_bucket("a", 100), "a100")
        self.assertEqual(fpl_bucket("d", 75), "d75")
        self.assertEqual(fpl_bucket("i", 0), "i0")
        self.assertEqual(fpl_bucket("i", None), "i0")
        self.assertEqual(fpl_bucket("d", None), "d50")
        self.assertEqual(fpl_bucket("u", 0), "u")
        self.assertEqual(slp_bucket("Out"), "out")
        self.assertEqual(slp_bucket(None), "none")
        self.assertTrue(is_hard_out("i0") and is_hard_out("s") and is_hard_out("d0"))
        self.assertFalse(is_hard_out("d25") or is_hard_out("a100"))

    def test_chance_for_switches_at_deadline(self):
        el = {"chance_next": 0, "chance_this": 100}
        self.assertEqual(chance_for(el, deadline_passed=False), 0)
        self.assertEqual(chance_for(el, deadline_passed=True), 100)


class LabelLayerTests(unittest.TestCase):
    def test_priors_without_counts(self):
        ps_fpl, pa_fpl, ps, pa = apply_labels(0.8, 0.9, "a100", "out", False, LabelTable(), 1.0)
        self.assertEqual((ps_fpl, pa_fpl), (0.8, 0.9))
        self.assertAlmostEqual(ps, 0.64)
        self.assertAlmostEqual(pa, 0.855)

    def test_weight_zero_ignores_sleeper(self):
        _, _, ps, pa = apply_labels(0.8, 0.9, "a100", "out", False, LabelTable(), 0.0)
        self.assertAlmostEqual(ps, 0.8)
        self.assertAlmostEqual(pa, 0.9)

    def test_stale_short_doubt_halfway(self):
        ps_fpl, _, _, _ = apply_labels(1.0, 1.0, "d75", "none", True, LabelTable(), 1.0)
        self.assertAlmostEqual(ps_fpl, 1 - 0.5 * 0.37)

    def test_stale_long_injury_not_revived(self):
        ps_fpl, _, _, _ = apply_labels(1.0, 1.0, "i25", "none", True, LabelTable(), 1.0)
        self.assertAlmostEqual(ps_fpl, 0.06)

    def test_stale_fpl_sleeper_out_cap(self):
        _, _, ps, _ = apply_labels(1.0, 1.0, "a100", "out", True, LabelTable(), 1.0)
        self.assertAlmostEqual(ps, 0.60)

    def test_hard_out_zero(self):
        self.assertEqual(apply_labels(0.9, 0.95, "i0", "none", False, LabelTable(), 1.0), (0.0, 0.0, 0.0, 0.0))

    def test_flagged_out_and_unknown_cells(self):
        t = LabelTable()
        self.assertEqual(t.rr_slp("d75", "out"), (0.90, 0.95))
        self.assertEqual(t.rr_slp("a100", "none"), (1.0, 1.0))
        self.assertEqual(t.rr_fpl("d10"), t.rr_fpl("d25"))   # nearest chance level

    def test_fpl_stale(self):
        self.assertTrue(fpl_stale("2026-10-01T10:00:00Z", "2026-10-18T14:00:00Z"))
        self.assertFalse(fpl_stale("2026-10-20T10:00:00Z", "2026-10-18T14:00:00Z"))
        self.assertFalse(fpl_stale(None, "2026-10-18T14:00:00Z"))
        self.assertFalse(fpl_stale("2026-10-01T10:00:00Z", None))


if __name__ == "__main__":
    unittest.main()
