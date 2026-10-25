import unittest

import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from sleeperfpl.__main__ import main
from sleeperfpl.availability import (LabelTable, apply_labels, audit_rows, chance_for, format_audit, fpl_bucket,
                                    fpl_stale, hard_out_weeks, is_hard_out, load_counts, rebuild_label_counts,
                                    slp_bucket)
from sleeperfpl.db import connect


class BucketTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(fpl_bucket("a", None), "a100")
        self.assertEqual(fpl_bucket("a", 100), "a100")
        self.assertEqual(fpl_bucket("d", 75), "d75")
        self.assertEqual(fpl_bucket("i", 0), "i0")
        self.assertEqual(fpl_bucket("i", None), "i0")
        self.assertEqual(fpl_bucket("d", None), "d50")
        self.assertEqual(fpl_bucket("u", 0), "u")
        self.assertEqual(slp_bucket("Out"), "out")
        self.assertEqual(slp_bucket(None), "none")
        self.assertTrue(is_hard_out("i0") and is_hard_out("s") and is_hard_out("d0"))
        self.assertFalse(is_hard_out("d25") or is_hard_out("a100"))

    def test_chance_for_switches_at_deadline(self):
        el = {"chance_next": 0, "chance_this": 100}
        self.assertEqual(chance_for(el, deadline_passed=False), 0)
        self.assertEqual(chance_for(el, deadline_passed=True), 100)


class LabelLayerTests(unittest.TestCase):
    def test_priors_without_counts(self):
        ps_fpl, pa_fpl, ps, pa = apply_labels(0.8, 0.9, "a100", "out", False, LabelTable(), 1.0)
        self.assertEqual((ps_fpl, pa_fpl), (0.8, 0.9))
        self.assertAlmostEqual(ps, 0.64)
        self.assertAlmostEqual(pa, 0.855)

    def test_weight_zero_ignores_sleeper(self):
        _, _, ps, pa = apply_labels(0.8, 0.9, "a100", "out", False, LabelTable(), 0.0)
        self.assertAlmostEqual(ps, 0.8)
        self.assertAlmostEqual(pa, 0.9)

    def test_stale_short_doubt_halfway(self):
        ps_fpl, _, _, _ = apply_labels(1.0, 1.0, "d75", "none", True, LabelTable(), 1.0)
        self.assertAlmostEqual(ps_fpl, 1 - 0.5 * 0.37)

    def test_stale_long_injury_not_revived(self):
        ps_fpl, _, _, _ = apply_labels(1.0, 1.0, "i25", "none", True, LabelTable(), 1.0)
        self.assertAlmostEqual(ps_fpl, 0.06)

    def test_stale_fpl_sleeper_out_cap(self):
        _, _, ps, _ = apply_labels(1.0, 1.0, "a100", "out", True, LabelTable(), 1.0)
        self.assertAlmostEqual(ps, 0.60)

    def test_hard_out_zero(self):
        self.assertEqual(apply_labels(0.9, 0.95, "i0", "none", False, LabelTable(), 1.0), (0.0, 0.0, 0.0, 0.0))

    def test_flagged_out_and_unknown_cells(self):
        t = LabelTable()
        self.assertEqual(t.rr_slp("d75", "out"), (0.90, 0.95))
        self.assertEqual(t.rr_slp("a100", "none"), (1.0, 1.0))
        self.assertEqual(t.rr_fpl("d10"), t.rr_fpl("d25"))   # nearest chance level

    def test_fpl_stale(self):
        self.assertTrue(fpl_stale("2026-10-01T10:00:00Z", "2026-10-18T14:00:00Z"))
        self.assertFalse(fpl_stale("2026-10-20T10:00:00Z", "2026-10-18T14:00:00Z"))
        self.assertFalse(fpl_stale(None, "2026-10-18T14:00:00Z"))
        self.assertFalse(fpl_stale("2026-10-01T10:00:00Z", None))


KO = "2026-10-18T14:00:00Z"


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def log(self, sid, gw=8, made_at="2026-10-18T12:00:00Z", label_at="2026-10-18T13:00:00Z", fb="a100", sb="out",
            p_model=0.8, p_fpl=0.8):
        self.conn.execute(
            "INSERT INTO projection_log (made_at, season, gw, sleeper_id, kickoff, v1, fpl_bucket, slp_bucket, "
            "p_start_model, p_app_model, p_start_fpl, p_app_fpl, p_start, label_at) "
            "VALUES (?, '2026', ?, ?, ?, 1.0, ?, ?, ?, ?, ?, ?, ?, ?)",
            (made_at, gw, sid, KO, fb, sb, p_model, p_model, p_fpl, p_fpl, p_fpl, label_at))

    def stats(self, sid, st, week=8):
        self.conn.execute("INSERT INTO sleeper_stats VALUES ('2026', ?, ?, ?, 'x')", (week, sid, json.dumps(st)))

    def test_rebuild_counts_observed_expected(self):
        self.log("a")
        self.log("b")
        self.stats("a", {"min": 90, "gs": 1})
        self.stats("b", {"min": 0})
        rebuild_label_counts(self.conn, "2026", 8, "now")
        c = load_counts(self.conn, "2026")
        fpl = c[("fpl", "a100")]
        self.assertEqual((fpl["n"], fpl["starts"], fpl["apps"], fpl["sixty"]), (2, 1, 1, 1))
        self.assertAlmostEqual(fpl["e_start"], 1.6)
        self.assertEqual(c[("sleeper", "a100+out")]["n"], 2)

    def test_label_age_limit(self):
        self.log("a", label_at="2026-10-15T09:00:00Z")      # 77 h before kickoff
        self.stats("a", {"min": 90, "gs": 1})
        rebuild_label_counts(self.conn, "2026", 8, "now")
        self.assertEqual(load_counts(self.conn, "2026"), {})

    def test_post_kickoff_rows_ignored_and_last_pre_kickoff_used(self):
        self.log("a", made_at="2026-10-18T10:00:00Z", fb="d75")
        self.log("a", made_at="2026-10-18T13:30:00Z", fb="a100")
        self.log("a", made_at="2026-10-18T15:00:00Z", fb="i0")
        self.stats("a", {"min": 90, "gs": 1})
        rebuild_label_counts(self.conn, "2026", 8, "now")
        self.assertEqual({cell for _, cell in load_counts(self.conn, "2026")}, {"a100", "a100+out"})

    def test_unfetched_week_not_counted(self):
        self.log("a")
        rebuild_label_counts(self.conn, "2026", 8, "now")
        self.assertEqual(load_counts(self.conn, "2026"), {})

    def test_hard_out_rows_not_in_sleeper_cells(self):
        self.log("a", fb="i0", p_fpl=0.0)
        self.log("b", fb="d75", p_fpl=0.5)
        self.stats("a", {"min": 90, "gs": 1})
        self.stats("b", {"min": 0})
        rebuild_label_counts(self.conn, "2026", 8, "now")
        c = load_counts(self.conn, "2026")
        self.assertEqual(c[("fpl", "i0")]["n"], 1)                 # still shown in the audit
        self.assertEqual(c[("sleeper", "flagged+out")]["n"], 1)    # only the d75 player
        self.assertEqual(c[("sleeper", "flagged+out")]["starts"], 0)

    def test_learned_rr(self):
        t = LabelTable({("sleeper", "a100+out"): {"n": 40, "starts": 10, "apps": 10, "sixty": 8, "e_start": 10.0,
                                                  "e_app": 10.0}}, k=20)
        ss, _ = t.rr_slp("a100", "out")
        self.assertAlmostEqual(ss, (10 + 20 * 0.8) / 30)
        self.assertEqual(t.rr_slp("a100", "none"), (1.0, 1.0))     # fixed, never learned

    def test_pooling_under_30(self):
        c = {("fpl", "d75"): {"n": 10, "starts": 2, "apps": 5, "sixty": 2, "e_start": 5.0, "e_app": 6.0},
             ("fpl", "i75"): {"n": 25, "starts": 10, "apps": 15, "sixty": 8, "e_start": 10.0, "e_app": 12.0}}
        rs, _ = LabelTable(c, k=20).rr_fpl("d75")
        self.assertAlmostEqual(rs, (12 + 20 * 0.63) / (15 + 20))

    def test_learned_rr_capped(self):
        c = {("fpl", "d75"): {"n": 100, "starts": 100, "apps": 100, "sixty": 90, "e_start": 10.0, "e_app": 10.0}}
        self.assertEqual(LabelTable(c, k=20).rr_fpl("d75")[0], 1.2)

    def test_hard_out_weeks(self):
        self.log("a", fb="i0")
        self.assertEqual(hard_out_weeks(self.conn, "2026"), {"a": {8}})

    def test_audit_text_leads_with_sleeper_out(self):
        self.log("a")
        self.log("b", fb="d75", sb="none")
        self.stats("a", {"min": 90, "gs": 1})
        self.stats("b", {"min": 20})
        rebuild_label_counts(self.conn, "2026", 8, "now")
        rows = audit_rows(self.conn, "2026")
        self.assertEqual((rows[0]["kind"], rows[0]["cell"]), ("sleeper", "a100+out"))
        text = format_audit(rows)
        self.assertTrue(text.startswith("Sleeper says Out, FPL says available"))
        self.assertIn("d75", text)

    def test_label_audit_empty_message(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            cfg.write_text(json.dumps({"league_id": "L1", "owner": "me", "db_path": "t.db"}))
            conn = connect(Path(d) / "t.db")
            conn.execute("INSERT INTO sleeper_league VALUES ('t', 'L1', ?)", (json.dumps({"season": "2026"}),))
            conn.commit()
            conn.close()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["--config", str(cfg), "label-audit"]), 0)
            self.assertIn("no label history yet", out.getvalue())


if __name__ == "__main__":
    unittest.main()
