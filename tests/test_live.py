import unittest

from sleeperfpl.live import unlikely_to_play, zero_minute_replacements
from sleeperfpl.models import PlayerCtx

ELIG = {"M": ["M"], "D": ["D"], "FLEX": ["M", "D"]}


def p(sid, pos, club, avg=5.0, **kw):
    return PlayerCtx(sleeper_id=sid, name=sid.title(), positions=[pos], club=club, fpl_id=1, avg=avg,
                     on_roster=True, **kw)


class ZeroMinuteTests(unittest.TestCase):
    def test_zero_minute_gets_bench_from_later_match(self):
        starters = [("M", p("sat", "M", "ARS"))]
        bench = [p("sun1", "M", "LIV", avg=4), p("sun2", "M", "LIV", avg=7), p("def", "D", "LIV", avg=9)]
        out = zero_minute_replacements(starters, bench, {"ARS"}, {"ARS"}, {}, ELIG)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].starter.sleeper_id, out[0].slot, out[0].bench.sleeper_id), ("sat", "M", "sun2"))

    def test_no_candidate_when_all_bench_started(self):
        out = zero_minute_replacements([("M", p("sat", "M", "ARS"))], [p("b", "M", "CHE")],
                                       {"ARS"}, {"ARS", "CHE"}, {}, ELIG)
        self.assertIsNone(out[0].bench)
        self.assertEqual(out[0].reason, "no bench player whose match hasn't started")

    def test_played_starter_not_replaced(self):
        out = zero_minute_replacements([("M", p("sat", "M", "ARS"))], [p("b", "M", "LIV")],
                                       {"ARS"}, {"ARS"}, {"sat": 12}, ELIG)
        self.assertEqual(out, [])

    def test_unfinished_match_not_checked(self):
        self.assertEqual(zero_minute_replacements([("M", p("sat", "M", "ARS"))], [p("b", "M", "LIV")],
                                                  set(), {"ARS"}, {}, ELIG), [])

    def test_bench_used_once_and_reserve_skipped(self):
        starters = [("M", p("a", "M", "ARS")), ("FLEX", p("b", "M", "ARS"))]
        bench = [p("x", "M", "LIV", avg=6), p("ir", "M", "LIV", avg=9, in_reserve=True)]
        out = zero_minute_replacements(starters, bench, {"ARS"}, {"ARS"}, {}, ELIG)
        self.assertEqual([r.bench.sleeper_id if r.bench else None for r in out], ["x", None])


class UnlikelyTests(unittest.TestCase):
    def test_unlikely_not_in_market_and_injury(self):
        starters = [("M", p("salah", "M", "LIV")), ("M", p("szoboszlai", "M", "LIV")), ("D", p("saliba", "D", "ARS"))]
        starters[0][1].name = "Mohamed Salah"
        starters[1][1].name = "Dominik Szoboszlai"
        starters[2][1].name = "William Saliba"
        out = unlikely_to_play(starters, {"LIV": {"mohamed salah"}}, {"saliba": "Questionable"})
        self.assertEqual(out, {"szoboszlai": "not in market lineup", "saliba": "injury: Questionable"})


class ReviewFixLiveTests(unittest.TestCase):
    def test_bench_needs_upcoming_fixture_and_fitness(self):
        starters = [("M", p("sat", "M", "ARS"))]
        no_match = p("blank", "M", "CHE", avg=9)
        injured = p("hurt", "M", "LIV", avg=8, flag="out")
        fit = p("ok", "M", "LIV", avg=3)
        out = zero_minute_replacements(starters, [no_match, injured, fit], {"ARS"}, {"ARS"}, {}, ELIG,
                                       upcoming_teams={"LIV"})
        self.assertEqual(out[0].bench.sleeper_id, "ok")

    def test_unlikely_only_for_known_attackers(self):
        gk = p("raya", "GK", "ARS")
        gk.name = "David Raya"
        mid = p("odegaard", "M", "ARS")
        mid.name = "Martin Odegaard"
        new = p("newbie", "M", "ARS")
        new.name = "Never Listed"
        out = unlikely_to_play([("GK", gk), ("M", mid), ("M", new)], {"ARS": {"bukayo saka"}}, {},
                               known_names={"martin odegaard", "david raya"})
        self.assertEqual(out, {"odegaard": "not in market lineup"})


if __name__ == "__main__":
    unittest.main()
