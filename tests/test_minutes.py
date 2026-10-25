import json
import unittest

from sleeperfpl.db import connect
from sleeperfpl.minutes import (FEATURES, MinutesModel, Week, ewma, features, fixture_minutes, history,
                                last_season_start_share, personal_start_buckets, player_minutes, regular_weeks,
                                season_rows)


def model(**kw):
    base = dict(version="t", weights=[0.0] * (len(FEATURES) + 1), mu=[0.0] * len(FEATURES), sd=[1.0] * len(FEATURES),
                q_prior={"GK": 0.023, "DEF": 0.2, "MID": 0.4, "FWD": 0.5},
                start_buckets={p: [[90, 0.6], [82, 0.2], [67, 0.1], [45, 0.1]] for p in ("GK", "DEF", "MID", "FWD")},
                sub_buckets=[[5, 0.3], [15, 0.3], [25, 0.2], [40, 0.19], [65, 0.01]],
                short_prior={"GK": 0.3, "DEF": 0.2, "MID": 0.2, "FWD": 0.2}, trained_on=["2025"], n_rows=0)
    base.update(kw)
    return MinutesModel(**base)


def teams(gp=1):
    return {f"TEAM_{t}": {"gp": gp} for t in range(1, 21)}


class MinutesCoreTests(unittest.TestCase):
    def test_ewma_known(self):
        lam = 0.5 ** (1 / 3)
        self.assertAlmostEqual(ewma([0, 0, 1], 3), 1 / (1 + lam + lam * lam))
        self.assertEqual(ewma([], 3), 0.0)

    def test_history_missing_row_is_unused_and_before_first_row_ignored(self):
        rows = {w: dict(teams()) for w in range(1, 5)}
        rows[2]["p"] = {"min": 90, "gs": 1}
        rows[4]["p"] = {"min": 20}
        hist = history(rows, "p", 5, regular_weeks(rows))
        self.assertEqual([h.week for h in hist], [2, 3, 4])
        self.assertEqual([(h.minutes, h.started) for h in hist], [(90, True), (0, False), (20, False)])
        self.assertEqual([h.week for h in history(rows, "p", 5, regular_weeks(rows), excluded={3})], [2, 4])

    def test_irregular_week_excluded_and_no_team_rows_regular(self):
        rows = {1: teams(), 2: {**teams(), "TEAM_3": {"gp": 2}}, 3: {"p": {"min": 90}}}
        self.assertEqual(regular_weeks(rows), {1, 3})

    def test_features_values(self):
        hist = [Week(1, 90, True), Week(2, 20, False), Week(3, 0, False), Week(4, 75, True)]
        x = dict(zip(FEATURES, features(hist, "MID")))
        self.assertEqual(x["prev_st"], 1.0)
        self.assertAlmostEqual(x["prev_m90"], 75 / 90)
        self.assertEqual(x["zero_streak"], 0.0)
        self.assertAlmostEqual(x["last3"], 1 / 3)
        self.assertEqual(x["prev_sub"], 0.0)
        self.assertEqual((x["GK"], x["DEF"], x["FWD"]), (0.0, 0.0, 0.0))
        streak = dict(zip(FEATURES, features(hist[:3], "DEF")))
        self.assertAlmostEqual(streak["zero_streak"], 1 / 5)
        self.assertEqual(streak["DEF"], 1.0)

    def test_mixture_sums_to_one_and_bounds(self):
        m = model()
        fm = fixture_minutes(0.6, 0.8, m.start_buckets["MID"], m.sub_buckets)
        self.assertAlmostEqual(sum(p for _, p in fm.scenarios), 1.0)
        self.assertLessEqual(fm.p60, fm.p1)
        self.assertLessEqual(fm.e_min, 90)
        self.assertAlmostEqual(fm.p1, 0.8)
        clamped = fixture_minutes(0.7, 0.5, m.start_buckets["MID"], m.sub_buckets)
        self.assertAlmostEqual(clamped.p1, 0.7)

    def test_gk_rarely_sub(self):
        m = model()
        hist = [Week(w, 90, True) for w in range(1, 6)] + [Week(6, 0, False)]
        pm = player_minutes(hist, "GK", m, None)
        self.assertLess(pm.p_app - pm.p_start, 0.05 * (1 - pm.p_start) + 1e-9)

    def test_short_history_uses_last_season(self):
        m = model()
        pm = player_minutes([], "MID", m, 0.8)
        self.assertTrue(pm.short)
        self.assertAlmostEqual(pm.p_start, 0.8)
        self.assertAlmostEqual(player_minutes([], "MID", m, None).p_start, 0.2)
        two = [Week(1, 90, True), Week(2, 0, False)]
        self.assertAlmostEqual(player_minutes(two, "MID", m, None).p_start, (1 + 3 * 0.2) / 5)

    def test_personal_start_buckets_shrink(self):
        m = model()
        hooked = [Week(w, 65, True) for w in range(1, 11)]
        b = personal_start_buckets(hooked, "MID", m)
        self.assertAlmostEqual(sum(p for _, p in b), 1.0)
        self.assertAlmostEqual(b[0][1], 10 * 0.6 / 20)     # no 90s: halfway to the position's 0.6
        self.assertGreater(b[1][1] + b[2][1], 0.3)

    def test_last_season_share(self):
        rows = {1: {**teams(), "p": {"min": 90, "gs": 1}}, 2: {**teams(), "p": {"min": 0}}, 3: teams()}
        self.assertAlmostEqual(last_season_start_share(rows, "p"), 0.5)
        self.assertIsNone(last_season_start_share(rows, "q"))

    def test_model_roundtrip(self):
        m = model(weights=[0.1] * (len(FEATURES) + 1))
        self.assertEqual(MinutesModel.from_dict(json.loads(json.dumps(m.to_dict()))), m)
        self.assertAlmostEqual(model().p_start([0.0] * len(FEATURES)), 0.5)

    def test_season_rows(self):
        conn = connect(":memory:")
        conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 1, 'p', ?, 'x')", (json.dumps({"min": 90}),))
        conn.execute("INSERT INTO sleeper_stats VALUES ('2026', 2, 'p', ?, 'x')", (json.dumps({"min": 10}),))
        self.assertEqual(season_rows(conn, "2026", max_week=1), {1: {"p": {"min": 90}}})


if __name__ == "__main__":
    unittest.main()
