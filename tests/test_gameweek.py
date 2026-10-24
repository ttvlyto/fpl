import unittest
from datetime import datetime, timezone

from sleeperfpl.gameweek import live_gameweek, matchday_due, season_of, target_gameweek, team_fixtures


def ev(i, finished):
    return {"event_id": i, "finished": finished}


def fx(fid, event, h, a, kickoff, hd=2, ad=4):
    return {"fixture_id": fid, "event": event, "team_h": h, "team_a": a, "kickoff_time": kickoff,
            "team_h_difficulty": hd, "team_a_difficulty": ad}


EVENTS = [ev(8, True), ev(9, False), ev(10, False)]
FIXTURES = [fx(1, 9, 1, 2, "2026-10-24T11:30:00Z"), fx(2, 9, 3, 4, "2026-10-25T14:00:00Z"),
            fx(3, 10, 1, 3, "2026-10-31T15:00:00Z")]


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class GameweekTests(unittest.TestCase):
    def test_thursday_before_kickoff_is_instant(self):
        g = target_gameweek(EVENTS, FIXTURES, utc(2026, 10, 22, 20))
        self.assertEqual((g.gw, g.mode), (9, "instant"))
        self.assertEqual(g.first_kickoff, utc(2026, 10, 24, 11, 30))

    def test_after_first_kickoff_is_waiver_next_gw(self):
        g = target_gameweek(EVENTS, FIXTURES, utc(2026, 10, 24, 12))
        self.assertEqual((g.gw, g.mode), (10, "waiver"))

    def test_season_over(self):
        g = target_gameweek([ev(38, True)], [fx(1, 38, 1, 2, "2026-05-24T15:00:00Z")], utc(2026, 6, 1))
        self.assertIsNone(g.gw)

    def test_team_fixtures_double_and_blank(self):
        fixtures = [fx(1, 9, 1, 2, "2026-10-24T11:30:00Z", hd=2, ad=5),
                    fx(2, 9, 3, 1, "2026-10-26T14:00:00Z", hd=3, ad=4)]
        teams = {1: "ARS", 2: "LIV", 3: "EVE", 4: "CHE"}
        out = team_fixtures(fixtures, teams, 9)
        self.assertEqual(len(out["ARS"]), 2)
        self.assertNotIn("CHE", out)
        liv = out["LIV"][0]
        self.assertEqual((liv.opponent, liv.home, liv.difficulty), ("ARS", False, 5))
        self.assertEqual([f.difficulty for f in out["ARS"]], [2, 4])


class SeasonOfTests(unittest.TestCase):
    def test_season_from_earliest_deadline(self):
        events = [{"event_id": 2, "deadline_time": "2026-08-22T10:00:00Z"},
                  {"event_id": 38, "deadline_time": "2027-05-23T10:00:00Z"}]
        self.assertEqual(season_of(events), "2026")
        self.assertIsNone(season_of([{"event_id": 1, "deadline_time": None}]))


class LiveGameweekTests(unittest.TestCase):
    def test_live_gameweek(self):
        self.assertIsNone(live_gameweek(EVENTS, FIXTURES, utc(2026, 10, 22, 20)))
        self.assertEqual(live_gameweek(EVENTS, FIXTURES, utc(2026, 10, 24, 12)), 9)


class MatchdayDueTests(unittest.TestCase):
    def test_due_before_kickoff_and_after_final_whistle(self):
        fx = [fx_ for fx_ in FIXTURES if fx_["fixture_id"] == 1]   # kickoff 2026-10-24 11:30
        self.assertFalse(matchday_due(fx, utc(2026, 10, 24, 7), 3))
        self.assertTrue(matchday_due(fx, utc(2026, 10, 24, 9), 3))
        self.assertFalse(matchday_due(fx, utc(2026, 10, 24, 13), 3))   # mid-match: nothing to do
        self.assertTrue(matchday_due(fx, utc(2026, 10, 24, 14, 30), 3))  # just finished: check minutes
        self.assertFalse(matchday_due(fx, utc(2026, 10, 24, 18), 3))


if __name__ == "__main__":
    unittest.main()
