# Report Card and Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A report card that scores zeros, outcome categories, player ordering, the tool's lineup decisions and match odds, plus a `backtest` command that replays past weeks, so the next projection changes can be judged.

**Architecture:** Pure scoring functions live in a new `metrics.py`, shared by the live report card (`report_card.py`) and the new `backtest.py`. Builds log more (position and `ep_next` per player, the recommended XI and swaps, the fitted match odds) into append-only tables. `db.connect` migrates existing databases in place. Last season's Sleeper stats are fetched once with `fetch-history --season 2025` so the backtest has ~38 more weeks.

**Tech Stack:** Python 3.10+ standard library only (`sqlite3`, `argparse`, `json`, `math`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-24-report-card-backtest-design.md`

## Global Constraints

- Python 3.10+, standard library only, same package and style.
- No new dependencies, API keys or paid data.
- No changes to how projections are made (`projection.py`, `projection_v2.py`, `pricing.py` fitting stay as they are).
- Tests use `unittest` and never touch the network. Run everything with `python3 -m unittest`.
- Sleeper's week number is still treated as the FPL game week.
- New columns on existing tables need an in-place migration for the server's existing database.
- No pruning of snapshot tables.
- Outcome category defaults: low < 3, mid 3–10, haul ≥ 10 league points (config keys `category_low`, `category_haul`).
- Rank correlation: Spearman, per game week and per position (GK, DEF, MID, FWD); skip a position with fewer than 5 players that week.
- `ep_next` is graded only on rank correlation, never MAE.
- The week's recommendation is the last one logged before the game week's first kickoff. The owner's set lineup is the `starters` of the newest `sleeper_rosters` snapshot before that kickoff.
- Backtest: seasons are replayed separately and never pooled. History never crosses a season boundary. Week 1 of each season isn't scored. The sample is every player with a played week in the 5 weeks before *g*. The reference is `v1`.
- Every new number on the page gets a plain-words explanation under its table.
- Before Task 1, the owner's uncommitted `README.md` rewrite and the untracked `docs/README.md` are committed on their own. Otherwise the `git commit -am` steps would sweep them into feature commits, and docs edits to the untracked file would never be committed.

**Deviation from the spec, decided while planning:** the spec says a 2025 club's blank or double week is read from "the most `gp` any of its players recorded". But Sleeper's 2025 rows don't say which club a player was at; the player map only has current clubs. Sleeper's weekly stats do include one `TEAM_<id>` row per club with its own `gp`. So for 2025 every player gets one neutral fixture. Any week where a `TEAM_` row has `gp` other than 1, or fewer than 20 `TEAM_` rows have `gp` ≥ 1, is excluded from the replay and listed in the output as irregular. Nothing from an individual player's week-*g* stats is used.

## Review Focus

1. **The server's existing database** has the old column sets. After `connect`, a build and every insert must work, and old `projection_log` rows still score. (Task 2 test.)
2. **A logged player with no stats row** for a finished week scores 0 points in the "didn't play" category. He isn't skipped. (Task 4 test.)
3. **Ties and tiny samples in rank correlation:** all-equal projections (for example 0 for everyone) or fewer than 5 players give `None`, never a crash or NaN. (Task 1 test.)
4. **A week with no recommendation logged before kickoff**, or no roster snapshot before kickoff (missed cron), is left out of the decision metrics without an error. (Task 5 test.)
5. **A fixture marked finished but without scores** (postponed or abandoned), and probabilities of exactly 0 or 1, are skipped or clamped, never `math domain error`. (Task 1 and Task 6 tests.)

---

### Task 1: Shared scoring metrics

**Files:**
- Create: `sleeperfpl/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `mean(xs) -> float | None`: `None` for an empty list.
  - `position_group(positions: list[str]) -> str | None`: the first entry mapped through `{"GK": "GK", "D": "DEF", "DEF": "DEF", "M": "MID", "MID": "MID", "F": "FWD", "FWD": "FWD"}`; `None` if no entry is known.
  - `category(stats: dict | None, points: float, low: float, haul: float) -> str`: `"dnp"` if `stats` is None or `stats.get("min") or 0` ≤ 0; otherwise `"low"` if points < low, `"haul"` if points ≥ haul, else `"mid"`.
  - `@dataclass Scored(position: str | None, proj: float, actual: float, cat: str)`
  - `CATEGORIES = ("dnp", "low", "mid", "haul")`
  - `error_summary(rows: list[Scored]) -> dict` with keys `n`, `mae`, `n_played`, `mae_played` (rows whose cat isn't `"dnp"`), and `by_cat`: `{cat: {"n": int, "mae": float | None}}` for every cat in `CATEGORIES`.
  - `spearman(xs: list[float], ys: list[float]) -> float | None`: average ranks for ties, Pearson on the ranks; `None` when n < 2 or either side has zero variance.
  - `rank_by_position(rows: list[Scored], min_players: int = 5) -> float | None`: mean of per-position `spearman(proj, actual)` over positions with ≥ `min_players` rows and a non-None result; `None` if none qualify. Rows with `position` None are ignored.
  - `log_loss(p_home, p_draw, p_away, outcome: str) -> float`: `outcome` in `"home"`/`"draw"`/`"away"`; `-ln(max(p, 1e-6))` of the outcome's probability.
  - `rps(p_home, p_draw, p_away, outcome: str) -> float`: ordered home < draw < away, `((P1−O1)² + (P1+P2−O1−O2)²) / 2`.
  - `brier(p: float, happened: bool) -> float`

- [ ] **Step 1: Write the failing tests** in `tests/test_metrics.py`:

```python
def test_category(self):
    self.assertEqual(category(None, 0, 3, 10), "dnp")
    self.assertEqual(category({"min": 0}, 0, 3, 10), "dnp")
    self.assertEqual(category({"min": 90}, -1, 3, 10), "low")
    self.assertEqual(category({"min": 90}, 2.9, 3, 10), "low")
    self.assertEqual(category({"min": 90}, 3, 3, 10), "mid")
    self.assertEqual(category({"min": 90}, 10, 3, 10), "haul")

def test_position_group(self):
    self.assertEqual(position_group(["D", "M"]), "DEF")
    self.assertEqual(position_group(["FWD"]), "FWD")
    self.assertIsNone(position_group([]))

def test_error_summary(self):
    s = error_summary([Scored("MID", 4, 0, "dnp"), Scored("MID", 4, 5, "mid"), Scored("FWD", 2, 12, "haul")])
    self.assertEqual((s["n"], s["n_played"]), (3, 2))
    self.assertAlmostEqual(s["mae"], 5.0)
    self.assertAlmostEqual(s["mae_played"], 5.5)
    self.assertEqual(s["by_cat"]["dnp"], {"n": 1, "mae": 4.0})
    self.assertEqual(s["by_cat"]["low"], {"n": 0, "mae": None})

def test_spearman(self):
    self.assertAlmostEqual(spearman([1, 2, 3], [1, 2, 3]), 1.0)
    self.assertAlmostEqual(spearman([1, 2, 3], [3, 2, 1]), -1.0)
    self.assertAlmostEqual(spearman([1, 1, 2], [1, 2, 3]), 0.8660, places=4)
    self.assertIsNone(spearman([0, 0, 0], [1, 2, 3]))     # all-equal projections
    self.assertIsNone(spearman([1], [1]))

def test_rank_by_position(self):
    mids = [Scored("MID", i, i, "mid") for i in range(5)]
    fwds = [Scored("FWD", i, -i, "mid") for i in range(5)]
    gks = [Scored("GK", i, i, "mid") for i in range(4)]       # too few: ignored
    self.assertAlmostEqual(rank_by_position(mids + fwds + gks), 0.0)
    self.assertIsNone(rank_by_position(gks))

def test_match_scores(self):
    self.assertAlmostEqual(log_loss(0.5, 0.3, 0.2, "draw"), 1.2040, places=4)
    self.assertAlmostEqual(log_loss(0.0, 0.0, 1.0, "home"), 13.8155, places=4)   # clamped, no domain error
    self.assertAlmostEqual(rps(0.5, 0.3, 0.2, "home"), 0.145)
    self.assertAlmostEqual(brier(0.3, True), 0.49)
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_metrics -v`. Expected: FAIL (`ModuleNotFoundError: sleeperfpl.metrics`).
- [ ] **Step 3: Implement** the functions above in `sleeperfpl/metrics.py`, with a module docstring in the style of the other modules.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_metrics -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add sleeperfpl/metrics.py tests/test_metrics.py && git commit -m "feat: shared scoring metrics"`

---

### Task 2: Schema, migration and new stored fields

**Files:**
- Modify: `sleeperfpl/db.py` (column strings, `OTHER_SCHEMA`, `TABLES`, `connect`)
- Modify: `sleeperfpl/fetch_fpl.py` (`fetch_bootstrap`, `fetch_fixtures` inserts)
- Modify: `sleeperfpl/pricing.py` (`FixtureOdds`, `fit_fixture`)
- Modify: `tests/test_report.py` (`seed` positional inserts gain the new trailing values)
- Test: `tests/test_db.py`, `tests/test_fetch_fpl.py`, `tests/test_pricing.py`

**Interfaces:**
- Produces:
  - `fpl_elements` gains trailing column `ep_next` (REAL or NULL; FPL sends a string such as `"4.5"`).
  - `fpl_fixtures` gains trailing `team_h_score, team_a_score` (NULL until played).
  - `projection_log` gains trailing `position TEXT, ep_next REAL`.
  - New table `match_log (made_at TEXT, season TEXT, gw INTEGER, fixture_id INTEGER, kickoff TEXT, method TEXT, lam_home REAL, lam_away REAL, p_home REAL, p_draw REAL, p_away REAL, cs_home REAL, cs_away REAL, n_quotes INTEGER, avg_spread REAL)`, append-only.
  - New table `recommendation_log (made_at TEXT, season TEXT, gw INTEGER, first_kickoff TEXT, roster_id INTEGER, xi_json TEXT, swaps_json TEXT)`, append-only. `xi_json` is a list of starter sleeper ids; `swaps_json` a list of `[drop_id, add_id]`.
  - Both new tables are added to `TABLES`.
  - `FixtureOdds.avg_spread: float | None = None`: mean of `ask − bid` over the fit's quotes that have both; `None` if none do.
  - `connect` adds any missing column to an existing table with `ALTER TABLE ... ADD COLUMN`, driven by the same column strings that create the tables, so the list lives in one place. New columns go at the end, so positional `INSERT ... VALUES` stays valid on migrated and fresh databases alike.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_db.py::test_old_database_migrated`: in a temp file, create `fpl_elements` with only the old 10 columns and `projection_log` with the old 8, and insert one old `projection_log` row. Call `connect(path)` twice. Then assert:
    - `ep_next` is in `PRAGMA table_info(fpl_elements)`, and `position` and `ep_next` are in `projection_log`'s;
    - an 11-value positional insert into `fpl_elements` succeeds;
    - the old row reads back with `position` NULL.
  - `tests/test_fetch_fpl.py::test_bootstrap_stores_ep_next`: elements with `"ep_next": "4.5"` and one without; the stored values are `4.5` and `None`.
  - `tests/test_fetch_fpl.py::test_fixtures_store_scores`: a fixture with `team_h_score: 2, team_a_score: 0` and one with `None`s stores `(2, 0)` and `(None, None)`.
  - `tests/test_pricing.py::test_avg_spread`: `fit_fixture` on quotes with spreads 0.02 and 0.04 gives `avg_spread ≈ 0.03`. Reuse an existing identifiable quote set from that file.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_db tests.test_fetch_fpl tests.test_pricing -v`. Expected: the four new tests FAIL.
- [ ] **Step 3: Implement** the schema changes, the migration in `connect`, the two fetch inserts (`ep_next` as `float(...)` or `None`), and `avg_spread`. Update the positional inserts in `tests/test_report.py::seed` (fpl_elements +1 value, fpl_fixtures +2 values).
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Docs.** In `docs/README.md` → Database: add the new columns to the snapshot table rows, and `match_log` and `recommendation_log` to "Other tables".
- [ ] **Step 6: Commit** `git commit -am "feat: store ep_next, fixture scores, match and recommendation logs"` (also `git add` the new test file if one was created).

---

### Task 3: Builds log positions, ep_next, recommendations and match odds

**Files:**
- Modify: `sleeperfpl/models.py` (`PlayerCtx.ep_next: float | None = None`)
- Modify: `sleeperfpl/report_card.py` (`log_projections`, `log_goal_odds`, new `log_recommendation`, `log_matches`)
- Modify: `sleeperfpl/report.py` (`build_report`: set `pl.ep_next` from the element row; call the new loggers)
- Test: `tests/test_report_card.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: Task 1 `position_group`; Task 2 tables and `FixtureOdds.avg_spread`.
- Produces:
  - `log_projections(conn, season, gw, players, made_at)`: same signature. Also writes `position_group(pl.positions)` and `pl.ep_next`.
  - `log_goal_odds(...)`: unchanged signature, NULLs for the new columns.
  - `log_recommendation(conn, season, gw, first_kickoff: str | None, roster_id, lineup, swaps, made_at) -> None`. `lineup` is a `lineup.Lineup` (its non-None starters' `sleeper_id`s go in `xi_json`); `swaps` is a list of `lineup.Swap`.
  - `log_matches(conn, season, gw, fodds: dict, fixtures: list[dict], made_at, method: str = "market") -> int`: one row per target-week fixture (`event == gw`) present in `fodds`, with `kickoff` from the fixture's `kickoff_time`. Returns the row count.
  - In `build_report`, all three loggers run under the existing `if use_sleeper and gw.gw:` guard. `first_kickoff` is `gw.first_kickoff` formatted `%Y-%m-%dT%H:%M:%SZ`, or None. `roster_id` is `mine["roster_id"]`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/test_report_card.py::test_log_projections_position_and_ep_next`: a player with positions `["M"]` and `ep_next=3.5` → the row has `position "MID"` and `ep_next 3.5`.
  - `tests/test_report_card.py::test_log_recommendation`: a `Lineup` with starters `[("M", pl("a", 4)), ("M", None)]` and one `Swap(pl("b", 1), pl("c", 5), 4.0, "xi")` → `xi_json == ["a"]` and `swaps_json == [["b", "c"]]`.
  - `tests/test_report_card.py::test_log_matches_target_week_only`: two fixtures, events 9 and 10, both in `fodds`, `gw=9` → 1 row, `method "market"`, `avg_spread` copied.
  - Extend `tests/test_report.py::MarketReportTests::test_build_logs_projections`: after the build, `recommendation_log` has 1 row and `match_log` has 1 row for fixture 50.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report_card tests.test_report -v`. Expected: the new assertions FAIL.
- [ ] **Step 3: Implement** the loggers and the `build_report` wiring.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: log positions, ep_next, recommendations and match odds"`

---

### Task 4: Report card scores zeros, categories and ordering

**Files:**
- Modify: `sleeperfpl/config.py` (`DEFAULTS`, `Config`, `load_config`)
- Modify: `config.example.json`
- Modify: `sleeperfpl/report_card.py` (`score_weeks`, `summary`)
- Modify: `sleeperfpl/report.py` (the `score_weeks` call passes the cutoffs)
- Test: `tests/test_report_card.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Task 1 `category`, `Scored`, `error_summary`, `rank_by_position`, `spearman`, `mean`; Task 3 log columns.
- Produces:
  - `Config.category_low: float = 3`, `Config.category_haul: float = 10`. `load_config` raises `ConfigError` unless low < haul.
  - `score_weeks(conn, season, scoring, last_finished, low=3, haul=10) -> list[dict]`. One dict per finished week, using each player's last row with `v1` not NULL and `made_at <= kickoff` (as today). Every logged player is scored. A missing stats row means `stats=None`, 0 points, category `"dnp"`. Keys:
    - `gw`;
    - from `error_summary` over v1: `n`, `mae_v1`, `n_played`, `mae_v1_played`, `by_cat_v1`;
    - `rank_v1`;
    - over players with v2: `n_v2`, `mae_v2`, `mae_v1_paired`, `rank_v2`, `rank_v1_paired`;
    - over players with `ep_next` (rank only): `n_ep`, `rank_ep`, `rank_v1_ep`;
    - `brier_goal`, `n_goal` as today (played players only, unchanged).
  - Old log rows with `position` NULL count in the errors but not in the ranks.
  - `summary(weeks)`: unchanged text and rule. Task 5 extends it.

- [ ] **Step 1: Write the failing tests.**
  - Replace `test_zero_minute_players_excluded` with `test_zero_minute_and_missing_players_scored`:
    - log `a` (v1 4, 0 minutes), `b` (v1 4, no stats row) and `c` (v1 4, scores 5);
    - assert `n == 3`, `n_played == 1`, `mae_v1 == (4 + 4 + 1) / 3`, `mae_v1_played == 1.0`, `by_cat_v1["dnp"]["n"] == 2`.
  - `test_categories_use_cutoffs`: one player at 12 actual → `by_cat_v1["haul"]["n"] == 1` with default cutoffs, and `["mid"]["n"] == 1` with `haul=15`.
  - `test_rank_v1`: five MID players, v1 1..5, actual points 1..5 → `rank_v1 ≈ 1.0`. With four players → `rank_v1 is None`.
  - `test_rank_ep_paired`: five MID players with `ep_next` reversed against actual, v1 matching actual → `rank_ep ≈ -1.0`, `rank_v1_ep ≈ 1.0`, `n_ep == 5`.
  - `test_old_rows_without_position_still_score`: insert an 8-value-era row through SQL with `position` NULL → it counts in `n` and `rank_v1` is None.
  - Update the existing tests whose numbers change now that zeros count. `test_log_and_score` and `PairedComparisonTests` keep their values, since all their players played.
  - `tests/test_config.py::test_category_cutoffs`: defaults 3 and 10; `{"category_low": 10, "category_haul": 5}` raises `ConfigError`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report_card tests.test_config -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** In `report.py`, call `score_weeks(conn, sleeper_season, scoring, last_finished, cfg.category_low, cfg.category_haul)`. Add both keys to `config.example.json`.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Docs.** In `docs/README.md` → Configuration, add rows for `category_low` (3) and `category_haul` (10): "league-point cutoffs for the report card's low / mid / haul groups". In "How projections work", replace the last paragraph's "compares each player's last projection made before his kickoff with his real league points" with a sentence saying players who didn't play now count as 0.
- [ ] **Step 6: Commit** `git commit -am "feat: report card scores zeros, outcome groups and player order"`

---

### Task 5: Decision metrics: your lineup vs the tool vs hindsight

**Files:**
- Modify: `sleeperfpl/report_card.py` (new `score_decisions`; `summary` gains decisions)
- Modify: `sleeperfpl/report.py` (call it; put it in `report_card`)
- Test: `tests/test_report_card.py`

**Interfaces:**
- Consumes: Task 3 `recommendation_log`; `lineup.best_xi(players, slots, eligibility)`; `models.PlayerCtx`; `sleeper_stats.sleeper_points`.
- Produces:
  - `score_decisions(conn, season, scoring, last_finished, slots, eligibility) -> list[dict]`, one per finished week that has both a recommendation with `made_at < first_kickoff` (the latest such row) and a `sleeper_rosters` snapshot with `fetched_at < first_kickoff` containing that `roster_id`. Weeks missing either are skipped silently. Keys:
    - `gw`;
    - `set_pts`: actual points of the snapshot's `starters`, ignoring ids not on that roster, such as Sleeper's `"0"` empty slot;
    - `tool_pts`: actual points of `xi_json`;
    - `best_pts`: `best_xi` total over the snapshot roster minus its `reserve`. Each player is a `PlayerCtx` with `proj` = actual points, `positions` from the newest `sleeper_players` row, `fpl_id=0`;
    - `swaps`: `[{"drop", "add", "drop_pts", "add_pts", "diff"}]`, with names from the newest `sleeper_players` rows (falling back to the id) and `diff = add_pts − drop_pts`.
  - Actual points are `sleeper_points(stats, scoring)`, with 0 when the week has no row.
  - `summary(weeks, decisions=None)`: with ≥ 2 decision weeks, returns `f"the tool's XI beat your lineup in {wins} of {n} weeks"` (win = `tool_pts > set_pts`). Otherwise today's v2-vs-v1 text.
  - In `build_report`: `decisions = score_decisions(conn, sleeper_season, scoring, last_finished, slots, cfg.slot_eligibility) if use_sleeper else []`. `report["report_card"]` gains `"decisions": decisions[-8:]`, with floats rounded to 2 places like `weeks`, and `summary(card, decisions)`.

- [ ] **Step 1: Write the failing tests** (seed `sleeper_players`, one `sleeper_rosters` snapshot, a `recommendation_log` row and week-9 stats directly through SQL). Slots `["M", "M"]`, eligibility `{"M": ["M"]}`, players `a`, `b`, `c` all `["M"]` scoring 2, 5 and 8:
  - `test_decisions_compare_set_tool_best`:
    - roster starters `["a", "b"]`, tool XI `["b", "c"]`, swap `[["a", "d"]]`, and `d` scored 3;
    - assert `set_pts == 7`, `tool_pts == 13`, `best_pts == 13`, swap `diff == 1`.
  - `test_recommendation_after_kickoff_ignored`: the only recommendation is logged after `first_kickoff` → `[]`.
  - `test_no_roster_snapshot_before_kickoff`: the roster snapshot is fetched after kickoff → `[]` without error.
  - `test_empty_slot_id_ignored`: starters `["a", "0"]` → `set_pts == 2`.
  - `test_summary_prefers_decisions`: two decision weeks with tool > set and tool < set → `"the tool's XI beat your lineup in 1 of 2 weeks"`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report_card -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `score_decisions`, the `summary` change and the `build_report` wiring.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: grade the tool's lineup and swaps against your own"`

---

### Task 6: Match report card

**Files:**
- Modify: `sleeperfpl/report_card.py` (new `score_matches`)
- Modify: `sleeperfpl/report.py` (call it; `report_card["matches"]`)
- Test: `tests/test_report_card.py`

**Interfaces:**
- Consumes: Task 1 `log_loss`, `rps`, `brier`, `mean`; Task 2 `match_log` and fixture scores.
- Produces:
  - `score_matches(conn, season, last_finished) -> list[dict]`, one per week ≤ `last_finished` with at least one scored fixture. Keys: `gw`, `n`, `log_loss`, `rps`, `brier_cs` (the mean over both sides of `brier(cs_side, side conceded 0)`).
  - It uses the newest `fpl_fixtures` snapshot. A fixture counts only if `finished` and both scores are not NULL, and it has a `match_log` row for `season` with `made_at <= kickoff`. The latest such row is used.
  - `report["report_card"]["matches"] = score_matches(...)[-8:]` (rounded), under `use_sleeper`.

- [ ] **Step 1: Write the failing tests:**
  - `test_score_matches`: one logged fixture, `p 0.5/0.3/0.2`, `cs_home 0.4`, `cs_away 0.3`, final score 1–0 → `log_loss ≈ 0.6931`, `rps ≈ 0.145`, `brier_cs ≈ ((0.4−1)² + (0.3−0)²)/2 = 0.225`, `n == 1`.
  - `test_finished_without_scores_skipped`: `finished=1` with NULL scores → `[]`.
  - `test_log_after_kickoff_ignored`: the only log row is after kickoff → `[]`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report_card -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: score match odds against results"`

---

### Task 7: Report card on the page and in report.json

**Files:**
- Modify: `sleeperfpl/report.py` (`_report_card`)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `report["report_card"]` keys `weeks`, `decisions`, `matches` and `summary` from Tasks 4–6.
- Produces: HTML only. It's built with the existing helpers `_e`, `_num` and `_table`/`.scroll`, and stays readable at phone width.
  - **Weeks table:** Week | Players | Avg miss (all) | Avg miss (played) | Avg miss v2 | Order (v1). The Order cell shows `rank_v1` to 2 decimals, or "–". When a week has a decision row, three more cells: Your XI | Tool XI | Best XI.
  - **Matches table**, only when `matches` is non-empty: Week | Matches | Log loss | RPS | Clean-sheet score.
  - The outcome groups and swap outcomes stay in `report.json` only *(builder's call allowed by the spec; keeps the phone view short)*.
  - Explanation text under the tables, verbatim:
    - "Avg miss = average points the projection was off by (lower is better); "all" counts players who didn't play as 0 points."
    - "Order = how well the projection ranked players within each position, from −1 to 1 (higher is better)."
    - "Your XI / Tool XI / Best XI = real points of the lineup you set, the lineup this page suggested, and the best lineup possible from your roster."
    - "Log loss, RPS and clean-sheet score grade the match odds against results (lower is better)."
    - "Goal-odds score = how well market goal odds predicted scorers (Brier; lower is better)."

- [ ] **Step 1: Write the failing test** `tests/test_report.py::test_report_card_html`: call `render_html` on a minimal report dict, reusing the shape of an existing render test. Give it one week (`rank_v1` 0.42), one decision row (`set_pts` 7, `tool_pts` 13, `best_pts` 15) and one match row. Assert the HTML contains `"Order"`, `"0.42"`, `"Your XI"`, `">13<"` and `"Log loss"`. With `matches == []`, `"Log loss"` is absent.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `_report_card`.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Docs.** In `docs/README.md` → "What the page shows", expand the Report card bullet to mention zeros, order, your lineup vs the tool, and match odds. In Outputs, describe `report_card` with `weeks`, `decisions`, `matches` and `summary`.
- [ ] **Step 6: Commit** `git commit -am "feat: show the new report card on the page"`

---

### Task 8: Backfill last season's Sleeper stats

**Files:**
- Modify: `sleeperfpl/sleeper_stats.py` (`fetch_sleeper_stats` gains `update_meta`)
- Modify: `sleeperfpl/__main__.py` (`fetch-history --season`)
- Test: `tests/test_sleeper_stats.py`

**Interfaces:**
- Produces:
  - `fetch_sleeper_stats(conn, sport, season, last_week, *, force=False, get=get_json, update_meta=True)`. With `update_meta=False`, the `sleeper_stats_week` meta key isn't touched, so a backfill can't change the live "stats week" shown on the page.
  - `fetch-history --season SEASON`: fetches weeks 1–38 for that season with `update_meta=False` and skips the FPL crawl, printing `sleeper stats <season>: <n> week(s) fetched`. If `SEASON` equals the league's current season, it exits with `error: --season is for past seasons; run fetch-history without it` (a `BuildError`). Without `--season`, behaviour is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `tests/test_sleeper_stats.py::test_backfill_leaves_meta`: set meta `sleeper_stats_week` to `"8"`, call `fetch_sleeper_stats(conn, "s", "2025", 2, update_meta=False, get=fake)` → 2 weeks stored for `"2025"`, and meta is still `"8"`.
  - `test_cli_season_skips_fpl`: patch `fetch_history` and `fetch_sleeper_stats` in `sleeperfpl.__main__`, the way `test_matchday_builds_even_if_fetches_fail` does. Run `main(["--config", cfg, "fetch-history", "--season", "2025"])` → `fetch_history` isn't called, and `fetch_sleeper_stats` is called with `"2025"`, `38` and `update_meta=False`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_sleeper_stats -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Docs.** In `docs/README.md` → Commands, add `fetch-history --season <year>` to the `fetch-history` row. Add a Setup line: `python3 -m sleeperfpl fetch-history --season 2025   # once: last season, for backtest`.
- [ ] **Step 6: Commit** `git commit -am "feat: backfill a past season's Sleeper stats"`

---

### Task 9: Backtest command

**Files:**
- Create: `sleeperfpl/backtest.py`
- Modify: `sleeperfpl/__main__.py` (`backtest` command)
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: Task 1 metrics; `projection.project`; `sleeper_stats.sleeper_points`; `matching.match_players`, `load_overrides`, `FPL_POSITIONS`; `gameweek.team_fixtures`; `db.latest`.
- Produces:
  - `NEUTRAL = 0`: a difficulty meaning "no fixture data". `v1` maps it to multiplier 1.0.
  - `@dataclass BacktestInput(sleeper_id: str, position: str, weeks: list[tuple[int, dict]], history: list[float], difficulties: list[int], scoring: dict)`:
    - `weeks`: every prior week of this season with a stats row, most recent first, 0-minute rows included (the minutes project will need them);
    - `history`: league points of prior played weeks (min > 0), most recent first;
    - `difficulties`: one entry per week-*g* fixture, from the player's side.
  - `METHODS: dict[str, Callable[[BacktestInput, Config], float]]`, initially:
    - `"v1"`: calls `projection.project(inp.history, inp.difficulties, False, {**cfg.difficulty_multipliers, NEUTRAL: 1.0}, cfg.avg_window).proj`;
    - `"season_avg"`: `mean(history) × len(difficulties)`, or 0 with no history.
    - A new method is one entry in this dict.
  - `run_backtest(conn, cfg, seasons: list[str] | None = None) -> dict`. `{"seasons": {season: {"weeks": [...], "summary": {...}, "irregular_weeks": [...], "notes": [...]}}, "cannot_replay": [...]}`. Details:
    - Default seasons: every distinct season in `sleeper_stats`, ascending.
    - Scoring is `flatten_scoring_settings` of the newest league's `scoring_settings`.
    - Weeks are 2 to the season's last stored week. In the current season this is capped at the last finished FPL event, so a week in progress is never scored.
    - Sample for week *g*: Sleeper ids not starting with `TEAM_` that have a known `position_group` from the newest `sleeper_players`, and a row with min > 0 in weeks *g*−5 … *g*−1.
    - Actual points are `sleeper_points` of week *g*'s row (0 if none). Category comes from `metrics.category` with the config cutoffs.
    - Fixtures in the current season: `team_fixtures` from the newest `fpl_fixtures`, via the `match_players` mapping to the FPL club; unmatched players get `[NEUTRAL]`. Notes record that "fixture difficulty comes from the newest snapshot; clubs are players' current clubs".
    - Other seasons: `[NEUTRAL]` for everyone. Weeks failing the `TEAM_` check from the Global Constraints deviation are skipped and listed in `irregular_weeks`. Notes record "no fixture data: v1 runs with a neutral multiplier".
    - Each week entry: `{"gw", "n", "methods": {name: error_summary + {"rank": rank_by_position}}, "logged": {...same, restricted to sleeper ids in projection_log for (season, gw); omitted if none}}`.
    - `summary[name]` for each name other than `"v1"`: `{"weeks_beat_v1_rank", "weeks_beat_v1_mae", "weeks_compared", "mean_rank", "mean_mae"}`. A week counts toward a rank comparison only when both ranks are non-None. `summary["v1"]` has `mean_rank` and `mean_mae`.
    - `cannot_replay` text, verbatim: `["v2 and market numbers (no historical odds stored)", "injury and status flags (no point-in-time snapshots)", "lineup and swap decisions (no historical rosters or recommendations)"]`.
  - `format_backtest(result: dict) -> str`. Per season: a header line, then one row per week with `n` and each method's `mae`/`mae_played`/`rank`. Then per method `"<name> vs v1: better order in A of N weeks, smaller miss in B of N weeks"`, then the notes, the irregular weeks and the `cannot_replay` lines.
  - CLI `backtest [--season S ...] [--json PATH]`. It prints `format_backtest`. `--json` writes the full result with `json.dumps(..., indent=2)`. It raises `BuildError("no Sleeper stats yet; run fetch-history first")` when `sleeper_stats` is empty. It doesn't write to the database.

- [ ] **Step 1: Write the failing tests** in `tests/test_backtest.py`. Seed `sleeper_league` (scoring `{"pos_m_g": 5}`) and `sleeper_players` for MID players `p1`–`p6`. Seed season `"2025"` stats through SQL with a helper that also inserts `TEAM_1` … `TEAM_20` rows with `gp` 1 for every seeded week, unless a test overrides them (otherwise every past-season week would count as irregular):
  - `test_no_future_leak`: `p1` scores 5 in weeks 1–3 and 50 in week 4. Week 4's `v1` projection for `p1` is 5.0. Check through a method stub registered in `METHODS` for the test that records `inp.history` and sees only weeks < 4.
  - `test_sample_requires_recent_played_week`: `p6` played only in week 1 → absent from week 7's `n`; present in week 3's.
  - `test_team_rows_ignored_and_irregular_week_skipped`: set `TEAM_3`'s `gp` to 2 in week 3 → week 3 is in `irregular_weeks` and absent from `weeks`. No `TEAM_` id is ever in a sample.
  - `test_dnp_scored_zero`: `p2` played weeks 1–2 and has no week-3 row → week 3 `by_cat["dnp"]["n"] == 1` for `v1`.
  - `test_seasons_not_pooled`: stats in `"2025"` (weeks 1–3) and `"2026"` (weeks 1–2). The 2026 week 2 history for a player excludes 2025 weeks, and both seasons appear separately in `result["seasons"]`.
  - `test_summary_counts`: with `v1` and `season_avg` over 3 weeks, `summary["season_avg"]["weeks_compared"]` equals the number of weeks where both ranks are non-None.
  - `test_cli_json_and_no_stats_error`: `main([... "backtest", "--json", path])` writes valid JSON with key `"seasons"`. On an empty DB it returns 1 and prints `error: no Sleeper stats yet; run fetch-history first`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_backtest -v`. Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `backtest.py` and the CLI command (help text: `"replay past weeks to compare projection methods"`). Load each season's stats once into memory, keyed `(week, player_id)`, rather than querying per player.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all PASS.
- [ ] **Step 5: Smoke run on the real database** (read-only):
  - `python3 -m sleeperfpl backtest`. Expected: a 2026 table for weeks 2–8 with `n` in the hundreds, and the `cannot_replay` lines.
  - If 2025 hasn't been backfilled, only 2026 shows. Running `fetch-history --season 2025` first is the owner's step on the server. It's fine on the dev machine too, but it uses the network.
- [ ] **Step 6: Docs.** In `docs/README.md`:
  - Commands table: add `backtest [--season S] [--json PATH]`.
  - Code layout: add `metrics.py` ("scoring functions shared by the report card and backtest") and `backtest.py` ("replay past weeks to compare projection methods").
  - Known limitations: note the backtest's current-club and newest-difficulty approximations, and that 2025 has no fixture data.
  - Top-level `README.md`: add one line under the quick start pointing to `backtest`.
- [ ] **Step 7: Commit** `git add sleeperfpl/backtest.py tests/test_backtest.py && git commit -am "feat: backtest command to compare projection methods"`
