import unittest

from sleeperfpl.matchup import find_opponent, opponent_for, team_label

S = {"start_gw": 9, "end_gw": 31, "opponents": ["A", "B", "C", "D", "E"]}
USERS = [{"user_id": "u1", "display_name": "ttvlyto", "team_name": "noSHOWdegaard"},
         {"user_id": "u2", "display_name": "Joliv487", "team_name": "Machine Gun Skelly"},
         {"user_id": "u3", "display_name": "briantrovato", "team_name": None}]
ROSTERS = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"}, {"roster_id": 3, "owner_id": "u3"}]


class ScheduleTests(unittest.TestCase):
    def test_cycle(self):
        self.assertEqual([opponent_for(S, g) for g in (9, 13, 14, 31)], ["A", "E", "A", "C"])

    def test_outside_range_is_none(self):
        self.assertEqual([opponent_for(S, 8), opponent_for(S, 32), opponent_for(S, None), opponent_for(None, 9)],
                         [None] * 4)


class FindOpponentTests(unittest.TestCase):
    def test_match_team_name_ignoring_case_and_spaces(self):
        roster, notice = find_opponent("  machine gun SKELLY ", USERS, ROSTERS, 1)
        self.assertEqual((roster["roster_id"], notice), (2, None))

    def test_falls_back_to_username(self):
        self.assertEqual(find_opponent("briantrovato", USERS, ROSTERS, 1)[0]["roster_id"], 3)

    def test_no_match_notice(self):
        roster, notice = find_opponent("nobody", USERS, ROSTERS, 1)
        self.assertIsNone(roster)
        self.assertIn("nobody", notice)
        self.assertIn("Machine Gun Skelly", notice)
        self.assertIn("briantrovato", notice)

    def test_owner_is_not_an_opponent(self):
        roster, notice = find_opponent("noSHOWdegaard", USERS, ROSTERS, 1)
        self.assertIsNone(roster)
        self.assertIn("you", notice)

    def test_team_label(self):
        self.assertEqual([team_label(u) for u in USERS], ["noSHOWdegaard", "Machine Gun Skelly", "briantrovato"])


if __name__ == "__main__":
    unittest.main()
