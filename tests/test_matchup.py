import unittest

from types import SimpleNamespace

from sleeperfpl.matchup import (find_opponent, formation, live_side, opponent_for, pitch_row, record_text,
                                 team_label)

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


class DisplayTests(unittest.TestCase):
    def test_pitch_row_and_formation(self):
        self.assertEqual([pitch_row(p) for p in (["F", "M"], ["MID"], ["D"], ["GK"], [], ["X"])], [0, 1, 2, 3, 1, 1])
        self.assertEqual(formation([["D"], ["D"], ["D"], ["M"], ["M", "D"], ["F", "M"], ["GK"]]), "3-2-1")

    def test_record_text(self):
        self.assertEqual(record_text({"wins": 4, "losses": 3, "ties": 0}), "4–3")
        self.assertEqual(record_text({"wins": 4, "losses": 3, "ties": 1}), "4–3–1")
        self.assertEqual(record_text({"wins": None}), "–")
        self.assertEqual(record_text({}), "–")


class LiveSideTests(unittest.TestCase):
    def test_live_side_points_and_states(self):
        done = SimpleNamespace(sleeper_id="a", club="ARS")
        later = SimpleNamespace(sleeper_id="b", club="CHE")
        playing = SimpleNamespace(sleeper_id="c", club="LIV")
        rows, total, to_play = live_side([("M", done), ("M", later), ("F", playing), ("GK", None)],
                                         {"a": {"pos_m_g": 1}, "b": {"pos_m_g": 1}, "c": {"pos_m_g": 2}},
                                         {"pos_m_g": 9}, {"ARS": "done", "CHE": "to_play", "LIV": "playing"})
        self.assertEqual([(r["points"], r["state"]) for r in rows],
                         [(9.0, "done"), (0.0, "to_play"), (18.0, "playing"), (0.0, None)])
        self.assertEqual((total, to_play), (27.0, 1))

    def test_unknown_club_is_to_play(self):
        rows, total, to_play = live_side([("M", SimpleNamespace(sleeper_id="x", club=""))], {}, {}, {})
        self.assertEqual((rows[0]["state"], total, to_play), ("to_play", 0.0, 1))


if __name__ == "__main__":
    unittest.main()
