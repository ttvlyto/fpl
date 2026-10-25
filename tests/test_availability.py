import unittest

from sleeperfpl.availability import chance_for, fpl_bucket, is_hard_out, slp_bucket


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


if __name__ == "__main__":
    unittest.main()
