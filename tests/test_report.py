import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sleeperfpl.config import Config, ConfigError
from sleeperfpl.db import connect, meta_set
from sleeperfpl.report import BuildError, build_report, render_html, write_outputs

NOW = datetime(2026, 10, 22, 20, tzinfo=timezone.utc)
TS = "2026-10-22T06:00:00Z"


def make_cfg(tmp, owner="me"):
    smap = tmp / "scoring_map.json"
    smap.write_text(json.dumps({"rules": {
        "goal": {"fpl_field": "goals_scored", "points": 5},
        "app_60": {"fpl_field": "minutes", "points": 2, "min": 60},
        "shots": {"fpl_field": None, "points": 1},
    }}))
    return Config(league_id="L1", owner=owner, db_path=tmp / "x.db", output_dir=tmp / "site",
                  scoring_map_path=smap, overrides_path=tmp / "overrides.json",
                  difficulty_multipliers={1: 1.2, 2: 1.1, 3: 1.0, 4: 0.9, 5: 0.8}, avg_window=5,
                  free_agents_per_position=10, swap_min_gain=1.0, max_swaps=3,
                  slot_eligibility={"GK": ["GK"], "DEF": ["DEF"], "MID": ["MID"], "FWD": ["FWD"]},
                  fpl_request_interval=0, stats_source="fpl", undroppable=[])


def seed(conn, ts=TS, roster=("s1", "s2", "s3", "s4"), names=None):
    names = names or {}
    league = {"sport": "s", "roster_positions": ["GK", "MID", "MID", "BN", "BN"], "scoring_settings": {}}
    conn.execute("INSERT INTO sleeper_league VALUES (?,?,?)", (ts, "L1", json.dumps(league)))
    conn.executemany("INSERT INTO sleeper_users (fetched_at, user_id, display_name) VALUES (?,?,?)", [(ts, "u1", "me"), (ts, "u2", "rival")])
    players = [("s1", "David Raya", "ARS", ["GK"], None), ("s2", "Bukayo Saka", "ARS", ["MID"], None),
               ("s3", "Declan Rice", "ARS", ["MID"], None), ("s4", "New Signing", "ARS", ["MID"], None),
               ("s5", "Cole Palmer", "CHE", ["MID"], None), ("s6", "Marc Cucurella", "CHE", ["DEF"], None)]
    conn.executemany("INSERT INTO sleeper_players VALUES (?,?,?,?,?,?,?,?)",
                     [(ts, pid, names.get(pid, n), t, json.dumps(pos), "Active", inj, 1)
                      for pid, n, t, pos, inj in players])
    conn.executemany("INSERT INTO sleeper_rosters (fetched_at, roster_id, owner_id, players_json, starters_json, "
                     "reserve_json) VALUES (?,?,?,?,?,?)",
                     [(ts, 1, "u1", json.dumps(list(roster)), "[]", "[]"), (ts, 2, "u2", "[]", "[]", "[]")])
    conn.execute("INSERT INTO sleeper_trending VALUES (?,?,?,?)", (ts, "add", "s5", 40))
    elements = [(1, "David", "Raya", "Raya", 1, 1), (2, "Bukayo", "Saka", "Saka", 1, 3),
                (3, "Declan", "Rice", "Rice", 1, 3), (5, "Cole", "Palmer", "Palmer", 2, 3),
                (6, "Marc", "Cucurella", "Cucurella", 2, 2)]
    conn.executemany("INSERT INTO fpl_elements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(ts, fid, f, s, w, t, et, "a", None, 900, None, None, None, None)
                      for fid, f, s, w, t, et in elements])
    conn.executemany("INSERT INTO fpl_teams (fetched_at, team_id, short_name, name) VALUES (?,?,?,?)", [(ts, 1, "ARS", "Arsenal"), (ts, 2, "CHE", "Chelsea")])
    conn.executemany("INSERT INTO fpl_events VALUES (?,?,?,?)", [(ts, 1, "2026-08-15T10:00:00Z", 1), (ts, 8, "2026-10-17T10:00:00Z", 1),
                                                                 (ts, 9, "2026-10-24T10:00:00Z", 0)])
    conn.execute("INSERT INTO fpl_fixtures VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (ts, 50, 9, 1, 2, 4, 3, "2026-10-24T11:30:00Z", 0, None, None))
    # goals per match: Saka 1 (5+2=7), Rice 0 (2), Palmer 2 (12), Raya 0 (2), Cucurella 0 (2)
    goals = {1: 0, 2: 1, 3: 0, 5: 2, 6: 0}
    rows = []
    for fid, g in goals.items():
        for k in range(3):
            stats = {"goals_scored": g, "minutes": 90}
            rows.append(("2026", fid, 100 + k, 6 + k, f"2026-10-{10 + k}T14:00:00Z", 90, json.dumps(stats), ts))
    rows.append(("2026", 2, 99, 5, "2026-10-01T14:00:00Z", 0, json.dumps({"goals_scored": 0, "minutes": 0}), ts))
    conn.executemany("INSERT OR REPLACE INTO fpl_history VALUES (?,?,?,?,?,?,?,?)", rows)
    meta_set(conn, "history_last_gw", "8")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.conn = connect(":memory:")

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_data_raises(self):
        with self.assertRaisesRegex(BuildError, "fetch-daily"):
            build_report(self.conn, self.cfg, NOW)

    def test_owner_not_found_lists_names(self):
        seed(self.conn)
        with self.assertRaisesRegex(BuildError, "rival"):
            build_report(self.conn, make_cfg(self.tmp, owner="nobody"), NOW)

    def test_unknown_slot_raises_config_error(self):
        seed(self.conn)
        self.conn.execute("UPDATE sleeper_league SET data_json = ?",
                          (json.dumps({"roster_positions": ["GK", "SUPER_FLEX"], "scoring_settings": {}}),))
        with self.assertRaisesRegex(ConfigError, "SUPER_FLEX"):
            build_report(self.conn, self.cfg, NOW)

    def test_report_shape(self):
        seed(self.conn)
        r = build_report(self.conn, self.cfg, NOW)
        self.assertEqual((r["gameweek"], r["mode"]), (9, "instant"))
        xi = {s["slot"] + str(i): s["player"]["name"] for i, s in enumerate(r["xi"]["starters"])}
        self.assertEqual(xi["GK0"], "David Raya")
        starters = [s["player"]["name"] for s in r["xi"]["starters"]]
        self.assertIn("Bukayo Saka", starters)
        saka = next(s["player"] for s in r["xi"]["starters"] if s["player"]["name"] == "Bukayo Saka")
        self.assertEqual((saka["avg"], saka["n"]), (7.0, 3))   # 0-minute match skipped
        self.assertEqual(saka["proj"], 6.3)                    # 7 * 0.9 (home vs difficulty 4)
        self.assertEqual(saka["fixtures"][0], {"opponent": "CHE", "home": True, "difficulty": 4,
                                               "kickoff": "2026-10-24T11:30:00Z"})
        self.assertEqual(len(r["unmatched_rostered"]), 1)
        self.assertEqual(r["unmatched_rostered"][0]["flag"], "no data")
        self.assertEqual(r["coverage"]["missing"], ["shots"])
        self.assertEqual(r["free_agents"]["MID"][0]["name"], "Cole Palmer")
        self.assertEqual(r["free_agents"]["MID"][0]["trending_adds"], 40)
        self.assertEqual(r["swaps"][0]["add"]["name"], "Cole Palmer")
        self.assertEqual(r["sources"]["fpl_history_gw"], "8")
        json.dumps(r)

    def test_build_report_fills_context_and_skips_logs_when_asked(self):
        seed(self.conn)
        self.cfg.stats_source = "sleeper"
        league = {"sport": "s", "season": "2026", "roster_positions": ["GK", "MID", "MID", "BN", "BN"],
                  "scoring_settings": {}}
        self.conn.execute("INSERT INTO sleeper_league VALUES (?,?,?)", ("2026-10-22T07:00:00Z", "L1", json.dumps(league)))
        self.conn.execute("INSERT INTO sleeper_stats VALUES (?,?,?,?,?)", ("2026", 8, "s2", json.dumps({"min": 90}), TS))
        c = {}
        build_report(self.conn, self.cfg, NOW, log=False, context=c)
        self.assertEqual(c["ctx"]["s2"].name, "Bukayo Saka")
        self.assertEqual(c["gw"].gw, 9)
        self.assertEqual(c["season"], "2026")
        self.assertIn("fodds", c)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM recommendation_log").fetchone()[0], 0)
        build_report(self.conn, self.cfg, NOW)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM recommendation_log").fetchone()[0], 1)

    def add_opinion(self, xi, swaps=(), gw=9, reason="Starts every week"):
        self.conn.execute("INSERT INTO second_opinion_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          ("2026-10-22T19:00:00Z", "", gw, "2026-10-24T11:30:00Z", "claude-opus-5-5",
                           json.dumps([{"player_id": pid, "slot": slot, "reason": reason} for slot, pid in xi]),
                           json.dumps([{"drop_id": d, "add_id": a, "reason": "Better fixture"} for d, a in swaps]),
                           json.dumps([{"fixture": "ARS v CHE", "note": "Chelsea unbeaten in 3"}]),
                           "Back the Arsenal core", 1000, 200))

    def test_no_second_opinion_is_none_and_page_renders(self):
        seed(self.conn)
        r = build_report(self.conn, self.cfg, NOW)
        self.assertIsNone(r["second_opinion"])
        self.assertNotIn("second opinion", render_html(r))

    def test_second_opinion_rendered_with_agreement(self):
        seed(self.conn)
        self.add_opinion([("GK", "s1"), ("MID", "s2"), ("MID", "s4")], [("s3", "s5")])
        r = build_report(self.conn, self.cfg, NOW)
        so = r["second_opinion"]
        self.assertEqual((so["agree_xi"], so["xi_size"]), (2, 3))
        self.assertEqual([x["in_tool_xi"] for x in so["xi"]], [True, True, False])
        self.assertEqual(so["xi"][0]["name"], "David Raya")
        self.assertEqual(so["swaps"][0]["add"]["name"], "Cole Palmer")
        self.assertEqual(so["changed"], [])
        page = render_html(r)
        self.assertIn("Claude&#x27;s second opinion", page)
        self.assertIn("Agrees on 2 of 3 starters", page)
        self.assertIn("Starts every week", page)
        self.assertIn("Chelsea unbeaten in 3", page)
        self.assertIn("Claude only", page)
        self.assertEqual(page.count("also in tool XI"), 2)
        json.dumps(r)

    def test_opinion_for_another_week_is_ignored(self):
        seed(self.conn)
        self.add_opinion([("GK", "s1")], gw=8)
        self.assertIsNone(build_report(self.conn, self.cfg, NOW)["second_opinion"])

    def test_changed_lists_players_now_elsewhere(self):
        seed(self.conn)
        self.conn.execute("UPDATE sleeper_rosters SET players_json = '[\"s6\"]' WHERE roster_id = 2")
        self.add_opinion([("GK", "s1"), ("MID", "s6"), ("MID", "s99")])
        so = build_report(self.conn, self.cfg, NOW)["second_opinion"]
        self.assertEqual(so["changed"], ["Marc Cucurella", "s99"])
        self.assertIn("Roster changes since this opinion: Marc Cucurella, s99", render_html(build_report(self.conn, self.cfg, NOW)))

    def test_own_add_now_on_my_roster_is_not_a_change(self):
        seed(self.conn, roster=("s1", "s2", "s3", "s5"))
        self.add_opinion([("GK", "s1"), ("MID", "s2"), ("MID", "s5")], [("s4", "s5")])
        self.assertEqual(build_report(self.conn, self.cfg, NOW)["second_opinion"]["changed"], [])

    def test_reason_text_is_escaped(self):
        seed(self.conn)
        self.add_opinion([("GK", "s1")], reason="<script>x</script>")
        page = render_html(build_report(self.conn, self.cfg, NOW))
        self.assertNotIn("<script>x", page)
        self.assertIn("&lt;script&gt;x", page)

    def test_claude_column_only_with_data(self):
        from sleeperfpl.report import _report_card
        week = {"gw": 8, "n": 5, "mae_v1": 1.0, "mae_v1_played": 1.0, "mae_v2": None, "rank_v1": None,
                "brier_goal": None, "n_goal": 0}
        dec = {"gw": 8, "set_pts": 5, "tool_pts": 6, "best_pts": 9, "swaps": [], "claude_pts": None, "claude_swaps": []}
        r = {"report_card": {"weeks": [week], "decisions": [dec], "matches": [], "labels": [], "summary": "s"}}
        self.assertNotIn("Claude", _report_card(r))
        dec["claude_pts"] = 7
        html_ = _report_card(r)
        self.assertIn("Claude XI (followed in full)", html_)
        self.assertIn('<td class="num">7.0</td>', html_)

    def test_week_in_progress_banner(self):
        seed(self.conn)
        self.assertIsNone(build_report(self.conn, self.cfg, NOW)["in_progress"])
        self.conn.execute("INSERT INTO fpl_events VALUES (?,?,?,?)", (TS, 10, "2026-10-31T10:00:00Z", 0))
        self.conn.execute("INSERT INTO fpl_fixtures VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (TS, 51, 10, 2, 1, 3, 4, "2026-10-31T15:00:00Z", 0, None, None))
        r = build_report(self.conn, self.cfg, datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
        self.assertEqual((r["gameweek"], r["mode"]), (10, "waiver"))
        self.assertEqual(r["in_progress"], {"gameweek": 9, "matches_left": 1})
        self.assertIn("GW9 is still being played (1 match left)", render_html(r))

    def test_uses_latest_snapshot_after_failed_fetch(self):
        seed(self.conn, ts="2026-10-21T06:00:00Z", roster=("s1", "s6"))
        seed(self.conn, ts=TS)
        r = build_report(self.conn, self.cfg, NOW)
        names = [s["player"]["name"] for s in r["xi"]["starters"] if s["player"]]
        self.assertNotIn("Marc Cucurella", names)
        self.assertEqual(r["sources"]["sleeper_rosters"], TS)

    def test_injured_player_flagged_out(self):
        seed(self.conn)
        self.conn.execute("UPDATE fpl_elements SET status = 'i' WHERE fpl_id = 2")
        r = build_report(self.conn, self.cfg, NOW)
        everyone = [s["player"] for s in r["xi"]["starters"] if s["player"]] + r["xi"]["bench"]
        saka = next(p for p in everyone if p["name"] == "Bukayo Saka")
        self.assertEqual((saka["flag"], saka["proj"]), ("out", 0.0))

    def everyone(self, r):
        return [s["player"] for s in r["xi"]["starters"] if s["player"]] + r["xi"]["bench"]

    def test_sleeper_out_fpl_available_not_zeroed(self):
        seed(self.conn)
        self.conn.execute("UPDATE sleeper_players SET injury_status = 'Out' WHERE player_id = 's3'")
        r = build_report(self.conn, self.cfg, NOW)
        rice = next(p for p in self.everyone(r) if p["name"] == "Declan Rice")
        self.assertEqual(rice["flag"], "Sleeper: out")
        self.assertGreater(rice["proj"], 0)
        starters = [s["player"]["name"] for s in r["xi"]["starters"] if s["player"]]
        self.assertIn("Declan Rice", starters)

    def test_fpl_injured_zero_is_out(self):
        seed(self.conn)
        self.conn.execute("UPDATE fpl_elements SET status = 'i', chance_next = 0 WHERE fpl_id = 2")
        saka = next(p for p in self.everyone(build_report(self.conn, self.cfg, NOW)) if p["name"] == "Bukayo Saka")
        self.assertEqual((saka["flag"], saka["proj"]), ("out", 0.0))
        self.conn.execute("UPDATE fpl_elements SET status = 'i', chance_next = 75 WHERE fpl_id = 2")
        saka = next(p for p in self.everyone(build_report(self.conn, self.cfg, NOW)) if p["name"] == "Bukayo Saka")
        self.assertEqual(saka["flag"], "doubtful")
        self.assertGreater(saka["proj"], 0)

    def test_chance_this_used_after_deadline(self):
        seed(self.conn)
        self.conn.execute("UPDATE fpl_elements SET status = 'd', chance_next = 0, chance_this = 100 WHERE fpl_id = 2")
        before = next(p for p in self.everyone(build_report(self.conn, self.cfg, NOW)) if p["name"] == "Bukayo Saka")
        self.assertEqual(before["flag"], "out")
        after_deadline = datetime(2026, 10, 24, 10, 30, tzinfo=timezone.utc)   # deadline 10:00, kickoff 11:30
        r = build_report(self.conn, self.cfg, after_deadline)
        self.assertEqual(r["gameweek"], 9)
        saka = next(p for p in self.everyone(r) if p["name"] == "Bukayo Saka")
        self.assertNotEqual(saka["flag"], "out")
        self.assertGreater(saka["proj"], 0)

    def test_html_escapes_and_labels_waiver(self):
        seed(self.conn, names={"s4": "<b>New</b>"})
        r = build_report(self.conn, self.cfg, NOW)
        r["mode"] = "waiver"
        page = render_html(r)
        self.assertNotIn("<b>New</b>", page)
        self.assertIn("&lt;b&gt;New&lt;/b&gt;", page)
        self.assertIn("Waiver claim:", page)
        self.assertIn('name="viewport"', page)

    def test_report_card_html(self):
        seed(self.conn)
        r = build_report(self.conn, self.cfg, NOW)
        week = {"gw": 8, "n": 40, "mae_v1": 3.1, "mae_v1_played": 2.5, "mae_v2": 2.0, "mae_v1_paired": 2.7,
                "n_v2": 12, "rank_v1": 0.42, "brier_goal": None, "n_goal": 0}
        r["report_card"] = {"weeks": [week], "summary": "not enough weeks yet", "matches": [],
                            "decisions": [{"gw": 8, "set_pts": 7, "tool_pts": 13, "best_pts": 15, "swaps": []}]}
        page = render_html(r)
        for text in ("Order", "0.42", "Your XI", ">13.0<", ">15.0<", "2.0 / 2.7 <small>(12)</small>"):
            self.assertIn(text, page)
        self.assertNotIn("Log loss", page)
        r["report_card"]["matches"] = [{"gw": 8, "n": 10, "log_loss": 0.98, "rps": 0.2, "brier_cs": 0.21}]
        self.assertIn("Log loss", render_html(r))

    def test_report_card_html_minutes_and_labels(self):
        seed(self.conn)
        r = build_report(self.conn, self.cfg, NOW)
        week = {"gw": 8, "n": 40, "mae_v1": 3.1, "mae_v1_played": 2.5, "mae_v2": None, "rank_v1": 0.42,
                "brier_goal": None, "n_goal": 0}
        r["report_card"] = {"weeks": [week], "summary": "not enough weeks yet", "matches": [], "decisions": [],
                            "labels": []}
        page = render_html(r)
        self.assertNotIn("With minutes", page)
        self.assertNotIn("how often flagged players played", page)
        week.update(n_v1m=38, mae_v1m=2.9, mae_v1_v1m=3.0, rank_v1m=0.45, rank_v1_v1m=0.40, brier_start=0.141,
                    n_minutes=38)
        r["report_card"]["labels"] = [{"kind": "sleeper", "cell": "a100+out", "n": 12, "start_rate": 0.5,
                                       "app_rate": 0.75, "sixty_rate": 0.5, "app_lo": 0.52, "app_hi": 0.9}]
        page = render_html(r)
        for text in ("With minutes", "2.9 / 3.0 · 0.45 / 0.40", "0.141", "how often flagged players played",
                     "Sleeper says Out, FPL says available", "52–90%"):
            self.assertIn(text, page)

    def test_approximate_banner(self):
        seed(self.conn)
        page = render_html(build_report(self.conn, self.cfg, NOW))
        self.assertIn("Approximate", page)
        self.assertIn("shots", page)

    def test_write_outputs_creates_both_files(self):
        seed(self.conn)
        r = build_report(self.conn, self.cfg, NOW)
        write_outputs(r, self.tmp / "site")
        self.assertEqual(json.loads((self.tmp / "site" / "report.json").read_text())["gameweek"], 9)
        self.assertIn("<html", (self.tmp / "site" / "index.html").read_text())


class ReviewFixReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.conn = connect(":memory:")

    def tearDown(self):
        self._tmp.cleanup()

    def test_rostered_id_missing_from_players_is_listed(self):
        seed(self.conn, roster=("s1", "s2", "s3", "s9"))
        r = build_report(self.conn, self.cfg, NOW)
        ids = [p["sleeper_id"] for p in r["unmatched_rostered"]]
        self.assertIn("s9", ids)
        self.assertEqual(next(p for p in r["unmatched_rostered"] if p["sleeper_id"] == "s9")["flag"], "no data")

    def test_inactive_or_clubless_free_agent_hidden(self):
        seed(self.conn)
        self.conn.execute("UPDATE sleeper_players SET active = 0 WHERE player_id = 's5'")
        r = build_report(self.conn, self.cfg, NOW)
        self.assertNotIn("Cole Palmer", [p["name"] for p in r["free_agents"].get("MID", [])])

    def test_override_to_missing_fpl_id_is_unmatched_not_dropped(self):
        seed(self.conn)
        self.cfg.overrides_path.write_text('{"players": {"s3": 999}}')
        r = build_report(self.conn, self.cfg, NOW)
        self.assertIn("s3", [p["sleeper_id"] for p in r["unmatched_rostered"]])
        self.assertNotIn("s3", [s["drop"]["sleeper_id"] for s in r["swaps"]])

    def test_previous_season_history_ignored(self):
        seed(self.conn)
        self.conn.execute("INSERT INTO fpl_history VALUES ('2025', 2, 100, 30, '2026-05-01T14:00:00Z', 90, ?, ?)",
                          (json.dumps({"goals_scored": 9, "minutes": 90}), TS))
        r = build_report(self.conn, self.cfg, NOW)
        saka = next(s["player"] for s in r["xi"]["starters"] if s["player"]["name"] == "Bukayo Saka")
        self.assertEqual((saka["avg"], saka["n"]), (7.0, 3))

    def test_league_scoring_drift_reported(self):
        seed(self.conn)
        league = {"sport": "s", "roster_positions": ["GK", "MID", "MID"],
                  "scoring_settings": {"goal": 6, "app_60": 2, "shots": 1, "tackle": 1}}
        self.conn.execute("UPDATE sleeper_league SET data_json = ?", (json.dumps(league),))
        r = build_report(self.conn, self.cfg, NOW)
        self.assertIn("tackle", r["coverage"]["missing"])
        self.assertEqual(r["coverage"]["changed"], ["goal"])
        self.assertIn("goal", render_html(r))


class SleeperSourceReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.cfg.stats_source = "sleeper"
        self.conn = connect(":memory:")
        seed(self.conn)
        league = {"sport": "s", "season": "2026", "roster_positions": ["GK", "MID", "MID", "BN", "BN"],
                  "scoring_settings": {"pos_m_g": 9, "pos_m_sot": 2, "pos_gk_sv": 2}}
        self.conn.execute("UPDATE sleeper_league SET data_json = ?", (json.dumps(league),))

    def tearDown(self):
        self._tmp.cleanup()

    def add_stats(self, pid, week, stats):
        self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', ?, ?, ?, ?)", (week, pid, json.dumps(stats), TS))

    def player(self, r, name):
        everyone = [s["player"] for s in r["xi"]["starters"] if s["player"]] + r["xi"]["bench"]
        return next(p for p in everyone if p["name"] == name)

    def test_uses_sleeper_points(self):
        self.add_stats("s2", 6, {"pos_m_g": 1, "pos_m_sot": 2, "min": 90})   # 13
        self.add_stats("s2", 7, {"pos_m_sot": 1, "min": 90})                 # 2
        r = build_report(self.conn, self.cfg, NOW)
        saka = self.player(r, "Bukayo Saka")
        self.assertEqual((saka["avg"], saka["n"]), (7.5, 2))
        self.assertEqual(r["stats_source"], "sleeper")
        self.assertEqual(r["coverage"]["missing"], [])
        self.assertNotIn("Approximate", render_html(r))

    def seed_minutes(self):
        for w in range(4, 9):
            self.add_stats("s2", w, {"pos_m_g": 1, "min": 90, "gs": 1})
            self.add_stats("s3", w, {"min": 20 if w % 2 else 0})

    def test_minutes_fields_in_report(self):
        self.seed_minutes()
        r = build_report(self.conn, self.cfg, NOW)
        saka = self.player(r, "Bukayo Saka")
        self.assertTrue(0 < saka["p_start"] <= saka["p_app"] <= 1)
        self.assertGreater(saka["p_start"], 0.7)
        self.assertIsNotNone(saka["proj_v1m"])
        self.assertEqual((saka["fpl_bucket"], saka["slp_bucket"]), ("a100", "none"))
        rice = self.player(r, "Declan Rice")
        self.assertLess(rice["p_start"], saka["p_start"])
        self.assertEqual(saka["proj"], saka["proj_v1"])          # shadow: picks unchanged
        self.assertIn("starts:", render_html(r))

    def test_minutes_on_uses_v1m(self):
        self.seed_minutes()
        self.cfg.minutes_model = "on"
        saka = self.player(build_report(self.conn, self.cfg, NOW), "Bukayo Saka")
        self.assertEqual(saka["proj"], saka["proj_v1m"])

    def test_builds_without_model(self):
        from unittest import mock
        from sleeperfpl import minutes_fit
        self.seed_minutes()
        with mock.patch.object(minutes_fit, "DEFAULT_PATH", self.tmp / "missing.json"):
            r = build_report(self.conn, self.cfg, NOW)
        saka = self.player(r, "Bukayo Saka")
        self.assertIsNone(saka["p_start"])
        self.assertIsNone(saka["proj_v1m"])
        self.assertNotIn("starts:", render_html(r))

    def test_falls_back_to_fpl_without_sleeper_stats(self):
        r = build_report(self.conn, self.cfg, NOW)
        self.assertEqual(r["stats_source"], "fpl")
        self.assertEqual(self.player(r, "Bukayo Saka")["n"], 3)

    def test_undroppable_by_name_and_unknown_warned(self):
        self.cfg.undroppable = ["declan rice", "Nobody Here"]
        r = build_report(self.conn, self.cfg, NOW)
        self.assertNotIn("s3", [s["drop"]["sleeper_id"] for s in r["swaps"]])
        self.assertTrue(self.player(r, "Declan Rice")["undroppable"])
        self.assertEqual(r["undroppable_unmatched"], ["Nobody Here"])
        page = render_html(r)
        self.assertIn("Nobody Here", page)
        self.assertIn("🔒", page)


from sleeperfpl.lineup import best_xi  # noqa: E402

SCHED = {"start_gw": 9, "end_gw": 31, "opponents": ["Rivals FC"]}


class MatchupReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.conn = connect(":memory:")
        seed(self.conn)
        self.conn.execute("UPDATE sleeper_users SET team_name = 'Rivals FC' WHERE user_id = 'u2'")
        self.opp(["s5", "s6"])

    def tearDown(self):
        self._tmp.cleanup()

    def opp(self, players, reserve=()):
        self.conn.execute("UPDATE sleeper_rosters SET players_json = ?, reserve_json = ? WHERE roster_id = 2",
                          (json.dumps(players), json.dumps(list(reserve))))

    def build(self, schedule=SCHED, context=None):
        self.cfg.schedule = schedule
        return build_report(self.conn, self.cfg, NOW, context=context)

    def test_projected_matchup_block(self):
        m = self.build()["matchup"]
        self.assertEqual((m["gw"], m["mode"], m["notice"]), (9, "projected", None))
        self.assertEqual((m["them"]["name"], m["them"]["username"], m["them"]["record"]), ("Rivals FC", "rival", "–"))
        ids = [s["player"]["sleeper_id"] for s in m["them"]["starters"] if s["player"]]
        self.assertEqual(ids, ["s5"])
        self.assertEqual(m["them"]["total"], round(sum(s["player"]["proj"] for s in m["them"]["starters"] if s["player"]), 1))
        self.assertEqual(m["me"]["name"], "me")
        self.assertEqual(len(m["me"]["starters"]), 3)

    def test_opponent_players_do_not_leak(self):
        without, c0 = self.build(schedule=None), {}
        self.build(schedule=None, context=c0)
        c1 = {}
        r = self.build(context=c1)
        self.assertEqual(r["free_agents"], without["free_agents"])
        self.assertEqual(r["swaps"], without["swaps"])
        self.assertEqual(r["xi"], without["xi"])
        self.assertEqual(set(c1["ctx"]), set(c0["ctx"]))
        self.assertFalse({"s5", "s6"} & set(c1["ctx"]))

    def test_opponent_ir_excluded(self):
        self.opp(["s5", "s6"], reserve=["s5"])
        m = self.build()["matchup"]
        self.assertFalse([s for s in m["them"]["starters"] if s["player"]])
        self.assertEqual([p["sleeper_id"] for p in m["them"]["bench"]], ["s6"])

    def test_empty_opponent_roster(self):
        self.opp([])
        m = self.build()["matchup"]
        self.assertEqual(m["them"]["total"], 0.0)
        self.assertTrue(all(s["player"] is None for s in m["them"]["starters"]))

    def test_after_swaps_total(self):
        self.opp(["s6"])
        c = {}
        m = self.build(context=c)["matchup"]
        self.assertTrue(c["swaps"])
        drops = {s.drop.sleeper_id for s in c["swaps"]}
        roster = [pl for pl in c["ctx"].values() if pl.on_roster and pl.sleeper_id not in drops]
        expected = best_xi(roster + [s.add for s in c["swaps"]], c["slots"], self.cfg.slot_eligibility).total
        self.assertEqual(m["after_swaps"], round(expected, 1))
        self.assertGreater(m["after_swaps"], m["me"]["total"])

    def test_no_schedule_no_matchup(self):
        r = self.build(schedule=None)
        self.assertIsNone(r["matchup"])
        self.assertTrue(all(m["mine"] is None and m["theirs"] is None for m in r["matches"]))

    def test_week_outside_schedule_no_matchup(self):
        self.assertIsNone(self.build(schedule={"start_gw": 10, "end_gw": 31, "opponents": ["Rivals FC"]})["matchup"])

    def test_unmatched_name_gives_notice(self):
        m = self.build(schedule={"start_gw": 9, "end_gw": 31, "opponents": ["Nobody"]})["matchup"]
        self.assertIn("Nobody", m["notice"])
        self.assertIsNone(m["me"])
        self.assertIsNone(m["them"])

    def test_badges_and_fixture_counts(self):
        self.conn.execute("UPDATE fpl_teams SET code = 3 WHERE short_name = 'ARS'")
        r = self.build()
        self.assertEqual(r["badges"], {"ARS": 3, "CHE": None})
        fx = r["matches"][0]
        self.assertEqual((fx["home"], fx["away"]), ("ARS", "CHE"))
        mine = sum(1 for s in r["matchup"]["me"]["starters"] if s["player"] and s["player"]["club"] in ("ARS", "CHE"))
        self.assertEqual((fx["mine"], fx["theirs"]), (mine, 1))

    def test_record_from_roster(self):
        self.conn.execute("UPDATE sleeper_rosters SET wins = 4, losses = 3, ties = 0 WHERE roster_id = 2")
        self.assertEqual(self.build()["matchup"]["them"]["record"], "4–3")


class MatchupPageTests(MatchupReportTests):
    def test_page_matchup_card(self):
        self.opp(["s6"])
        r = self.build()
        m, page = r["matchup"], render_html(r)
        self.assertIn("Rivals FC", page)
        self.assertIn("@rival", page)
        self.assertIn(f'{m["me"]["total"]:.1f}', page)
        self.assertIn(f'{m["them"]["total"]:.1f}', page)
        self.assertTrue(any(t in page for t in ("You lead by", "You trail by", "Level")))
        self.assertIn(f'With all {len(r["swaps"])} suggested swap', page)
        self.assertIn(f'{m["after_swaps"]:.1f}', page)

    def test_page_xis_pitch_and_list(self):
        r = self.build()
        page = render_html(r)
        self.assertIn('data-xi-view="pitch"', page)
        self.assertIn('data-xi-view="list"', page)
        self.assertIn('class="pitch"', page)
        self.assertIn(r["matchup"]["them"]["formation"], page)
        self.assertIn('data-view="list"', page)
        self.assertNotIn("<h2>Best XI</h2>", page)          # the list view holds it in projected mode
        self.assertIn("Bukayo Saka", page)

    def test_page_badges_with_fallback(self):
        self.conn.execute("UPDATE fpl_teams SET code = 3 WHERE short_name = 'ARS'")
        page = render_html(self.build())
        self.assertIn("badges/70/t3.png", page)
        self.assertIn("<b>ARS</b>", page)
        self.assertIn("<b>CHE</b>", page)
        self.assertNotIn("tNone", page)
        self.assertIn('onerror="this.remove()"', page)

    def test_page_has_view_switch_and_local_time_script(self):
        page = render_html(self.build())
        self.assertIn("localStorage", page)
        self.assertIn("'xi-view'", page)
        self.assertIn("toLocaleString", page)
        self.assertIn("[hidden]{display:none", page)

    def test_page_notice_only(self):
        page = render_html(self.build(schedule={"start_gw": 9, "end_gw": 31, "opponents": ["Nobody"]}))
        self.assertIn("Nobody", page)
        self.assertNotIn('class="pitch"', page)
        self.assertIn("<h2>Best XI</h2>", page)

    def test_page_without_schedule_unchanged_sections(self):
        page = render_html(self.build(schedule=None))
        for text in ("<h2>Best XI</h2>", "Swap tips", "Top free agents", "<h2>Fixtures</h2>"):
            self.assertIn(text, page)
        self.assertNotIn('class="card matchup"', page)
        self.assertNotIn('class="pitch"', page)


from sleeperfpl.pricing import poisson_probs  # noqa: E402


class MarketReportTests(unittest.TestCase):
    SCORING = {"pos_m_g": 9, "pos_m_sot": 2, "pos_m_cs": 1, "pos_gk_cs": 8, "pos_gk_ga": -2, "pos_gk_sv": 2,
               "pos_d_cs": 6, "pos_d_ga": -2}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.cfg.stats_source = "sleeper"
        self.conn = connect(":memory:")
        seed(self.conn)
        league = {"sport": "s", "season": "2026", "roster_positions": ["GK", "MID", "MID", "BN", "BN"],
                  "scoring_settings": self.SCORING}
        self.conn.execute("UPDATE sleeper_league SET data_json = ?", (json.dumps(league),))
        for week in (6, 7):
            self.stats("s1", week, {"min": 90, "pos_gk_cs": 1, "cs": 1, "pos_gk_sv": 3})
            self.stats("s2", week, {"min": 90, "pos_m_sot": 2, "g": 0})
            self.stats("s3", week, {"min": 90, "pos_m_sot": 1})
        self.stats("s2", 9, {"min": 90, "pos_m_g": 3, "g": 3})   # in-progress week: must be ignored

    def tearDown(self):
        self._tmp.cleanup()

    def stats(self, pid, week, st):
        self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', ?, ?, ?, ?)", (week, pid, json.dumps(st), TS))

    def quotes(self, fetched_at="2026-10-22T19:00:00Z", lh=1.8, la=1.0):
        pr = poisson_probs(lh, la)
        for market, side, p, line in (("1x2", "ARS", pr["home"], None), ("1x2", "TIE", pr["draw"], None),
                                      ("1x2", "CFC", pr["away"], None), ("total", "over", pr["over"][2.5], 2.5)):
            self.conn.execute("INSERT INTO market_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              (fetched_at, "kalshi", "ARS", "CFC", "2026-10-24T11:30:00Z", market, line,
                               "draw" if side == "TIE" else side, p - 0.01, p + 0.01, 500, "t"))

    def player(self, r, name):
        everyone = [s["player"] for s in r["xi"]["starters"] if s["player"]] + r["xi"]["bench"]
        return next(p for p in everyone if p["name"] == name)

    def test_matches_section_from_quotes(self):
        self.quotes()
        r = build_report(self.conn, self.cfg, NOW)
        m = r["matches"][0]
        self.assertEqual((m["home"], m["away"], m["sources"], m["stale"]), ("ARS", "CHE", ["kalshi"], False))
        self.assertGreater(m["p_home"], m["p_away"])
        self.assertEqual(r["markets_unmatched"], [])

    def test_page_fixtures_keep_market_details(self):
        self.quotes()
        r = build_report(self.conn, self.cfg, NOW)
        page = render_html(r)
        self.assertIn('<time datetime="2026-10-24T11:30:00Z">', page)
        self.assertIn("xG 1.80", page)
        self.assertIn("clean sheet", page)
        self.assertIn("Draw", page)
        self.assertIn("kalshi", page)
        self.assertIn("Arsenal", page)

    def test_projection_uses_market_when_available(self):
        self.quotes()
        r = build_report(self.conn, self.cfg, NOW)
        saka = self.player(r, "Bukayo Saka")
        self.assertEqual(saka["proj_source"], "market")
        self.assertNotEqual(saka["proj"], saka["proj_v1"])
        self.assertEqual(saka["n"], 2)   # week 9 (in progress) excluded
        self.assertEqual(r["projection_note"], "market")

    def test_fixture_without_market_uses_v1(self):
        r = build_report(self.conn, self.cfg, NOW)
        saka = self.player(r, "Bukayo Saka")
        self.assertEqual((saka["proj_source"], saka["proj"]), ("difficulty", saka["proj_v1"]))
        self.assertEqual(r["projection_note"], "difficulty")
        self.assertIsNone(r["matches"][0]["market"])

    def test_stale_quotes_marked_and_unused(self):
        self.quotes(fetched_at="2026-10-21T12:00:00Z")
        r = build_report(self.conn, self.cfg, NOW)
        self.assertTrue(r["matches"][0]["stale"])
        self.assertEqual(self.player(r, "Bukayo Saka")["proj_source"], "difficulty")

    def test_cs_flag(self):
        self.quotes(lh=1.8, la=1.6)
        r = build_report(self.conn, self.cfg, NOW)
        self.assertIn("cs", self.player(r, "David Raya")["market_flags"])

    def test_goal_flag_from_player_quote(self):
        self.quotes()
        self.conn.execute("INSERT INTO player_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          ("2026-10-22T19:00:00Z", "kalshi", "ARS", "CFC", "2026-10-24T11:30:00Z", "Bukayo Saka",
                           "ARS", "goal1", 0.59, 0.61, 0.6, 500, "t"))
        r = build_report(self.conn, self.cfg, NOW)
        saka = self.player(r, "Bukayo Saka")
        self.assertAlmostEqual(saka["p_goal_market"], 0.6)
        self.assertEqual(saka["p_goal_hist"], 0.0)
        self.assertIn("goal", saka["market_flags"])

    def test_html_shows_match_card_and_flag(self):
        self.quotes(lh=1.8, la=1.6)
        page = render_html(build_report(self.conn, self.cfg, NOW))
        self.assertIn("<h2>Fixtures</h2>", page)
        self.assertIn("⚑", page)
        self.assertIn("mkt", page)
        self.assertIn("Report card", page)

    def test_build_logs_projections(self):
        self.quotes()
        build_report(self.conn, self.cfg, NOW)
        n = self.conn.execute("SELECT COUNT(*) FROM projection_log WHERE gw = 9 AND v2 IS NOT NULL").fetchone()[0]
        self.assertGreater(n, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM recommendation_log").fetchone()[0], 1)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT fixture_id FROM match_log")], [50])


class LiveReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.cfg.stats_source = "sleeper"
        self.conn = connect(":memory:")
        seed(self.conn)
        league = {"sport": "s", "season": "2026", "roster_positions": ["GK", "MID", "MID", "BN", "BN"],
                  "scoring_settings": {"pos_m_g": 9}}
        self.conn.execute("UPDATE sleeper_league SET data_json = ?", (json.dumps(league),))
        self.conn.execute("UPDATE sleeper_rosters SET starters_json = ? WHERE owner_id = 'u1'",
                          (json.dumps(["s1", "s2", "s3"]),))
        for pid, minutes in (("s1", 90), ("s2", 0), ("s3", 90)):   # fetched after the 11:30 match ended
            self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 9, ?, ?, '2026-10-24T14:30:00Z')",
                              (pid, json.dumps({"min": minutes})))
        self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 7, 's2', ?, ?)", (json.dumps({"min": 90}), TS))

    def tearDown(self):
        self._tmp.cleanup()

    def test_report_live_block_only_when_in_progress(self):
        self.assertIsNone(build_report(self.conn, self.cfg, NOW)["live"])
        after = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)
        r = build_report(self.conn, self.cfg, after)
        live = r["live"]
        self.assertEqual(live["gameweek"], 9)
        self.assertEqual([s["player"]["sleeper_id"] for s in live["starters"]], ["s1", "s2", "s3"])
        self.assertEqual([x["starter"]["sleeper_id"] for x in live["replacements"]], ["s2"])
        self.assertIsNone(live["replacements"][0]["bench"])
        page = render_html(r)
        self.assertIn("This week (live)", page)

    def test_no_replacement_without_stats_fetched_after_the_match(self):
        self.conn.execute("DELETE FROM sleeper_stats WHERE week = 9")
        after = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)
        self.assertEqual(build_report(self.conn, self.cfg, after)["live"]["replacements"], [])

    def test_stats_fetched_before_match_end_dont_count(self):
        self.conn.execute("UPDATE sleeper_stats SET fetched_at = '2026-10-24T12:00:00Z' WHERE week = 9")
        after = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)
        self.assertEqual(build_report(self.conn, self.cfg, after)["live"]["replacements"], [])


class LiveMatchupTests(LiveReportTests):
    AFTER = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        self.conn.execute("UPDATE sleeper_users SET team_name = 'Rivals FC' WHERE user_id = 'u2'")
        self.conn.execute("UPDATE sleeper_rosters SET players_json = ?, starters_json = ? WHERE roster_id = 2",
                          (json.dumps(["s5", "s6"]), json.dumps(["s6", "s5", "0"])))
        self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 9, 's5', ?, '2026-10-24T14:30:00Z')",
                          (json.dumps({"min": 90, "pos_m_g": 1}),))
        self.cfg.schedule = {"start_gw": 9, "end_gw": 31, "opponents": ["Rivals FC"]}

    def test_live_week_shows_set_lineups_and_points(self):
        m = build_report(self.conn, self.cfg, self.AFTER)["matchup"]
        self.assertEqual((m["gw"], m["mode"], m["after_swaps"]), (9, "live", None))
        self.assertEqual([s["player"]["sleeper_id"] for s in m["me"]["starters"]], ["s1", "s2", "s3"])
        them = m["them"]["starters"]
        self.assertEqual([s["player"] and s["player"]["sleeper_id"] for s in them], ["s6", "s5", None])
        self.assertEqual([s["points"] for s in them], [0.0, 9.0, 0.0])
        self.assertEqual(m["them"]["total"], 9.0)
        self.assertEqual(m["me"]["total"], round(sum(s["points"] for s in m["me"]["starters"]), 1))
        self.assertEqual(m["them"]["to_play"], 0)
        self.assertEqual(m["them"]["bench"], [])

    def test_live_week_without_scheduled_opponent_falls_back(self):
        self.cfg.schedule = {"start_gw": 10, "end_gw": 31, "opponents": ["Rivals FC"]}
        r = build_report(self.conn, self.cfg, self.AFTER)
        self.assertTrue(r["matchup"] is None or r["matchup"]["mode"] == "projected")
        if r["matchup"]:
            self.assertEqual(r["matchup"]["gw"], r["gameweek"])

    def test_odd_starter_ids(self):
        self.conn.execute("UPDATE sleeper_rosters SET starters_json = ? WHERE roster_id = 2", (json.dumps(["0", "s99"]),))
        them = build_report(self.conn, self.cfg, self.AFTER)["matchup"]["them"]["starters"]
        self.assertIsNone(them[0]["player"])
        self.assertEqual((them[1]["player"]["name"], them[1]["points"]), ("Sleeper player s99", 0.0))
        self.assertIsNone(them[2]["player"])


class LiveMatchupPageTests(LiveMatchupTests):
    def test_page_live_mode(self):
        page = render_html(build_report(self.conn, self.cfg, self.AFTER))
        self.assertIn("GW9 · live", page)
        self.assertIn("to play", page)
        self.assertIn("<h2>Best XI · GW", page)
        self.assertIn('class="pitch"', page)


class ReviewFixReportMarketTests(MarketReportTests):
    def test_goal_quote_for_other_fixture_not_attached(self):
        self.quotes()
        self.conn.execute("INSERT INTO player_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          ("2026-10-22T19:00:00Z", "kalshi", "ARS", "LFC", "2026-10-17T11:30:00Z", "Bukayo Saka",
                           "ARS", "goal1", 0.59, 0.61, 0.6, 500, "t"))
        r = build_report(self.conn, self.cfg, NOW)
        self.assertIsNone(self.player(r, "Bukayo Saka")["p_goal_market"])


class ReviewFixLiveReportTests(LiveReportTests):
    def test_club_needs_players_with_minutes_in_stats(self):
        # stats fetched after the match, but nobody from ARS has minutes yet (Sleeper lag)
        self.conn.execute("UPDATE sleeper_stats SET stats_json = ? WHERE week = 9", (json.dumps({"min": 0}),))
        after = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)
        self.assertEqual(build_report(self.conn, self.cfg, after)["live"]["replacements"], [])

    def test_preview_build_logs_no_matchday_goal_odds(self):
        from unittest import mock
        self.conn.execute("INSERT INTO fpl_fixtures VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (TS, 51, 9, 2, 1, 3, 4, "2026-10-24T16:30:00Z", 0, None, None))
        after = datetime(2026, 10, 24, 15, tzinfo=timezone.utc)
        always = mock.Mock(get=lambda key, default=None: 0.5)

        def logged():
            return self.conn.execute("SELECT COUNT(*) FROM projection_log WHERE p_goal_market IS NOT NULL").fetchone()[0]

        with mock.patch("sleeperfpl.report._goal_quotes", return_value=always):
            build_report(self.conn, self.cfg, after, log=False)
            self.assertEqual(logged(), 0)
            build_report(self.conn, self.cfg, after)
            self.assertGreater(logged(), 0)

    def test_matchday_builds_even_if_fetches_fail(self):
        from unittest import mock
        import sleeperfpl.__main__ as cli
        built = []
        boom = mock.Mock(side_effect=RuntimeError("down"))
        with mock.patch.object(cli, "fetch_rosters", boom), mock.patch.object(cli, "fetch_fixtures", boom), \
             mock.patch.object(cli, "fetch_kalshi", boom), mock.patch.object(cli, "fetch_polymarket", boom), \
             mock.patch.object(cli, "fetch_sleeper_stats", boom), mock.patch.object(cli, "fetch_bootstrap", boom), \
             mock.patch.object(cli, "matchday_due", return_value=True), \
             mock.patch.object(cli, "cmd_build", lambda *a: built.append(1)):
            cli.cmd_matchday(self.cfg, self.conn, mock.Mock(force=False))
        self.assertEqual(built, [1])


if __name__ == "__main__":
    unittest.main()
