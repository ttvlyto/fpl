import unittest

from sleeperfpl.config import ConfigError
from sleeperfpl.lineup import best_xi, starting_slots
from sleeperfpl.models import PlayerCtx

ELIG = {"GK": ["GK"], "DEF": ["DEF"], "MID": ["MID"], "FWD": ["FWD"]}


def p(sid, pos, proj, avg=None, fpl_id=1, **kw):
    positions = pos.split("/")
    return PlayerCtx(sleeper_id=sid, name=sid, positions=positions, club="ARS", fpl_id=fpl_id,
                     proj=proj, avg=proj if avg is None else avg, on_roster=True, **kw)


def names(lineup):
    return {slot: (pl.sleeper_id if pl else None) for slot, pl in lineup.starters}


def brute_total(roster, slots):
    import itertools
    best = (-1, 0.0)
    pool = [pl for pl in roster if not pl.in_reserve]
    for perm in itertools.permutations(range(len(pool) + len(slots)), len(slots)):
        filled, total, seen = 0, 0.0, set()
        for slot, j in zip(slots, perm):
            if j < len(pool) and set(pool[j].positions) & set(ELIG[slot]):
                filled += 1
                total += pool[j].proj
        best = max(best, (filled, round(total, 6)))
    return best


class OptimalityTests(unittest.TestCase):
    def test_matches_brute_force_on_random_rosters(self):
        import random
        rng = random.Random(7)
        for _ in range(40):
            roster = [p(f"P{i}", rng.choice(["GK", "DEF", "MID", "FWD", "MID/FWD", "DEF/MID"]),
                        round(rng.uniform(0, 10), 1)) for i in range(rng.randint(2, 6))]
            slots = [rng.choice(["GK", "DEF", "MID", "FWD"]) for _ in range(rng.randint(1, 4))]
            lu = best_xi(roster, slots, ELIG)
            filled = len(slots) - len(lu.empty_slots)
            self.assertEqual((filled, round(lu.total, 6)), brute_total(roster, slots))


class StartingSlotsTests(unittest.TestCase):
    def test_unknown_slot_raises(self):
        with self.assertRaisesRegex(ConfigError, "SUPER_FLEX"):
            starting_slots(["GK", "SUPER_FLEX", "BN"], {"GK": ["GK"]})

    def test_drops_non_starting(self):
        self.assertEqual(starting_slots(["GK", "DEF", "BN", "BN", "IR"], ELIG), ["GK", "DEF"])


class BestXiTests(unittest.TestCase):
    def test_multi_position_beats_greedy(self):
        roster = [p("A", "MID/FWD", 10), p("B", "MID", 9), p("C", "FWD", 1)]
        lu = best_xi(roster, ["MID", "FWD"], ELIG)
        self.assertEqual(names(lu), {"MID": "B", "FWD": "A"})
        self.assertEqual(lu.total, 19)

    def test_reserve_excluded(self):
        roster = [p("A", "MID", 10, in_reserve=True), p("B", "MID", 2)]
        lu = best_xi(roster, ["MID"], ELIG)
        self.assertEqual(names(lu), {"MID": "B"})
        self.assertNotIn("A", [b.sleeper_id for b in lu.bench])

    def test_unmatched_fills_slot_legally(self):
        roster = [p("G", "GK", 0.0, fpl_id=None, flag="no data"), p("M", "MID", 5)]
        lu = best_xi(roster, ["GK", "MID"], ELIG)
        self.assertEqual(names(lu), {"GK": "G", "MID": "M"})
        self.assertEqual(lu.empty_slots, [])

    def test_empty_slot_reported(self):
        lu = best_xi([p("M", "MID", 5)], ["GK", "MID"], ELIG)
        self.assertEqual(lu.empty_slots, ["GK"])
        self.assertEqual(names(lu)["GK"], None)

    def test_bench_is_rest_sorted_by_proj(self):
        roster = [p("A", "MID", 9), p("B", "MID", 1), p("C", "MID", 4)]
        lu = best_xi(roster, ["MID"], ELIG)
        self.assertEqual([b.sleeper_id for b in lu.bench], ["C", "B"])

    def test_slot_order_preserved(self):
        roster = [p("G", "GK", 3), p("D1", "DEF", 4), p("D2", "DEF", 2), p("F", "FWD", 6)]
        lu = best_xi(roster, ["GK", "DEF", "DEF", "FWD"], ELIG)
        self.assertEqual([s for s, _ in lu.starters], ["GK", "DEF", "DEF", "FWD"])
        self.assertEqual(lu.total, 15)


if __name__ == "__main__":
    unittest.main()
