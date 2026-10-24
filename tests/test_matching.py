import tempfile
import unittest
from pathlib import Path

from sleeperfpl.config import ConfigError
from sleeperfpl.matching import load_overrides, match_players, normalize_name

TEAMS = [{"team_id": 1, "short_name": "ARS"}, {"team_id": 2, "short_name": "CHE"}]


def sp(pid, name, team="ARS", positions=("MID",)):
    return {"player_id": pid, "name": name, "team": team, "positions": list(positions)}


def el(fid, first, second, web, team=1, etype=3):
    return {"fpl_id": fid, "first_name": first, "second_name": second, "web_name": web,
            "team_id": team, "element_type": etype}


class MatchingTests(unittest.TestCase):
    def test_normalize_name(self):
        self.assertEqual(normalize_name("  Martin  Ødegaard-Jr. "), "martin odegaard jr")

    def test_accented_exact_match(self):
        r = match_players([sp("s1", "Martin Odegaard")], [el(10, "Martin", "Ødegaard", "Ødegaard")], TEAMS, {})
        self.assertEqual(r.mapping, {"s1": 10})
        self.assertEqual(r.method["s1"], "exact")

    def test_club_alias(self):
        r = match_players([sp("s1", "Bukayo Saka", team="AFC")], [el(7, "Bukayo", "Saka", "Saka")], TEAMS,
                          {"players": {}, "clubs": {"AFC": "ARS"}})
        self.assertEqual(r.mapping, {"s1": 7})

    def test_last_initial_match(self):
        r = match_players([sp("s1", "Ben White")], [el(3, "Benjamin", "White", "White", etype=2)], TEAMS, {})
        self.assertEqual(r.mapping, {"s1": 3})
        self.assertEqual(r.method["s1"], "last+initial")

    def test_web_name_match(self):
        r = match_players([sp("s1", "Gabriel", positions=("DEF",))],
                          [el(5, "Gabriel", "dos Santos Magalhães", "Gabriel", etype=2)], TEAMS, {})
        self.assertEqual(r.mapping, {"s1": 5})
        self.assertEqual(r.method["s1"], "web_name")

    def test_ambiguous_stays_unmatched(self):
        r = match_players([sp("s1", "Ben White")],
                          [el(3, "Ben", "White", "White"), el(4, "Ben", "White", "B.White")], TEAMS, {})
        self.assertEqual(r.mapping, {})
        self.assertEqual(r.unmatched, ["s1"])

    def test_position_breaks_tie(self):
        r = match_players([sp("s1", "Ben White", positions=("DEF",))],
                          [el(3, "Ben", "White", "White", etype=2), el(4, "Ben", "White", "B.White", etype=3)],
                          TEAMS, {})
        self.assertEqual(r.mapping, {"s1": 3})

    def test_override_wins(self):
        r = match_players([sp("s1", "Totally Different")], [el(9, "Bukayo", "Saka", "Saka")], TEAMS,
                          {"players": {"s1": 9}, "clubs": {}})
        self.assertEqual(r.mapping, {"s1": 9})
        self.assertEqual(r.method["s1"], "override")

    def test_no_club_candidates_unmatched(self):
        r = match_players([sp("s1", "Bukayo Saka", team="XYZ")], [el(7, "Bukayo", "Saka", "Saka")], TEAMS, {})
        self.assertEqual(r.unmatched, ["s1"])

    def test_fpl_id_claimed_twice_unmatches_both(self):
        r = match_players([sp("s1", "Saka"), sp("s2", "Bukayo Saka")], [el(7, "Bukayo", "Saka", "Saka")], TEAMS, {})
        self.assertEqual(r.mapping, {})
        self.assertEqual(sorted(r.unmatched), ["s1", "s2"])

    def test_load_overrides_missing_and_bad(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_overrides(Path(d) / "nope.json"), {"players": {}, "clubs": {}, "market_teams": {}})
            bad = Path(d) / "o.json"
            bad.write_text("{oops")
            with self.assertRaisesRegex(ConfigError, "o.json"):
                load_overrides(bad)


    def test_load_overrides_bad_value(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "o.json"
            bad.write_text('{"players": {"s1": "abc"}}')
            with self.assertRaisesRegex(ConfigError, "o.json"):
                load_overrides(bad)


class SleeperFormatTests(unittest.TestCase):
    TEAMS = [{"team_id": 1, "short_name": "ARS"}, {"team_id": 2, "short_name": "CHE"}]
    ELEMENTS = [el(1, "Bukayo", "Saka", "Saka"), el(2, "Declan", "Rice", "Rice"),
                el(3, "David", "Raya", "Raya", etype=1), el(4, "Cole", "Palmer", "Palmer", team=2),
                el(5, "Ben", "White", "White", etype=2), el(6, "Ben", "White", "B.White", etype=3)]

    def test_numeric_club_ids_resolved_by_vote(self):
        players = [sp("a", "Bukayo Saka", team="1037"), sp("b", "Declan Rice", team="1037"),
                   sp("c", "David Raya", team="1037", positions=("GK",)), sp("d", "Cole Palmer", team="1020"),
                   sp("e", "Cole Palmerson", team="1020")]
        r = match_players(players, self.ELEMENTS, self.TEAMS, {})
        self.assertEqual({k: r.mapping.get(k) for k in "abc"}, {"a": 1, "b": 2, "c": 3})
        # one vote is not enough to trust a club mapping
        self.assertNotIn("d", r.mapping)

    def test_manual_club_alias_beats_vote(self):
        players = [sp("a", "Bukayo Saka", team="1037"), sp("b", "Declan Rice", team="1037")]
        r = match_players(players, self.ELEMENTS, self.TEAMS, {"players": {}, "clubs": {"1037": "CHE"}})
        self.assertEqual(r.mapping, {})

    def test_sleeper_position_codes_narrow_ties(self):
        players = [sp("a", "Bukayo Saka", team="1037"), sp("b", "Declan Rice", team="1037"),
                   sp("w", "Ben White", team="1037", positions=("D",))]
        r = match_players(players, self.ELEMENTS, self.TEAMS, {})
        self.assertEqual(r.mapping.get("w"), 5)


if __name__ == "__main__":
    unittest.main()
