import unittest

from sleeperfpl.db import connect
from sleeperfpl.headtohead import Result, all_results, form, head_to_head, season_results
from tests.test_report import TS, seed

# Oldest first: Arsenal win, draw, Chelsea win (at Chelsea), draw.
RESULTS = [Result("2023-01-01", "Arsenal", "Chelsea", 2, 0), Result("2023-08-01", "Chelsea", "Arsenal", 1, 1),
           Result("2024-01-01", "Chelsea", "Arsenal", 1, 0), Result("2024-08-01", "Arsenal", "Chelsea", 0, 0)]


class HeadToHeadTests(unittest.TestCase):
    def test_head_to_head_counts_and_winless_runs(self):
        h = head_to_head(RESULTS, "Arsenal", "Chelsea")
        self.assertEqual((h["a_wins"], h["draws"], h["b_wins"]), (1, 2, 1))
        self.assertEqual((h["a_winless_run"], h["b_winless_run"]), (3, 1))
        self.assertEqual(h["meetings"][0], {"date": "2024-08-01", "home": "Arsenal", "away": "Chelsea", "score": "0-0"})

    def test_head_to_head_limits_meetings_but_not_runs(self):
        h = head_to_head(RESULTS, "Arsenal", "Chelsea", n=2)
        self.assertEqual(len(h["meetings"]), 2)
        self.assertEqual((h["a_wins"], h["draws"], h["b_wins"]), (0, 1, 1))
        self.assertEqual(h["a_winless_run"], 3)

    def test_no_meetings(self):
        h = head_to_head(RESULTS, "Arsenal", "Fulham")
        self.assertEqual((h["meetings"], h["a_winless_run"], h["b_winless_run"]), ([], 0, 0))

    def test_form_from_team_perspective(self):
        self.assertEqual(form(RESULTS, "Chelsea", 2), ["D 0-0 v Arsenal (A)", "W 1-0 v Arsenal (H)"])


class AllResultsTests(unittest.TestCase):
    def test_all_results_merges_fpl_fixtures_without_duplicates(self):
        conn = connect(":memory:")
        seed(conn)
        conn.execute("INSERT INTO fpl_fixtures VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (TS, 40, 8, 2, 1, 3, 4, "2026-10-18T14:00:00Z", 1, 2, 1))
        conn.execute("INSERT INTO match_results VALUES (?,?,?,?,?,?)", ("2026", "2026-10-18", "Chelsea", "Arsenal", 2, 1))
        conn.execute("INSERT INTO match_results VALUES (?,?,?,?,?,?)", ("2025", "2025-12-01", "Arsenal", "Chelsea", 3, 0))
        self.assertEqual(all_results(conn), [Result("2025-12-01", "Arsenal", "Chelsea", 3, 0),
                                             Result("2026-10-18", "Chelsea", "Arsenal", 2, 1)])
        self.assertEqual(season_results(conn), [Result("2026-10-18", "Chelsea", "Arsenal", 2, 1)])


if __name__ == "__main__":
    unittest.main()
