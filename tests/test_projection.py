import unittest

from sleeperfpl.projection import project

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
