import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from sleeperfpl import second_opinion as so
from sleeperfpl.db import connect
from sleeperfpl.report import BuildError
from tests.test_report import NOW, TS, make_cfg, seed

LEAGUE = {"sport": "s", "season": "2026", "roster_positions": ["GK", "MID", "MID", "BN", "BN"],
          "scoring_settings": {"pos_m_g": 5}}


def seed_sleeper(conn):
    """The report test data plus a 2026 league and Sleeper stats for weeks 6-8 (Saka misses week 7)."""
    seed(conn)
    conn.execute("INSERT INTO sleeper_league VALUES (?,?,?)", ("2026-10-22T07:00:00Z", "L1", json.dumps(LEAGUE)))
    rows = []
    for pid, goals in (("s1", 0), ("s2", 1), ("s3", 0), ("s5", 2), ("s6", 0)):
        for week in (6, 7, 8):
            if pid == "s2" and week == 7:
                continue
            rows.append(("2026", week, pid, json.dumps({"min": 90, "pos_m_g": goals}), TS))
    conn.executemany("INSERT INTO sleeper_stats VALUES (?,?,?,?,?)", rows)


class PacketTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self._tmp.name))
        self.cfg.stats_source = "sleeper"
        self.conn = connect(":memory:")
        seed_sleeper(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_packet_has_no_tool_projections_or_picks(self):
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        text = json.dumps(packet)
        for word in ('"proj', '"p_start', '"e_min', '"ep_next', '"avg', '"p60', '"value'):
            self.assertNotIn(word, text)
        self.assertNotIn("xi", packet)
        self.assertNotIn("swaps", packet)
        self.assertEqual(packet["gameweek"], 9)
        self.assertEqual(packet["adds_are"], "free agent pickups")
        self.assertEqual(packet["slots"], ["GK", "MID", "MID"])

    def test_recent_weeks_include_zero_minute_weeks(self):
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        saka = next(p for p in packet["roster"] if p["id"] == "s2")
        self.assertIn({"gw": 7, "pts": 0, "min": 0}, saka["recent"])
        self.assertIn({"gw": 8, "pts": 5.0, "min": 90}, saka["recent"])
        self.assertEqual(saka["season"], {"apps": 2, "minutes": 180, "pts": 10.0})

    def test_trending_free_agent_always_included(self):
        with mock.patch.object(so, "FA_PER_POSITION", 0):
            packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        self.assertEqual([p["id"] for p in packet["free_agents"]], ["s5"])
        self.assertEqual(packet["free_agents"][0]["trending_adds"], 40)

    def test_free_agents_ranked_by_recent_points(self):
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        self.assertEqual({p["id"] for p in packet["free_agents"]}, {"s5", "s6"})

    def test_roster_flags_and_unmatched_left_out(self):
        self.cfg.undroppable = ["Bukayo Saka"]
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        ids = [p["id"] for p in packet["roster"]]
        self.assertNotIn("s4", ids)   # no FPL match
        saka = next(p for p in packet["roster"] if p["id"] == "s2")
        self.assertTrue(saka["undroppable"])
        self.assertFalse(saka["ir"])

    def test_fixture_carries_head_to_head(self):
        self.conn.execute("INSERT INTO match_results VALUES (?,?,?,?,?,?)", ("2025", "2025-12-01", "Arsenal", "Chelsea", 3, 0))
        packet, _ = so.build_packet(self.conn, self.cfg, NOW)
        f = packet["fixtures"][0]
        self.assertEqual((f["home"], f["away"]), ("ARS", "CHE"))
        self.assertEqual((f["home_name"], f["away_name"]), ("Arsenal", "Chelsea"))
        self.assertEqual(len(f["head_to_head"]["meetings"]), 1)
        self.assertEqual(f["head_to_head"]["home_wins"], 1)
        self.assertIsNone(f["p_home"])

    def test_no_target_week_raises_build_error(self):
        self.conn.execute("UPDATE fpl_events SET finished = 1")
        with self.assertRaisesRegex(BuildError, "game week"):
            so.build_packet(self.conn, self.cfg, NOW)

    def test_no_sleeper_stats_raises_build_error(self):
        self.conn.execute("DELETE FROM sleeper_stats")
        with self.assertRaisesRegex(BuildError, "Sleeper stats"):
            so.build_packet(self.conn, self.cfg, NOW)


if __name__ == "__main__":
    unittest.main()


from sleeperfpl.models import PlayerCtx  # noqa: E402

ELIG = {"GK": ["GK"], "MID": ["MID"], "FWD": ["FWD"]}


def pc(pid, pos, on_roster=True, ir=False, undroppable=False):
    return PlayerCtx(sleeper_id=pid, name=f"Player {pid}", positions=[pos], club="ARS", fpl_id=1,
                     on_roster=on_roster, in_reserve=ir, undroppable=undroppable)


def setup(slots=("GK", "MID", "MID"), extra=()):
    players = [pc("g", "GK"), pc("m1", "MID"), pc("m2", "MID"), pc("m3", "MID"), pc("ir", "MID", ir=True),
               pc("keep", "MID", undroppable=True), pc("fa", "MID", on_roster=False),
               pc("fa2", "FWD", on_roster=False), *extra]
    ctx = {p.sleeper_id: p for p in players}
    packet = {"slots": list(slots), "max_swaps": 2,
              "roster": [{"id": p.sleeper_id, "ir": p.in_reserve, "undroppable": p.undroppable}
                         for p in players if p.on_roster],
              "free_agents": [{"id": p.sleeper_id} for p in players if not p.on_roster]}
    return packet, {"ctx": ctx}


def answer(xi, swaps=()):
    return {"summary": "s", "matchup_notes": [],
            "xi": [{"player_id": pid, "slot": slot, "reason": "r"} for slot, pid in xi],
            "swaps": [{"drop_id": d, "add_id": a, "reason": "r"} for d, a in swaps]}


class ValidateTests(unittest.TestCase):
    def check(self, ans, slots=("GK", "MID", "MID"), extra=()):
        packet, context = setup(slots, extra)
        return so.validate_answer(ans, packet, context, ELIG)

    def test_valid_answer_has_no_problems(self):
        self.assertEqual(self.check(answer([("GK", "g"), ("MID", "m1"), ("MID", "m2")])), [])

    def test_xi_may_start_own_add_but_not_own_drop(self):
        self.assertEqual(self.check(answer([("GK", "g"), ("MID", "fa"), ("MID", "m2")], [("m1", "fa")])), [])
        problems = self.check(answer([("GK", "g"), ("MID", "m1"), ("MID", "fa")], [("m1", "fa")]))
        self.assertEqual(len(problems), 1)
        self.assertIn("m1", problems[0])

    def test_unknown_id_and_bad_slot_reported_together(self):
        problems = self.check(answer([("GK", "g"), ("MID", "zz"), ("DEF", "m2")]))
        self.assertEqual(len(problems), 3)   # plus the MID slot the bad entry left empty
        self.assertTrue(any("empty" in p for p in problems))
        self.assertTrue(any("zz" in p for p in problems))
        self.assertTrue(any("DEF" in p for p in problems))

    def test_too_many_swaps(self):
        problems = self.check(answer([("GK", "g"), ("MID", "m1"), ("MID", "m2")],
                                     [("m3", "fa"), ("m2", "fa2"), ("m1", "fa3")]))
        self.assertTrue(any("swaps" in p for p in problems))

    def test_undroppable_and_ir_drop_rejected(self):
        problems = self.check(answer([("GK", "g"), ("MID", "m1"), ("MID", "m2")], [("keep", "fa"), ("ir", "fa2")]))
        self.assertEqual(len(problems), 2)

    def test_add_must_be_a_listed_free_agent(self):
        problems = self.check(answer([("GK", "g"), ("MID", "m1"), ("MID", "m2")], [("m3", "m1")]))
        self.assertTrue(problems)

    def test_ineligible_and_reserve_starters(self):
        problems = self.check(answer([("GK", "m3"), ("MID", "ir"), ("MID", "m2")]))
        self.assertEqual(len(problems), 2)

    def test_empty_slot_allowed_only_when_roster_cannot_fill_it(self):
        self.assertEqual(self.check(answer([("GK", "g"), ("MID", "m1"), ("MID", "m2")]),
                                    slots=("GK", "MID", "MID", "FWD")), [])
        problems = self.check(answer([("GK", "g"), ("MID", "m1")]))
        self.assertTrue(any("empty" in p for p in problems))

    def test_duplicate_player_in_xi(self):
        problems = self.check(answer([("GK", "g"), ("MID", "m1"), ("MID", "m1")]))
        self.assertTrue(any("twice" in p for p in problems))

    def test_schema_requires_every_field(self):
        self.assertEqual(set(so.ANSWER_SCHEMA["required"]), {"summary", "xi", "swaps", "matchup_notes"})
        self.assertFalse(so.ANSWER_SCHEMA["additionalProperties"])
        self.assertFalse(so.ANSWER_SCHEMA["properties"]["xi"]["items"]["additionalProperties"])


GOOD = json.dumps(answer([("GK", "s1"), ("MID", "s2"), ("MID", "s3")]))
BAD = json.dumps(answer([("GK", "s1"), ("MID", "s2"), ("MID", "s9")]))


class FakeCall:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        text, stop = self.replies.pop(0) if self.replies else (GOOD, "end_turn")
        return so.Reply(text, stop, 1000, 200)


class RunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self._tmp.name))
        self.cfg.stats_source = "sleeper"
        self.cfg.second_opinion = {"enabled": True, "model": "claude-opus-5-5", "effort": "high", "lead_hours": 48}
        self.conn = connect(":memory:")
        seed_sleeper(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def rows(self):
        return self.conn.execute("SELECT COUNT(*) FROM second_opinion_log").fetchone()[0]

    def test_not_due_makes_no_call(self):
        self.cfg.second_opinion["lead_hours"] = 24   # first kickoff is ~39.5 hours after NOW
        call, refresh = FakeCall(), mock.Mock()
        self.assertIn("not due", so.run_second_opinion(self.conn, self.cfg, NOW, call=call, refresh=refresh))
        self.assertEqual((len(call.requests), self.rows()), (0, 0))
        refresh.assert_not_called()

    def test_due_saves_one_row_and_second_run_skips(self):
        call, refresh = FakeCall(), mock.Mock()
        status = so.run_second_opinion(self.conn, self.cfg, NOW, call=call, refresh=refresh)
        self.assertEqual(status, "second opinion: GW9 saved (1000/200 tokens)")
        refresh.assert_called_once()
        row = self.conn.execute("SELECT season, gw, first_kickoff, model, xi_json FROM second_opinion_log").fetchone()
        self.assertEqual(tuple(row[:4]), ("2026", 9, "2026-10-24T11:30:00Z", "claude-opus-5-5"))
        self.assertEqual(json.loads(row[4])[0]["player_id"], "s1")
        self.assertIn("not due", so.run_second_opinion(self.conn, self.cfg, NOW, call=call))
        self.assertEqual(len(call.requests), 1)
        so.run_second_opinion(self.conn, self.cfg, NOW, call=call, force=True)
        self.assertEqual(self.rows(), 2)

    def test_request_shape(self):
        call = FakeCall()
        so.run_second_opinion(self.conn, self.cfg, NOW, call=call)
        r = call.requests[0]
        self.assertEqual(r["model"], "claude-opus-5-5")
        self.assertEqual(r["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", r["betas"])
        self.assertEqual(r["thinking"], {"type": "adaptive"})
        self.assertEqual(r["output_config"]["effort"], "high")
        self.assertIs(r["output_config"]["format"]["schema"], so.ANSWER_SCHEMA)
        self.assertNotIn("temperature", r)
        self.assertEqual(json.loads(r["messages"][0]["content"])["gameweek"], 9)
        self.assertIn("reputation", r["system"])

    def test_invalid_then_valid_retries_once(self):
        call = FakeCall((BAD, "end_turn"), (GOOD, "end_turn"))
        so.run_second_opinion(self.conn, self.cfg, NOW, call=call)
        self.assertEqual(len(call.requests), 2)
        msgs = call.requests[1]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[1]["content"], BAD)
        self.assertIn("s9", msgs[2]["content"])
        self.assertEqual(self.rows(), 1)

    def test_invalid_twice_raises_and_saves_nothing(self):
        call = FakeCall((BAD, "end_turn"), (BAD, "end_turn"))
        with self.assertRaisesRegex(BuildError, "s9"):
            so.run_second_opinion(self.conn, self.cfg, NOW, call=call)
        self.assertEqual(self.rows(), 0)

    def test_refusal_or_bad_json_raises(self):
        for reply in ((GOOD, "refusal"), (GOOD, "max_tokens"), ("not json", "end_turn")):
            with self.assertRaises(BuildError):
                so.run_second_opinion(self.conn, self.cfg, NOW, call=FakeCall(reply), force=True)
        self.assertEqual(self.rows(), 0)

    def test_json_that_is_not_an_object_raises(self):
        for text in ("[]", "null", '"text"'):
            with self.assertRaisesRegex(BuildError, "JSON object"):
                so.run_second_opinion(self.conn, self.cfg, NOW, call=FakeCall((text, "end_turn")), force=True)
        self.assertEqual(self.rows(), 0)

    def test_saves_the_model_that_served_the_answer(self):
        def call(request):
            return so.Reply(GOOD, "end_turn", 1000, 200, "claude-sonnet-5-5")
        so.run_second_opinion(self.conn, self.cfg, NOW, call=call)
        self.assertEqual(self.conn.execute("SELECT model FROM second_opinion_log").fetchone()[0], "claude-sonnet-5-5")

    def test_usage_counts_every_attempt_of_a_fallback(self):
        ns = SimpleNamespace
        plain = ns(input_tokens=500, output_tokens=50, iterations=None)
        self.assertEqual(so._usage(plain, "claude-opus-5-5"), (500, 50, "claude-opus-5-5"))
        fell_back = ns(input_tokens=520, output_tokens=300, iterations=[
            ns(type="message", input_tokens=500, output_tokens=40),
            ns(type="fallback_message", input_tokens=520, output_tokens=300, model="claude-sonnet-5-5")])
        self.assertEqual(so._usage(fell_back, "claude-opus-5-5"), (1020, 340, "claude-sonnet-5-5"))
        compacted = ns(input_tokens=500, output_tokens=50, iterations=[
            ns(type="compaction", input_tokens=9000, output_tokens=900),
            ns(type="message", input_tokens=500, output_tokens=50)])
        self.assertEqual(so._usage(compacted, "m"), (500, 50, "m"))

    def test_failed_runs_stop_after_two_attempts(self):
        for _ in range(2):
            with self.assertRaises(BuildError):
                so.run_second_opinion(self.conn, self.cfg, NOW, call=FakeCall(("not json", "end_turn")))
        call = FakeCall()
        self.assertIn("not due", so.run_second_opinion(self.conn, self.cfg, NOW, call=call))
        self.assertEqual(call.requests, [])
        so.run_second_opinion(self.conn, self.cfg, NOW, call=call, force=True)
        self.assertEqual(self.rows(), 1)

    def test_disabled_does_nothing(self):
        self.cfg.second_opinion["enabled"] = False
        call = FakeCall()
        self.assertIn("disabled", so.run_second_opinion(self.conn, self.cfg, NOW, call=call, force=True))
        self.assertEqual(call.requests, [])

    def test_missing_package_message(self):
        real = __import__

        def fake_import(name, *a, **k):
            if name == "anthropic":
                raise ImportError("no anthropic")
            return real(name, *a, **k)

        with mock.patch("builtins.__import__", fake_import), mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
            with self.assertRaisesRegex(BuildError, "requirements.txt"):
                so._default_call({})

    def test_missing_key_message(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(BuildError, "ANTHROPIC_API_KEY"):
                so._default_call({})
