import unittest
from datetime import datetime, timezone

from sleeperfpl.gameweek import target_gameweek, team_fixtures


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


if __name__ == "__main__":
    unittest.main()
