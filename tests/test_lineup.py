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



from sleeperfpl.lineup import rank_free_agents, swap_tips  # noqa: E402


def fa(sid, pos, proj, avg=None, **kw):
    pl = p(sid, pos, proj, avg, **kw)
    pl.on_roster = False
    return pl


class SwapTests(unittest.TestCase):
    def tips(self, roster, fas, slots, min_gain=1.0, max_swaps=3):
        return swap_tips(roster, fas, slots, ELIG, min_gain, max_swaps)

    def test_xi_upgrade_suggested(self):
        tips = self.tips([p("M", "MID", 2)], [fa("F", "MID", 6)], ["MID"])
        self.assertEqual(len(tips), 1)
        self.assertEqual((tips[0].drop.sleeper_id, tips[0].add.sleeper_id, tips[0].kind), ("M", "F", "xi"))
        self.assertAlmostEqual(tips[0].gain, 4)

    def test_below_threshold_not_suggested(self):
        self.assertEqual(self.tips([p("M", "MID", 2)], [fa("F", "MID", 2.5)], ["MID"]), [])

    def test_cross_position_swap_keeps_legal_xi(self):
        roster = [p("G", "GK", 1), p("M", "MID", 5)]
        tips = self.tips(roster, [fa("X", "MID", 9)], ["GK", "MID"])
        self.assertNotIn("G", [t.drop.sleeper_id for t in tips])
        self.assertEqual([(t.drop.sleeper_id, t.add.sleeper_id) for t in tips], [("M", "X")])

    def test_bench_upgrade(self):
        roster = [p("S", "FWD", 8), p("B", "FWD", 1, avg=1)]
        tips = self.tips(roster, [fa("N", "FWD", 4, avg=4)], ["FWD"])
        self.assertEqual(len(tips), 1)
        self.assertEqual((tips[0].drop.sleeper_id, tips[0].kind), ("B", "bench"))
        self.assertAlmostEqual(tips[0].gain, 3)

    def test_unmatched_never_dropped(self):
        roster = [p("U", "MID", 0, fpl_id=None, flag="no data")]
        self.assertEqual(self.tips(roster, [fa("F", "MID", 9)], ["MID"]), [])

    def test_out_free_agent_never_added(self):
        self.assertEqual(self.tips([p("M", "MID", 2)], [fa("F", "MID", 9, flag="out")], ["MID"]), [])

    def test_each_player_used_once_and_max_swaps(self):
        roster = [p("M1", "MID", 1), p("M2", "MID", 2), p("M3", "MID", 3)]
        fas = [fa("A", "MID", 9), fa("B", "MID", 8)]
        tips = self.tips(roster, fas, ["MID", "MID", "MID"], max_swaps=5)
        drops = [t.drop.sleeper_id for t in tips]
        adds = [t.add.sleeper_id for t in tips]
        self.assertEqual(len(set(drops)), len(drops))
        self.assertEqual(sorted(adds), ["A", "B"])
        self.assertEqual(drops[0], "M1")
        self.assertEqual(len(self.tips(roster, fas, ["MID", "MID", "MID"], max_swaps=1)), 1)

    def test_rank_free_agents_top_n_per_position(self):
        fas = [fa(f"M{i}", "MID", float(i)) for i in range(12)]
        fas.append(fa("OUT", "MID", 99, flag="out"))
        fas.append(fa("U", "MID", 50, fpl_id=None))
        fas.append(fa("D", "DEF", 3))
        ranked = rank_free_agents(fas, 10)
        self.assertEqual([x.sleeper_id for x in ranked["MID"]][:2], ["M11", "M10"])
        self.assertEqual(len(ranked["MID"]), 10)
        self.assertNotIn("OUT", [x.sleeper_id for x in ranked["MID"]])
        self.assertEqual([x.sleeper_id for x in ranked["DEF"]], ["D"])


if __name__ == "__main__":
    unittest.main()
