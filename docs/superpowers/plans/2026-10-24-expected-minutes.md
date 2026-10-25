# Expected Minutes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop zeroing players only Sleeper calls "Out", and add a start/appearance model whose minutes feed shadow projections `v1m`/`v2m`, measured by the backtest and report card.

**Architecture:** `availability.py` turns FPL/Sleeper labels into hard-out and relative risks. `minutes.py` turns a player's Sleeper weeks into P(start), P(appear) and a per-fixture minutes mixture. `minutes_fit.py` fits the logistic (IRLS, plain Python). `projection.py` / `projection_v2.py` gain the minutes-aware versions. `report.py` wires it, `report_card.py` logs and grades it, and `backtest.py` replays it walk-forward.

**Tech Stack:** Python 3.10+ standard library, SQLite, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-24-expected-minutes-design.md`

## Global Constraints

- Standard library only. Tests use `unittest` and never touch the network. Run with `python3 -m unittest`.
- The suite stays green after every task. Shadow mode (the default) leaves `build` output unchanged except: the hard-out rule, the flag text, the hover text, the new report.json keys and the new report-card parts.
- The live hard-out rule: FPL `s`, `u`, `n`, or chance 0 in the right chance field. Sleeper never makes a player "out".
- Chance field: use `chance_this` when the target game week's `deadline_time` ≤ now, else `chance_next`.
- Label prior relative risks, Sleeper modifiers, `label_prior_k` = 20, pooling at < 30 rows, the 72-hour label age limit, staleness halfway shrink, the 0.60 cap: values exactly as in the spec.
- Model constants:
  - logistic needs ≥ 3 non-X history weeks;
  - P(sub) uses the last 10 non-starts, 5 pseudo-weeks;
  - personal start buckets use the last 10 starts, 10 pseudo-starts;
  - short history uses 3 pseudo-weeks;
  - backtest minimum is 1,500 training rows.
- `G60_CODES = {"cs"}`. Every other code is LIN. An unknown code warns once to stderr and counts as LIN.
- New config keys: `minutes_model` (`"shadow"` | `"on"`, default `"shadow"`), `label_prior_k` (20), `sleeper_label_weight` (1.0).
- Schema changes only append columns, migrated by `db._add_missing_columns`.
- Commit messages end with the two attribution lines in the session's system reminder.

## Review Focus

1. A player Sleeper marks "Out" while FPL says available must get a normal projection, appear in the tool's XI when he's good enough, and be offered as a swap or free agent. Pinned in Task 2.
2. An existing database without the new columns, with no `minutes_model` row and no `minutes_coef.json`, must still build: minutes fields are null and v1m/v2m absent. Pinned in Task 7.
3. Blank and double target weeks: v1m is 0 for a blank, and a double sums its two fixtures. Pinned in Task 6.
4. A player with no Sleeper rows this season (a new signing) goes through the short-history path without crashing, using last season's start share when stored. Pinned in Task 4.
5. After the target week's deadline, the hard-out rule and bucket read `chance_this`, so a player FPL cleared for this week isn't zeroed by next week's 0. Pinned in Task 2.

---

### Task 1: Storage, config and `fetch-status`

**Files:**
- Modify: `sleeperfpl/db.py`, `sleeperfpl/fetch_fpl.py`, `sleeperfpl/config.py`, `config.example.json`, `sleeperfpl/__main__.py`, `deploy/crontab.example`
- Test: `tests/test_db.py`, `tests/test_fetch_fpl.py`, `tests/test_config.py`, `tests/test_main.py` (create if absent, or put the CLI test in `tests/test_fetch_fpl.py`)

**Interfaces:**
- Produces:
  - `fpl_elements` columns appended: `news, news_added, chance_this`.
  - `projection_log` columns appended: `v1m REAL, v2m REAL, p_start REAL, p_app REAL, p60 REAL, e_min REAL, fpl_bucket TEXT, slp_bucket TEXT, p_start_model REAL, p_app_model REAL, p_start_fpl REAL, p_app_fpl REAL, label_at TEXT`.
  - New tables in `OTHER_SCHEMA`:
    - `minutes_model (version TEXT PRIMARY KEY, fitted_at TEXT, n_rows INTEGER, trained_on TEXT, model_json TEXT, holdout_brier REAL)`
    - `label_counts (season TEXT, kind TEXT, cell TEXT, n INTEGER, starts INTEGER, apps INTEGER, sixty INTEGER, e_start REAL, e_app REAL, built_at TEXT, PRIMARY KEY (season, kind, cell))`
  - `Config.minutes_model: str`, `Config.label_prior_k: float`, `Config.sleeper_label_weight: float`.
  - CLI `fetch-status` calls `fetch_bootstrap(conn)` only and prints `fpl players: N`.

- [ ] **Step 1: Write the failing tests**
  - `test_db.test_new_columns_migrate`: create the old `fpl_elements` and `projection_log` with their current column lists, call `connect`, and assert `PRAGMA table_info` lists `chance_this` and `label_at`. Also assert the tables `minutes_model` and `label_counts` exist.
  - `test_fetch_fpl.test_bootstrap_stores_news_and_chance_this`: fake bootstrap element with `news: "Knock"`, `news_added: "2026-10-20T10:00:00Z"`, `chance_of_playing_this_round: 75`. Assert those values in the stored row.
  - `test_config.test_minutes_defaults_and_validation`: defaults are `"shadow"`, 20, 1.0. `minutes_model: "maybe"` raises `ConfigError` mentioning `minutes_model`. `sleeper_label_weight: 1.5` raises (must be within 0–1).
  - CLI test: `main(["--config", cfg, "fetch-status"])` with `fetch_bootstrap` patched returns 0 and prints `fpl players:`.
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_db tests.test_fetch_fpl tests.test_config -v`). Expect missing columns, unknown command, and `AttributeError` on config.
- [ ] **Step 3: Implement.**
  - Append the columns to `SNAPSHOT_TABLES["fpl_elements"]` and `LOG_TABLES["projection_log"]`.
  - Add the two tables to `OTHER_SCHEMA` and `TABLES`.
  - In `fetch_bootstrap`, insert the new fields, so the insert gains three placeholders.
  - Pad `report_card.log_projections` with 13 NULLs so it keeps working.
  - Add config keys with validation, and add them to `config.example.json`.
  - `matchday` gains `step("fpl status", lambda: fetch_bootstrap(conn))` before `fpl fixtures`.
  - Crontab: add `fetch-status ; build` every 4 hours at minute 30, plus hourly 12–23 on Thursday and Friday (`* * 4,5`). Append `; python3 -m sleeperfpl fit-minutes` to the 03:30 history line (the command arrives in Task 5; the cron file is documentation).
- [ ] **Step 4: Run the tests and the full suite, expect PASS** (`python3 -m unittest`).
- [ ] **Step 5: Commit** `feat: store FPL news and this-round chance; fetch-status; minutes config`.

### Task 2: Hard-out rule and the chance field (goes live)

**Files:**
- Create: `sleeperfpl/availability.py`
- Modify: `sleeperfpl/report.py` (`_flag`, its call site)
- Test: `tests/test_availability.py`, `tests/test_report.py`

**Interfaces:**
- Produces in `availability.py`:
  - `chance_for(el: dict, deadline_passed: bool) -> int | None`: `el["chance_this"]` if `deadline_passed`, else `el["chance_next"]`.
  - `fpl_bucket(status: str | None, chance: int | None) -> str`:
    - `s`, `u`, `n` map to themselves.
    - `i` with chance None maps to `i0`; `d` with chance None maps to `d50` (ruling: FPL sends neither today).
    - Any other status with chance None or ≥ 100 maps to `a100`.
    - Otherwise `("i" if status == "i" else "d") + str(chance)`.
  - `slp_bucket(injury_status: str | None) -> str`: lowercased, or `"none"`.
  - `HARD_OUT = {"s", "u", "n", "i0", "d0"}`; `is_hard_out(bucket: str) -> bool`.
- `report._flag(sp, el, deadline_passed) -> str | None`:
  - `"no data"` when `el` is None;
  - `"out"` when the bucket is hard-out;
  - `"doubtful"` when the bucket starts with `d` or `i`;
  - `f"Sleeper: {slp}"` when the Sleeper bucket isn't `none`;
  - else None.
  - `deadline_passed` is computed once per build from the target week's `deadline_time` in `fpl_events`.

- [ ] **Step 1: Write the failing tests**
  - `test_availability.test_buckets`: `fpl_bucket("a", None) == "a100"`, `("a", 100) == "a100"`, `("d", 75) == "d75"`, `("i", 0) == "i0"`, `("i", None) == "i0"`, `("u", 0) == "u"`. `slp_bucket("Out") == "out"`, `slp_bucket(None) == "none"`.
  - `test_availability.test_chance_for_switches_at_deadline`: `el = {"chance_next": 0, "chance_this": 100}` gives 0 before the deadline and 100 after.
  - `test_report.test_sleeper_out_fpl_available_not_zeroed` (Review Focus 1): seed a player with Sleeper `injury_status` "Out", FPL status `a` and chance None, plus played history. Assert `flag == "Sleeper: out"`, `proj > 0`, and that he is in the XI starters when he is the only eligible player for a slot.
  - `test_report.test_fpl_injured_zero_is_out`: FPL `i`, chance 0 gives flag "out" and proj 0. FPL `i`, chance 75 gives "doubtful" with proj > 0. Today `i` alone is out, so this assertion fails first.
  - `test_report.test_chance_this_used_after_deadline` (Review Focus 5): the target week's `deadline_time` is before `now`, `chance_next` 0, `chance_this` 100, status `a`. Assert the flag isn't "out". Before the deadline it is "out".
  - Update `test_injured_player_flagged_out` only if its fixture relies on Sleeper "Out" alone; FPL hard-out must still give "out".
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_availability tests.test_report -v`).
- [ ] **Step 3: Implement** the functions and the new `_flag`. Find the target event in `_rows(conn, "fpl_events")` by `gw.gw`. `deadline_passed = parse_time(deadline) <= now`, and False if missing.
- [ ] **Step 4: Run the full suite, expect PASS.**
- [ ] **Step 5: Commit** `feat: only FPL's hard out zeroes a player; read this-round chance after the deadline`.

### Task 3: Shared statistics

**Files:**
- Modify: `sleeperfpl/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `wilson(successes: int, n: int, z: float = 1.645) -> tuple[float, float]`: (0, 1) when n is 0.
  - `paired_t(d: list[float]) -> tuple[float | None, int]`: (t, df). t is None for n < 2 or sd 0.
  - `mean_ci(d: list[float]) -> tuple[float, float] | None`: 95% CI of the mean, using a two-sided t-critical table for df 1–30 (`T95 = {1: 12.706, 2: 4.303, …, 30: 2.042}`) and 1.96 beyond. None for n < 2.
  - `signflip_p(d: list[float], b: int = 20000, seed: int = 1) -> float | None`: `(hits + 1) / (b + 1)`, using `random.Random(seed)`. None for an empty list.
  - `reliability(pairs: list[tuple[float, bool]], bins: int = 10) -> list[dict]`: one dict per non-empty bin, `{"lo", "hi", "n", "mean_p", "observed"}`. A p of 1.0 falls in the last bin.

- [ ] **Step 1: Write the failing tests:**
  - `wilson(5, 10)` ≈ (0.2486, 0.7514) to 3 places;
  - `wilson(0, 0) == (0.0, 1.0)`;
  - `paired_t([1, 2, 3])` gives t ≈ 3.4641, df 2;
  - `paired_t([1, 1])[0] is None`;
  - `mean_ci([1, 2, 3])` ≈ (2 − 4.303·1/√3, 2 + 4.303·1/√3);
  - `signflip_p([1, -1, 1, -1]) > 0.9`;
  - `signflip_p([5] * 10) < 0.01`;
  - `reliability([(0.05, False), (0.95, True), (1.0, True)])` gives 2 bins, the last with n 2 and observed 1.0.
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_metrics -v`).
- [ ] **Step 3: Implement** as specified. The test values pin each body.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: Wilson interval, paired tests and reliability bins`.

### Task 4: Minutes model core

**Files:**
- Create: `sleeperfpl/minutes.py`
- Modify: `sleeperfpl/sleeper_stats.py` (move `backtest._irregular` here as `irregular_week(rows) -> bool`; `backtest.py` imports it)
- Test: `tests/test_minutes.py`

**Interfaces:**
- Produces in `minutes.py`:
  - `@dataclass Week: week: int; minutes: float; started: bool` (oldest first everywhere).
  - `season_rows(conn, season, max_week=None) -> dict[int, dict[str, dict]]`, as `{week: {player_id: stats}}`.
  - `regular_weeks(rows_by_week) -> set[int]`. A week counts as regular when `not irregular_week(rows)`, or when it has no `TEAM_` rows at all (ruling: missing club rows mustn't blank the model).
  - `history(rows_by_week, pid, before: int, regular: set[int], excluded: set[int] = frozenset()) -> list[Week]`. It covers regular weeks < `before`, from the player's first week with any row. A missing row is `Week(w, 0, False)`. `excluded` holds X weeks.
  - `FEATURES = ["ewma3", "prev_st", "prev_sub", "last3", "prev_m90", "zero_streak", "GK", "DEF", "FWD", "ewma10"]`.
  - `features(hist: list[Week], pos: str) -> list[float]`. Follow the research's `features()` without `price` and `short_rest`: `ewma` over starts with half-lives 3 and 10, and `zero_streak` capped at 5 and divided by 5.
  - `@dataclass MinutesModel`:
    - fields: `version: str`, `weights: list[float]` (intercept first), `mu`, `sd`, `q_prior: dict[str, float]`, `start_buckets: dict[str, list[list[float]]]` (each entry `[minutes, prob]`), `sub_buckets: list[list[float]]`, `short_prior: dict[str, float]`, `trained_on: list[str]`, `n_rows: int = 0`;
    - `to_dict()`, `from_dict(d)`, and `p_start(x: list[float]) -> float` (standardise, then a logistic clamped at ±30).
  - `p_sub_given_not_start(hist, pos, model) -> float`: `(subs + 5·q) / (n + 5)` over the last 10 non-starts.
  - `personal_start_buckets(hist, pos, model) -> list[list[float]]`: from the last 10 starts. Shrink P(≥ 90) and P(< 60) with 10 pseudo-starts. Split the middle mass in the position's 75–89 : 60–74 ratio. Bucket minutes values are the position's.
  - `@dataclass PlayerMinutes: p_start: float; p_app: float; start_buckets; sub_buckets; short: bool`.
  - `player_minutes(hist, pos, model, last_season_share: float | None) -> PlayerMinutes`:
    - With ≥ 3 history weeks: the logistic, and `p_app = p_start + (1 − p_start)·p_sub`.
    - Otherwise the short-history rule `(starts + 3·p0) / (n + 3)`, where p0 is `last_season_share`, or `model.short_prior[pos]` when that's None. The same `p_sub` formula applies.
  - `@dataclass FixtureMinutes: scenarios: list[tuple[float, float]]; p1: float; p60: float; e_min: float`.
  - `fixture_minutes(p_start, p_app, start_buckets, sub_buckets) -> FixtureMinutes`. Use the research's mixture. Clamp `p_app ≥ p_start` and both to [0, 1].
  - `last_season_start_share(rows_by_week, pid) -> float | None`: starts over weeks with a row, among regular weeks.

- [ ] **Step 1: Write the failing tests:**
  - `test_ewma_known`: `ewma([0, 0, 1], 3)` hand value (weights 1, λ, λ² newest first, λ = 0.5^(1/3)).
  - `test_history_missing_row_is_unused_and_before_first_row_ignored`: weeks 1–4, player first appears week 2, no row in week 3. History before 5 is `[2, 3, 4]`, with week 3 at `minutes == 0`.
  - `test_irregular_week_excluded_and_no_team_rows_regular`.
  - `test_features_values`: hist S90, B20, U0, S75 (oldest first), position MID. `prev_st == 1`, `prev_m90 ≈ 0.8333`, `zero_streak == 0`, `last3 == 2/3`, `GK == DEF == FWD == 0`.
  - `test_mixture_sums_to_one_and_bounds`: for p_start 0.6 and p_app 0.8, the scenario probabilities sum to 1. `p60 ≤ p1`, `e_min ≤ 90`, `p1 == 0.8`.
  - `test_gk_rarely_sub`: a model with `q_prior["GK"] = 0.023` and a GK history of all starts gives `p_app - p_start < 0.05 * (1 - p_start) + 1e-9`.
  - `test_short_history_uses_last_season` (Review Focus 4): a player with no rows this season and `last_season_share = 0.8`, with `short_prior` MID 0.2. `p_start == (0 + 3·0.8) / 3`. With None it is 0.2. No exception.
  - `test_model_roundtrip`: `from_dict(to_dict())` equals the original.
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_minutes -v`).
- [ ] **Step 3: Implement.** Move `_irregular` and keep `backtest` behaviour identical; the backtest tests must stay green.
- [ ] **Step 4: Run the full suite, expect PASS.**
- [ ] **Step 5: Commit** `feat: minutes model core: history, features, mixture`.

### Task 5: Fitting, `fit-minutes` and the shipped default

**Files:**
- Create: `sleeperfpl/minutes_fit.py`, `sleeperfpl/minutes_coef.json`
- Modify: `sleeperfpl/__main__.py`
- Test: `tests/test_minutes_fit.py`

**Interfaces:**
- Consumes: Task 4 (`season_rows`, `regular_weeks`, `history`, `features`, `MinutesModel`, `Week`).
- Produces:
  - `solve(a: list[list[float]], b: list[float]) -> list[float]`: Gaussian elimination with partial pivoting.
  - `fit_irls(x, y, l2=1e-3, iters=25, tol=1e-8, w0=None) -> tuple[list, list, list]`: (weights, mu, sd). `w0` warm-starts.
  - `fit_gd(x, y, l2=1e-3, lr=0.5, iters=3000) -> list[float]`: weights. A test oracle only.
  - `@dataclass TrainRow: season; week; pid; pos; x: list[float]; started: bool; appeared: bool; minutes: float; hist: list`.
  - `training_rows(conn, pos_of: dict[str, str], seasons: list[str], before: tuple[str, int] | None = None) -> list[TrainRow]`:
    - target weeks are regular weeks;
    - a player needs ≥ 3 history weeks and a played week among his last 5;
    - `before=(season, week)` keeps rows strictly earlier: earlier seasons, or the same season with an earlier week.
  - `short_rows(...)`, same signature: targets for players with < 3 history weeks, used for `short_prior`.
  - `measure_priors(rows, short) -> dict`, measured from the target weeks:
    - `q_prior`: P(sub | not start) per position;
    - `start_buckets`: per position, edges ≥ 90 / 75–89 / 60–74 / < 60, each value the mean minutes in that bucket;
    - `sub_buckets`: edges < 10 / 10–19 / 20–29 / 30–59 / ≥ 60;
    - `short_prior` per position.
  - A position with no rows falls back to all-position values.
  - `fit_model(conn, pos_of, seasons, before=None, w0=None) -> MinutesModel | None`: None when fewer than 50 rows.
  - `pos_of_players(conn, rows_by_season) -> dict[str, str]`: `position_group` from the latest `sleeper_players`. When that's missing, use the first `pos_<gk|d|m|f>_` key prefix seen in the player's stats.
  - `load_model(conn) -> MinutesModel | None`: the newest `minutes_model` row, else `minutes_coef.json` next to the module, else None.
  - CLI `fit-minutes [--force] [--export PATH]`:
    - It skips with `fit-minutes: no new weeks since last fit` when the max stored (season, week) equals the newest row's `trained_on` max, unless `--force` is given.
    - Holdout: fit with the last 4 regular weeks of the newest season held out, and record their Brier on P(start). Then refit on everything and insert. `version` is `fitted_at`.
    - It prints rows, holdout Brier and version.
    - `--export` also writes `model.to_dict()` as JSON.

- [ ] **Step 1: Write the failing tests:**
  - `test_solve_3x3`, a known system.
  - `test_irls_matches_gd`: about 400 synthetic rows from a fixed seed with true weights (0.5, 1.5, -1.0). IRLS and GD weights (de-standardised, or both on standardised inputs) agree within 1e-3.
  - `test_training_rows_no_lookahead`: with `before=("2025", 5)`, no row has week ≥ 5 in 2025.
  - `test_training_rows_require_recent_play`.
  - `test_measure_priors_buckets_sum_to_one`.
  - `test_load_model_prefers_db_then_file`: write a JSON to a temp path, patching the module's default path.
  - `test_fit_minutes_skips_when_unchanged`: run the CLI twice on a seeded DB; the second run prints "no new weeks".
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_minutes_fit -v`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Generate the shipped default (network; Sleeper's public stats endpoint).**
  - Run: `python3 -m sleeperfpl fetch-history --season 2025`, then `python3 -m sleeperfpl fit-minutes --force --export sleeperfpl/minutes_coef.json`.
  - Expected: 38 weeks fetched, the irregular-week count printed by `backtest --season 2025` is small, the holdout Brier is printed, and the JSON exists.
  - Record in the ledger: the `TEAM_` check result for 2025 and the holdout Brier.
  - If the stats endpoint fails, ledger a ruling and fit on 2026 only.
  - The shipped JSON must have `trained_on` = ["2025"] only. Use `seasons=["2025"]`: the backtest relies on this, so the default never leaks 2026.
- [ ] **Step 6: Commit** `feat: fit the minutes model; fit-minutes command; default fit from 2025`.

### Task 6: Minutes-aware projections

**Files:**
- Modify: `sleeperfpl/projection.py`, `sleeperfpl/projection_v2.py`
- Test: `tests/test_projection.py`, `tests/test_projection_v2.py`

**Interfaces:**
- Consumes: `FixtureMinutes` (Task 4).
- Produces:
  - `projection_v2.G60_CODES = {"cs"}`.
  - `projection_v2.KNOWN_CODES`: every code in the stored league scoring as of 2026-10-24: `acnc aer at bs clr cos cs cs90 dis g ga hcs int kp min og pkd pkm pks rc sm sot sv tkw yc yc2`.
  - `projection.minutes_rates(weeks: list[dict], scoring: dict) -> tuple[float, float]`: (lin90, cs60) over the given played weeks (most recent first, caller slices `[:window]`).
    - lin90 is the ratio of sums, 0 when there are no minutes.
    - cs60 is the mean cs points over 60+ minute weeks, 0 when there are none.
    - A code outside `KNOWN_CODES` warns once per process via `sys.stderr`.
  - `projection.project_minutes(lin90, cs60, fixtures: list[FixtureMinutes], multipliers_per_fixture: list[float]) -> float`: v1m.
  - `projection_v2.V2mInputs: att90, other90, cs_points, ga_points`, from `v2m_inputs(weeks, scoring, prefix, window) -> V2mInputs | None` (ratio of sums per bucket).
  - `projection_v2.project_v2m(inp, fm: FixtureMinutes, own_lam, opp_lam, team_baseline) -> float`: `cs_points·exp(−opp)·fm.p60 + ga_points·opp·fm.e_min/90 + att90·fm.e_min/90·own/base + other90·fm.e_min/90`.

- [ ] **Step 1: Write the failing tests:**
  - `test_minutes_rates_ratio_of_sums`: weeks `{min 90, pos_d_tkw 3, pos_d_cs 1}` and `{min 30, pos_d_g 1}`, with `SC` from `test_projection_v2`. lin90 = (3 + 10)/120·90 = 9.75; cs60 = 6.0.
  - `test_project_minutes_threshold`: one fixture with scenarios [(90, 0.5), (20, 0.3), (0, 0.2)], lin90 9, cs60 6, multiplier 1. Value = 0.5·(9 + 6) + 0.3·(9·20/90) = 8.1.
  - `test_project_minutes_blank_and_double` (Review Focus 3): no fixtures gives 0. Two identical fixtures with multipliers 1.2 and 0.8 give 2 × the single value at 1.0.
  - `test_unknown_code_warns_once`: capture stderr, call twice with `pos_d_zzz`, and see exactly one warning.
  - `test_project_v2m_defender`: fm with p60 1, e_min 90 equals `project_v2` for an equivalent `V2Inputs` with p60 1.
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_projection tests.test_projection_v2 -v`).
- [ ] **Step 3: Implement.** Reuse `split_points` for the att/other/def split, and classify per code as `split_points` does.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat: v1m and v2m projections from minutes scenarios`.

### Task 7: Label layer and build wiring (shadow)

**Files:**
- Modify: `sleeperfpl/availability.py`, `sleeperfpl/models.py`, `sleeperfpl/report.py`, `sleeperfpl/report_card.py` (`log_projections`)
- Test: `tests/test_availability.py`, `tests/test_report.py`, `tests/test_report_card.py`

**Interfaces:**
- Consumes: Tasks 2, 4, 5, 6.
- Produces in `availability.py`:
  - `FPL_PRIOR = {"a100": (1.0, 1.0), "d75": (0.63, 0.82), "i75": (0.57, 0.76), "d50": (0.29, 0.43), "i50": (0.23, 0.38), "d25": (0.09, 0.16), "i25": (0.06, 0.11)}`. Any other non-hard-out bucket uses its nearest chance level's `d` entry.
  - `SLP_PRIOR = {("a100", "out"): (0.80, 0.95), ("a100", "gtd"): (0.90, 1.00), ("flagged", "out"): (0.90, 0.95), ("a100", "sus"): (0.50, 0.50)}`; anything else is (1.0, 1.0), fixed.
  - `class LabelTable`, built as `LabelTable(counts: dict[(kind, cell), dict] | None = None, k: float = 20)`:
    - `rr_fpl(bucket) -> (rs, ra)`;
    - `rr_slp(fpl_bucket, slp_bucket) -> (ss, sa)`.
  - With no counts it returns the priors exactly.
  - `apply_labels(p_start, p_app, fpl_b, slp_b, fpl_stale: bool, table: LabelTable, weight: float) -> tuple[float, float, float, float]`: (p_start_fpl, p_app_fpl, p_start, p_app). It follows the spec's rule, including staleness and the 0.60 cap. Hard-out returns zeros. The final values satisfy `p_app ≥ p_start`.
  - `fpl_stale(news_added: str | None, club_last_kickoff: str | None) -> bool`.
- `PlayerCtx` gains `p_start, p_app, p60, e_min, fpl_bucket, slp_bucket, proj_v1m, proj_v2m, p_start_model, p_app_model, p_start_fpl, p_app_fpl`, all `None` by default.
- `report.build_report`:
  - load the model once (`minutes_fit.load_model`) and the season rows once (`minutes.season_rows`);
  - per matched player with Sleeper stats: history, `player_minutes`, `apply_labels`, then per fixture `fixture_minutes`, then `proj_v1m` and (when v2 used a market for that fixture) `proj_v2m`;
  - per-fixture `p60`/`e_min` on the player are the first fixture's;
  - with `cfg.minutes_model == "on"`: `pl.proj = proj_v2m if pl.proj_source == "market" else proj_v1m` (when not None);
  - the table for live builds is `LabelTable(load_counts(conn, season), cfg.label_prior_k)` (from Task 8; until then `LabelTable()`).
- `_player_dict` adds `p_start, p_app, p60, e_min, fpl_bucket, slp_bucket, proj_v1m, proj_v2m` (rounded to 2 for probabilities and 1 for points).
- Hover text appends `· starts: 85% · plays: 92% · FPL: d75 · Sleeper: out (estimate)` when `p_start` is not None. Percent is rounded; Sleeper is omitted when `none`.
- `log_projections` writes the 13 new columns. `label_at` is `latest_fetched_at(conn, "fpl_elements")`, passed in.

- [ ] **Step 1: Write the failing tests**
  - `test_availability.test_priors_without_counts`: `apply_labels(0.8, 0.9, "a100", "out", False, LabelTable(), 1.0)` → p_start 0.64, p_app 0.855.
  - `test_availability.test_weight_zero_ignores_sleeper` → (0.8, 0.9).
  - `test_availability.test_stale_short_doubt_halfway`: `d75`, stale → rs = 1 − 0.5·0.37.
  - `test_availability.test_stale_long_injury_not_revived`: `i25` stale keeps 0.06.
  - `test_availability.test_stale_fpl_sleeper_out_cap`: `a100` stale + out → start modifier 0.60.
  - `test_availability.test_hard_out_zero`.
  - `test_report.test_minutes_fields_in_report`: seeded DB with Sleeper weeks (including `gs`), and a model written into `minutes_model`. The player dict has `0 < p_start ≤ p_app ≤ 1`, `proj_v1m` not None, and the hover contains `starts:`. `proj` equals v1 (shadow).
  - `test_report.test_minutes_on_uses_v1m`: config `minutes_model: "on"` → `proj == proj_v1m`.
  - `test_report.test_builds_without_model` (Review Focus 2): patch the default JSON path to a missing file, with no `minutes_model` rows. The build succeeds, `p_start` is None, and `proj_v1m` is None.
  - `test_report_card.test_log_projections_minutes_columns`: logged row has `fpl_bucket`, `p_start_model`, `label_at`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
  - Get the club's last kickoff from `fpl_fixtures`: the max `kickoff_time` < now among that club's fixtures.
  - Position for the model comes from `FPL_POSITIONS[el["element_type"]]` mapped to GK/DEF/MID/FWD.
  - `last_season_share` reads the previous season's rows, when stored (`str(int(season) − 1)`).
- [ ] **Step 4: Run the full suite, expect PASS.**
- [ ] **Step 5: Commit** `feat: label layer and minutes-aware projections in shadow`.

### Task 8: Learning labels and `label-audit`

**Files:**
- Modify: `sleeperfpl/availability.py`, `sleeperfpl/report.py`, `sleeperfpl/__main__.py`
- Test: `tests/test_availability.py`

**Interfaces:**
- Consumes: the `projection_log` columns (Task 1/7), `wilson` (Task 3), `irregular_week` (Task 4).
- Produces:
  - `rebuild_label_counts(conn, season, last_finished, built_at) -> int`:
    - For each finished, fetched, regular week, take the last pre-kickoff `projection_log` row per player that has `p_start_model` not null, a `fpl_bucket`, and `kickoff − label_at ≤ 72 h`.
    - Outcomes come from `sleeper_stats`: started is `gs ≥ 1`, appeared is `min > 0`, sixty is `min ≥ 60`. No row counts as not started and not appeared.
    - Accumulate `kind="fpl", cell=fpl_bucket` (e from `p_*_model`) and `kind="sleeper", cell=f"{'a100' if fpl_bucket == 'a100' else 'flagged'}+{slp_bucket}"` (e from `p_*_fpl`).
    - Replace the season's rows in `label_counts`. Returns the number of rows.
  - `load_counts(conn, season) -> dict[(kind, cell), dict]`.
  - `LabelTable` learning:
    - `RR = (O + k·RR_prior) / (E + k)`, capped to [0, 1.2], separately for start and appear.
    - For an FPL bucket with n < 30, use the pooled counts of `d<c>` + `i<c>`.
    - Hard-out and "anything else" cells are never learned.
  - `audit_rows(conn, season) -> list[dict]`: `{kind, cell, n, start_rate, app_rate, sixty_rate, app_lo, app_hi}`, with Wilson bounds on appearances.
  - `format_audit(rows) -> str`: the `a100+out` row comes first under the heading "Sleeper says Out, FPL says available".
  - `build_report` calls `rebuild_label_counts` before projections (cheap) and uses the learned table.
  - CLI `label-audit` rebuilds, then prints `format_audit`. With no rows it prints `no label history yet: it builds up as weeks finish after builds have logged labels`.

- [ ] **Step 1: Write the failing tests:**
  - `test_rebuild_counts_observed_expected`: two logged players in a finished week, both `a100`/`out` with `p_start_model` 0.8, `p_start_fpl` 0.8 and `label_at` 1 h before kickoff. One started, one didn't appear. Assert fpl `a100` n 2, starts 1, e_start 1.6; sleeper `a100+out` n 2.
  - `test_label_age_limit`: `label_at` 80 h before kickoff → not counted.
  - `test_post_kickoff_rows_ignored`.
  - `test_learned_rr`: counts O 10, E 10, prior 0.8, k 20 → (10 + 16)/30.
  - `test_pooling_under_30`.
  - `test_audit_text_leads_with_sleeper_out`.
  - `test_label_audit_empty_message`, via `main`.
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_availability -v`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite, expect PASS.**
- [ ] **Step 5: Commit** `feat: learn label reliability from logged builds; label-audit`.

### Task 9: Backtest: v1m, minutes section, oracle, paired tests

**Files:**
- Modify: `sleeperfpl/backtest.py`, `sleeperfpl/__main__.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: Tasks 3, 4, 5, 6.
- Produces:
  - `BacktestInput` gains `minutes: list | None = None` (one `FixtureMinutes` per week-g fixture, or None).
  - `METHODS["v1m"]` returns None when `inp.minutes` is None. Methods may now return None: those rows are left out of that method's scoring, and `_score([])` yields None metrics.
  - `run_backtest(conn, cfg, seasons=None, minutes=False)`:
    - Per (season, g), fit walk-forward with `minutes_fit.fit_model(conn, pos_of, seasons_le, before=(season, g), w0=prev_weights)`. When it returns None or its `n_rows` < 1,500, use `load_model`'s file default if all its `trained_on` < season, else no model.
    - The label layer is not applied.
    - Each week entry gains `"model": "fit" | "default" | None` and, for v1m, `"oracle_mae"`: v1m with each single-fixture player's actual minutes as scenario `[(min, 1.0)]`.
    - Season results gain `"paired": {"mae": {...}, "rank": {...}}`, each `{"n", "mean", "ci", "t", "df", "p_signflip"}` for v1m − v1 per week.
    - With `minutes=True`, they also gain `"minutes"`:
      - Brier for start/app/60 for the model, `b1` (last week clipped to 0.05–0.95) and `b2` ((count in last 5 + 0.5)/6);
      - `reliability` for P(start);
      - `n`, over players with a start in their last 10 history weeks, in regular weeks only.
  - `format_backtest` prints:
    - the v1m columns;
    - a paired line, `v1m vs v1: miss −0.21 (95% −0.40 to −0.02, p 0.03), order +0.01 (…)`;
    - `minutes error share: MAE v1m X, with actual minutes Y`;
    - with `--minutes`, the Brier table and reliability bins.
  - CLI `backtest --minutes`.

- [ ] **Step 1: Write the failing tests:**
  - `test_v1m_no_lookahead`: spy on `fit_model` and assert every `before == (season, gw)` for the scored gw.
  - `test_v1m_skipped_without_model`: patch `fit_model` → None and `load_model` → None. v1m metrics are None, and v1 is unchanged.
  - `test_default_model_not_used_for_its_own_season`: a default with `trained_on ["2025"]` isn't used for 2025.
  - `test_minutes_section_baselines`: seeded data where a player always starts. The model's and b2's Brier on start are both < 0.1, and the keys are present.
  - `test_paired_summary_present`.
  - `test_format_mentions_minutes_error_share`.
- [ ] **Step 2: Run, expect FAIL** (`python3 -m unittest tests.test_backtest -v`).
- [ ] **Step 3: Implement.** Training rows for the walk-forward come from all stored seasons ≤ the replayed season.
- [ ] **Step 4: Run the full suite, expect PASS.** Then smoke on the dev DB: `python3 -m sleeperfpl backtest --minutes`. It finishes in under 10 minutes and prints both seasons. Ledger the numbers.
- [ ] **Step 5: Commit** `feat: backtest the minutes model and v1m against v1`.

### Task 10: Report card and page

**Files:**
- Modify: `sleeperfpl/report_card.py`, `sleeperfpl/report.py` (`_report_card`, the report.json `report_card`)
- Test: `tests/test_report_card.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: the `projection_log` columns, `audit_rows` (Task 8).
- Produces:
  - `score_weeks` per week gains keys:
    - `n_v1m`, `mae_v1m`, `rank_v1m`, `mae_v1_v1m` and `rank_v1_v1m` (v1 on the same players);
    - `n_v2m`, `mae_v2m`, `rank_v2m`, `mae_v2_v2m` and `rank_v2_v2m` (v2 on players with both v2 and v2m);
    - `brier_start`, `brier_app`, `n_minutes` (rows with `p_start`; DNP counts as not appeared).
  - `report_card` in report.json gains `labels: audit_rows(...)`.
  - The HTML report card:
    - a column "With minutes (v1m / v1): miss · order", showing e.g. `4.10 / 4.87 · 0.35 / 0.30`;
    - a column "Start-odds score" showing `brier_start`;
    - a "Labels: how often flagged players played" table when `labels` is non-empty, with columns Label, Players, Started, Played, Played range, 60+;
    - a note under it: "Ranges are 90% intervals. Few players means a wide range."

- [ ] **Step 1: Write the failing tests:**
  - `test_score_weeks_v1m_paired`: logged v1 and v1m for 3 players, one with v1m None. `n_v1m == 2`, and `mae_v1_v1m` is computed on those 2.
  - `test_brier_start_counts_dnp`: p_start 0.9, no stats row → Brier 0.81 on start.
  - `test_report_card_html_minutes_and_labels`: the page contains `With minutes` and `how often flagged players played` when the data exists, and neither header without it.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite, expect PASS.** Smoke on the dev DB: `python3 -m sleeperfpl build` writes the page. Its XI and swaps match the pre-project build, except for players only Sleeper called "Out".
- [ ] **Step 5: Commit** `feat: grade minutes and label reliability on the report card`.

### Task 11: Docs

**Files:**
- Modify: `docs/README.md`, `README.md`

- [ ] **Step 1:** Document:
  - the commands `fetch-status`, `fit-minutes [--force] [--export]`, `label-audit` and `backtest --minutes`;
  - the config keys;
  - the new columns and tables;
  - the hard-out rule and the chance field;
  - shadow vs on, with the switch-on rule from the spec;
  - new files in the code layout;
  - limitations: no returns-from-injury handling, double weeks use the same inputs, labels can't be backtested, and the label table starts from guesses.
- [ ] **Step 2:** Run `python3 -m unittest`. Expect PASS.
- [ ] **Step 3: Commit** `docs: expected minutes`.
