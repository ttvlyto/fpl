import unittest

from sleeperfpl.db import connect
from sleeperfpl.fetch_results import RESULTS_ALIASES, fetch_results, parse_results

CSV = ("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
       "E0,16/08/24,Man United,Fulham,1,0\n"
       "E0,17/08/2024,Arsenal,Wolves,2,0\n"
       ",,,,,\n")


class ParseResultsTests(unittest.TestCase):
    def test_parse_results_handles_bom_short_dates_and_blank_rows(self):
        self.assertEqual(parse_results(CSV, "2024", RESULTS_ALIASES),
                         [("2024", "2024-08-16", "Man Utd", "Fulham", 1, 0),
                          ("2024", "2024-08-17", "Arsenal", "Wolves", 2, 0)])

    def test_override_beats_builtin_alias(self):
        rows = parse_results(CSV, "2024", {**RESULTS_ALIASES, "Man United": "Manchester United"})
        self.assertEqual(rows[0][2], "Manchester United")

    def test_unparseable_score_skipped(self):
        text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,16/08/24,Arsenal,Wolves,,\n"
        self.assertEqual(parse_results(text, "2024", {}), [])


class FetchResultsTests(unittest.TestCase):
    def test_fetch_results_skips_stored_seasons(self):
        conn = connect(":memory:")
        urls = []

        def get_text(url):
            urls.append(url)
            return CSV

        self.assertEqual(fetch_results(conn, 2023, 2024, {}, get_text=get_text), 2)
        self.assertEqual(urls, ["https://www.football-data.co.uk/mmz4281/2324/E0.csv",
                                "https://www.football-data.co.uk/mmz4281/2425/E0.csv"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0], 4)
        self.assertEqual(fetch_results(conn, 2023, 2024, {}, get_text=get_text), 0)
        self.assertEqual(len(urls), 2)
        self.assertEqual(fetch_results(conn, 2023, 2024, {}, force=True, get_text=get_text), 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0], 4)

    def test_overrides_results_teams_applied(self):
        conn = connect(":memory:")
        fetch_results(conn, 2024, 2024, {"results_teams": {"Fulham": "Fulham FC"}}, get_text=lambda url: CSV)
        self.assertEqual(conn.execute("SELECT away FROM match_results WHERE home = 'Man Utd'").fetchone()[0],
                         "Fulham FC")


if __name__ == "__main__":
    unittest.main()
