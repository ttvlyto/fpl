# Sleeper Premier League Rankings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect Sleeper + FPL data on a cron schedule into SQLite and publish one static web page with the owner's best XI, smart swap tips, and top free agents, scored in the league's own scoring.

**Architecture:** A `sleeperfpl/` Python package next to the existing `sleeper.py`. Independent fetchers (Sleeper, FPL) write append-only snapshots into one SQLite file. A `build` step reads the latest snapshots, matches players across sources, re-scores FPL match history with an editable JSON scoring map, projects points, picks the best legal XI, computes swap tips, and writes `report.json` + `index.html`.

**Tech Stack:** Python 3.10+, standard library only at runtime (`urllib`, `sqlite3`, `json`, `unittest`). No third-party packages.

**Spec:** `docs/superpowers/specs/2026-10-24-fpl-rankings-design.md`

## Global Constraints

- Runtime: Python 3.10+ standard library only, matching `sleeper.py`. Tests use `unittest`: run with `python3 -m unittest discover -s tests -v`.
- `sleeper.py` CLI commands (`rosters`, `free-agents`, `player`, `refresh`) must keep working unchanged.
- Nothing machine-specific is hard-coded. League ID, owner, DB path, output dir and every tunable come from `config.json` (path from `--config` or env `SLEEPER_FPL_CONFIG`, default `./config.json`). Relative paths in it resolve against the config file's directory.
- Difficulty multipliers default `{"1": 1.2, "2": 1.1, "3": 1.0, "4": 0.9, "5": 0.8}`. AVG window default 5. Top free agents per position default 10.
- Never rank by Sleeper `search_rank`.
- Snapshots (league, players status, rosters, trending, FPL elements/teams/events/fixtures) are append-only, tagged with one `fetched_at` (ISO-8601 UTC) per fetch run. FPL match history is re-downloadable and may be upserted.
- `scoring_map.json` is generated only when absent and never overwritten.
- No network calls in unit tests. Fetchers take an injectable `get_json` so tests pass fakes.
- The FPL game week (event id) is the one week number used everywhere. Sleeper's week number isn't used. *(Plan's call.)*

## Review Focus

1. **A rostered player has no FPL match** (new signing, name mismatch). They appear in an "Unmatched" list on the page. They can still fill a lineup slot (PROJ 0, flagged "no data") so the XI stays legal, and they're never suggested as a drop. Test owner: Task 9 / Task 10 / Task 11.
2. **A fetch fails part-way** (FPL down, trending endpoint 404 for soccer). Previous snapshots stay intact. `build` still produces a page from the latest good data and shows when each source was last fetched. Test owner: Task 3 / Task 4 / Task 11.
3. **`roster_positions` contains a slot name not in `slot_eligibility`.** `build` stops with an error naming that slot instead of guessing a lineup. Test owner: Task 9.
4. **A player with zero appearances.** AVG5 is `None`, count is 0, PROJ is 0, with no division by zero. Test owner: Task 8.
5. **A hand-edited `scoring_map.json` with bad JSON or an unknown FPL field.** The error names the file and the key. Re-running never overwrites the file. Test owner: Task 6.

---

## File Structure

```
sleeper.py                    (unchanged; helpers player_name/positions/rostered_ids are imported)
config.example.json           example config, copied to config.json by the owner
sleeperfpl/
  __init__.py
  __main__.py                 CLI: fetch-daily | fetch-rosters | fetch-history | coverage | build
  config.py                   load + validate config, defaults
  http.py                     get_json with retries/throttle, FetchError
  db.py                       connect/schema, latest-snapshot helpers, meta key/value
  fetch_sleeper.py            league, players, rosters, trending snapshots
  fetch_fpl.py                bootstrap, fixtures, per-player history
  matching.py                 Sleeper <-> FPL player map, overrides
  scoring_map.py              generate/load scoring_map.json, score_match, coverage, Sleeper-stats probe
  gameweek.py                 target game week + instant/waiver mode
  projection.py               AVG5 / PROJ (the swappable projection)
  lineup.py                   best legal XI, swap tips, free-agent ranking
  models.py                   dataclasses shared across modules
  report.py                   assemble report dict, render HTML, write outputs
deploy/
  crontab.example
  sleeperfpl-web.service      systemd unit serving the output dir on the LAN
README.md
tests/
  __init__.py
  test_config.py test_db.py test_fetch_sleeper.py test_fetch_fpl.py test_matching.py
  test_scoring_map.py test_gameweek.py test_projection.py test_lineup.py test_report.py
```

### Shared models (`sleeperfpl/models.py`, created in Task 1)

```python
@dataclass
class Fixture:
    opponent: str          # FPL short_name, e.g. "LIV"
    home: bool
    difficulty: int        # 1-5, from the player's side
    kickoff: str | None    # ISO UTC

@dataclass
class PlayerCtx:
    sleeper_id: str
    name: str
    positions: list[str]           # Sleeper fantasy_positions, primary first
    club: str                      # display club code
    fpl_id: int | None             # None = unmatched
    on_roster: bool = False        # on the owner's roster
    in_reserve: bool = False       # owner's IR
    flag: str | None = None        # "doubtful" | "out" | "no data" | None
    fixtures: list[Fixture] = field(default_factory=list)
    history: list[float] = field(default_factory=list)   # league points, most recent first, minutes > 0 only
    avg: float | None = None
    n: int = 0
    proj: float = 0.0
    trending_adds: int | None = None

    def value(self) -> float:  # used for bench comparisons
        return ((self.avg or 0.0) + self.proj) / 2
```

---

### Task 1: Scaffold, git, and config

**Files:**
- Create: `.gitignore`, `sleeperfpl/__init__.py`, `sleeperfpl/models.py`, `sleeperfpl/config.py`, `config.example.json`, `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `class ConfigError(Exception)`; `load_config(path: str | None = None) -> Config`. `Config` is a dataclass with: `league_id: str`, `owner: str` (Sleeper user_id or display_name), `db_path: Path`, `output_dir: Path`, `scoring_map_path: Path`, `overrides_path: Path`, `difficulty_multipliers: dict[int, float]`, `avg_window: int`, `free_agents_per_position: int`, `swap_min_gain: float`, `max_swaps: int`, `slot_eligibility: dict[str, list[str]]`, `fpl_request_interval: float`.
- Defaults: `db_path="data/fpl.db"`, `output_dir="site"`, `scoring_map_path="scoring_map.json"`, `overrides_path="overrides.json"`, multipliers as in Global Constraints, `avg_window=5`, `free_agents_per_position=10`, `swap_min_gain=1.0`, `max_swaps=3`, `slot_eligibility={"GK":["GK"],"DEF":["DEF"],"MID":["MID"],"FWD":["FWD"]}`, `fpl_request_interval=0.5`. `league_id` and `owner` are required.

- [ ] **Step 1: Initialize git and ignore files**

Run `git init`. `.gitignore`: `__pycache__/`, `.superpowers/`, `data/`, `site/`, `config.json`.

- [ ] **Step 2: Write the failing tests** in `tests/test_config.py`

```python
def test_defaults_and_relative_paths(self):
    # config.json in tmpdir with only {"league_id": "123", "owner": "me"}
    cfg = load_config(str(path))
    self.assertEqual(cfg.db_path, tmpdir / "data/fpl.db")
    self.assertEqual(cfg.difficulty_multipliers[1], 1.2)
    self.assertEqual(cfg.difficulty_multipliers[5], 0.8)
    self.assertEqual(cfg.avg_window, 5)
    self.assertEqual(cfg.free_agents_per_position, 10)

def test_env_var_path(self):         # SLEEPER_FPL_CONFIG used when path is None
def test_missing_league_id_raises(self):   # ConfigError, message contains "league_id"
def test_invalid_json_names_file(self):    # ConfigError, message contains the file path
def test_override_tunable(self):     # {"swap_min_gain": 2.5} -> cfg.swap_min_gain == 2.5
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `python3 -m unittest tests.test_config -v`. Expected: ImportError / FAIL.

- [ ] **Step 4: Implement** `models.py` (exactly the shared models above) and `config.py`. Multiplier keys arrive as JSON strings and are converted to `int`. Write `config.example.json` with every key and its default, `"league_id": "YOUR_LEAGUE_ID"`, `"owner": "YOUR_SLEEPER_DISPLAY_NAME"`.

- [ ] **Step 5: Run tests, verify they pass.** Same command. Expected: OK.

- [ ] **Step 6: Commit** `feat: scaffold sleeperfpl package and config`

---

### Task 2: HTTP client and database

**Files:**
- Create: `sleeperfpl/http.py`, `sleeperfpl/db.py`, `tests/test_db.py`

**Interfaces:**
- Produces:
  - `class FetchError(Exception)`; `get_json(url: str, *, retries: int = 3, timeout: int = 120, min_interval: float = 0.0) -> Any`. Retries on URLError/5xx/429 with backoff `2**attempt` seconds. Raises `FetchError` (never `sys.exit`). `min_interval` enforces a gap since the previous call (module-level last-call time). User-Agent `sleeper-fpl/1.0`.
  - `connect(path: Path) -> sqlite3.Connection`: creates parent dir and schema, `row_factory = sqlite3.Row`.
  - `now_iso() -> str` (UTC, seconds precision, `Z` suffix).
  - `latest(conn, table: str) -> list[sqlite3.Row]`: rows where `fetched_at = MAX(fetched_at)` in that table, `[]` if empty.
  - `latest_fetched_at(conn, table: str) -> str | None`.
  - `meta_get(conn, key: str) -> str | None`, `meta_set(conn, key: str, value: str) -> None`.
- Schema (all `CREATE TABLE IF NOT EXISTS`; every snapshot table also gets an index on `fetched_at`):
  - `sleeper_league(fetched_at, league_id, data_json)`
  - `sleeper_players(fetched_at, player_id, name, team, positions_json, status, injury_status, active)`
  - `sleeper_rosters(fetched_at, roster_id, owner_id, players_json, starters_json, reserve_json)`
  - `sleeper_users(fetched_at, user_id, display_name)`
  - `sleeper_trending(fetched_at, direction, player_id, count)`
  - `fpl_elements(fetched_at, fpl_id, first_name, second_name, web_name, team_id, element_type, status, chance_next, total_minutes)`
  - `fpl_teams(fetched_at, team_id, short_name, name)`
  - `fpl_events(fetched_at, event_id, deadline_time, finished)`
  - `fpl_fixtures(fetched_at, fixture_id, event, team_h, team_a, team_h_difficulty, team_a_difficulty, kickoff_time, finished)`
  - `fpl_history(fpl_id, fixture_id, round, kickoff_time, minutes, stats_json, fetched_at, PRIMARY KEY (fpl_id, fixture_id))`
  - `meta(key PRIMARY KEY, value)`

- [ ] **Step 1: Write the failing tests** in `tests/test_db.py`

```python
def test_latest_returns_only_newest_snapshot(self):
    conn = connect(tmp / "t.db")
    conn.execute("INSERT INTO sleeper_trending VALUES ('2026-01-01T00:00:00Z','add','1',5)")
    conn.execute("INSERT INTO sleeper_trending VALUES ('2026-01-02T00:00:00Z','add','2',9)")
    rows = latest(conn, "sleeper_trending")
    self.assertEqual([r["player_id"] for r in rows], ["2"])

def test_latest_empty_table(self):          # == []
def test_meta_roundtrip(self):              # meta_set then meta_get; missing key -> None
def test_connect_twice_is_idempotent(self): # second connect on same file doesn't raise
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_db -v`

- [ ] **Step 3: Implement** `http.py` and `db.py`. `latest` must reject table names not in the schema's table set (prevents SQL injection via the f-string).

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit** `feat: add http client and sqlite schema`

---

### Task 3: Sleeper fetcher

**Files:**
- Create: `sleeperfpl/fetch_sleeper.py`, `tests/test_fetch_sleeper.py`

**Interfaces:**
- Consumes: `get_json`, `FetchError`, `connect`, `now_iso` (Task 2); `player_name`, `positions` from `sleeper.py`.
- Produces:
  - `fetch_league(conn, league_id: str, get=get_json) -> str` stores league + users and returns the sport slug.
  - `fetch_players(conn, sport: str, get=get_json) -> int` stores only players with `active` true and a `team`, and returns the count.
  - `fetch_rosters(conn, league_id: str, get=get_json) -> int`.
  - `fetch_trending(conn, sport: str, get=get_json) -> int` calls `players/{sport}/trending/add?lookback_hours=24&limit=100`. On `FetchError` it logs a warning to stderr and returns 0 without raising.
  - Each function uses one `now_iso()` for all rows it inserts and wraps inserts in one transaction, so a failure part-way leaves no half snapshot.

- [ ] **Step 1: Write the failing tests** with a fake `get` that maps path suffix → canned JSON:

```python
def test_fetch_league_returns_sport_and_stores_users(self):
    sport = fetch_league(conn, "L1", get=fake({"league/L1": {"sport": "clubsoccer:epl", "scoring_settings": {"goal": 6}, "roster_positions": ["GK"]},
                                               "league/L1/users": [{"user_id": "u1", "display_name": "me"}]}))
    self.assertEqual(sport, "clubsoccer:epl")
    self.assertEqual(latest(conn, "sleeper_users")[0]["display_name"], "me")

def test_fetch_players_skips_inactive_and_clubless(self):  # 3 players in, 1 stored
def test_trending_failure_is_non_fatal(self):
    # fake get raises FetchError for the trending path
    self.assertEqual(fetch_trending(conn, "x", get=failing), 0)
    self.assertEqual(latest(conn, "sleeper_trending"), [])
def test_failed_rosters_fetch_keeps_previous_snapshot(self):
    # first fetch succeeds; second raises FetchError -> latest() still returns first snapshot
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_fetch_sleeper -v`
- [ ] **Step 3: Implement.** Use `sys.path` so `import sleeper` works from the repo root (the package lives next to it). Don't call `sleeper.get`/`load_players`, because they `sys.exit` on failure.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: add Sleeper snapshot fetcher`

---

### Task 4: FPL fetcher

**Files:**
- Create: `sleeperfpl/fetch_fpl.py`, `tests/test_fetch_fpl.py`

**Interfaces:**
- Consumes: Task 2.
- Produces:
  - `FPL = "https://fantasy.premierleague.com/api"`
  - `fetch_bootstrap(conn, get=get_json) -> int` stores `elements`, `teams`, `events` from `bootstrap-static/` with one `fetched_at`.
  - `fetch_fixtures(conn, get=get_json) -> int` from `fixtures/`.
  - `fetch_history(conn, *, force: bool = False, interval: float = 0.5, get=get_json) -> int`. Fetches `element-summary/{id}/` for every element in the latest `fpl_elements` with `total_minutes > 0`. It upserts each `history` row into `fpl_history`, with `stats_json` holding the whole row. It runs only when `force`, or when the highest finished `event_id` in the latest `fpl_events` is greater than `meta["history_last_gw"]`. On success it sets that meta key. Returns the count of players fetched (0 when skipped). A `FetchError` on one player is logged and skipped, but then `history_last_gw` is not advanced, so the next run retries.

- [ ] **Step 1: Write the failing tests**

```python
def test_history_skipped_when_no_new_finished_gw(self):
    # events: gw 8 finished, meta history_last_gw = "8" -> returns 0, fake get never called
def test_history_runs_and_advances_meta(self):
    # gw 9 finished, meta "8"; 2 elements (one with total_minutes 0) -> 1 request, meta becomes "9"
    # stats_json of stored row round-trips the canned history row
def test_history_partial_failure_does_not_advance(self):
    # one element raises FetchError -> meta stays "8", other element's rows stored
def test_history_upsert_replaces_row(self):  # same (fpl_id, fixture_id) twice -> 1 row, newest minutes
def test_bootstrap_stores_teams_and_events(self)
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_fetch_fpl -v`
- [ ] **Step 3: Implement.** Pass `min_interval=interval` to `get` for the element-summary calls only. Fakes accept and ignore kwargs.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: add FPL fetcher with game-week guard`

---

### Task 5: Player matching

**Files:**
- Create: `sleeperfpl/matching.py`, `tests/test_matching.py`

**Interfaces:**
- Produces:
  - `normalize_name(s: str) -> str`: NFKD, strip accents, lowercase, letters and spaces only, collapse spaces.
  - `load_overrides(path: Path) -> dict`, returning `{"players": {sleeper_id: fpl_id}, "clubs": {sleeper_code: fpl_short_name}}`. A missing file gives empty dicts. Bad JSON raises `ConfigError` naming the file.
  - `@dataclass MatchResult: mapping: dict[str, int]; method: dict[str, str]; unmatched: list[str]`
  - `match_players(sleeper_players: list[dict], elements: list[dict], teams: list[dict], overrides: dict) -> MatchResult`. Inputs are the latest snapshot rows as dicts (keys as in the Task 2 schema; `positions` already decoded to a list).
- FPL position by `element_type`: `{1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}`.

Matching order per Sleeper player (method recorded):
1. `override` when the Sleeper id is in `overrides["players"]`.
2. Candidates = FPL elements whose team short_name equals the Sleeper club (after `overrides["clubs"]` aliasing).
3. `exact`: normalized `first second` equals normalized Sleeper name.
4. `last+initial`: normalized last tokens equal and first initials equal.
5. `web_name`: normalized `web_name` equals the Sleeper last name or full name.
6. Within each step, if more than one candidate matches, narrow by position. If it's still not unique, the player is unmatched (never guess).
Each FPL id may map to at most one Sleeper id. A second claim makes both unmatched unless one is an override.

- [ ] **Step 1: Write the failing tests**

```python
def test_accented_exact_match(self):      # Sleeper "Martin Odegaard" ARS <-> FPL "Martin Ødegaard" ARS -> exact
def test_club_alias(self):                # Sleeper club "AFC" + overrides clubs {"AFC": "ARS"} -> matched
def test_web_name_match(self):            # Sleeper "Gabriel" vs FPL first "Gabriel", second "dos Santos Magalhães", web "Gabriel"
def test_ambiguous_stays_unmatched(self): # two FPL "Ben White"-style same-name same-club -> in unmatched
def test_override_wins(self)
def test_no_club_candidates_unmatched(self)
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_matching -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: match Sleeper players to FPL ids`

---

### Task 6: Scoring map, re-scoring, and coverage check

**Files:**
- Create: `sleeperfpl/scoring_map.py`, `tests/test_scoring_map.py`

**Interfaces:**
- Consumes: `ConfigError` (Task 1).
- Produces:
  - `FPL_FIELDS = ["minutes","goals_scored","assists","clean_sheets","goals_conceded","own_goals","penalties_saved","penalties_missed","yellow_cards","red_cards","saves","bonus","bps","tackles","recoveries","clearances_blocks_interceptions","defensive_contribution"]`
  - `@dataclass ScoringRule: key: str; fpl_field: str | None; points: float; min: float | None = None; per: float | None = None; positions: list[str] | None = None`
  - `generate_scoring_map(scoring_settings: dict[str, float]) -> dict`: returns `{"rules": {key: {"fpl_field": ..., "points": value, "positions": ... (only if detected)}}}` with keys sorted.
  - `ensure_scoring_map(path: Path, scoring_settings: dict) -> bool` writes only if `path` doesn't exist and returns whether it wrote.
  - `load_scoring_map(path: Path) -> list[ScoringRule]` raises `ConfigError` naming the file on bad JSON, and naming file + key when `fpl_field` isn't null and isn't in `FPL_FIELDS`.
  - `score_match(stats: dict, position: str, rules: list[ScoringRule]) -> float`
  - `coverage(rules) -> tuple[list[str], list[str]]` returns (covered keys, missing keys). Keys with `points == 0` count as covered.
  - `probe_sleeper_stats(sport: str, season: str, week: int, get=get_json) -> list[str]` tries `https://api.sleeper.app/v1/stats/{sport}/regular/{season}/{week}` and `https://api.sleeper.com/stats/{sport}/{season}/{week}?season_type=regular`. It returns one human-readable line per URL (status / item count / error) and never raises.

Suggestion rules for `generate_scoring_map`, checked in order. The first substring found in the lowercased key wins:

```
own_goal→own_goals, pen_save|penalty_save|ps→penalties_saved, pen_miss|penalty_miss|pm→penalties_missed,
clean_sheet|cs→clean_sheets, conced→goals_conceded, goal→goals_scored, assist|ast→assists,
yellow|yc→yellow_cards, red|rc→red_cards, save→saves, bonus→bonus, tackle|tkl→tackles,
recover→recoveries, clearance|block|intercept→clearances_blocks_interceptions, minute|min_|appear→minutes
```
Two-letter tokens (`ps`, `pm`, `cs`, `yc`, `rc`) only match a whole `_`-separated token, not a substring. A key ending in `_gk`, `_def`, `_mid` or `_fwd` sets `positions` to that one position. Unmatched keys get `"fpl_field": null`.

`score_match` semantics per rule (skip when `fpl_field` is null, or `positions` is set and excludes `position`, or the stat is absent):
- `min` set: award `points` once if `stats[field] >= min`.
- `per` set: `points * floor(stats[field] / per)`.
- otherwise: `points * stats[field]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_generate_suggests_fields(self):
    m = generate_scoring_map({"goal_def": 6, "own_goal": -2, "yc": -1, "weird_stat": 1})
    self.assertEqual(m["rules"]["goal_def"], {"fpl_field": "goals_scored", "points": 6, "positions": ["DEF"]})
    self.assertEqual(m["rules"]["own_goal"]["fpl_field"], "own_goals")
    self.assertEqual(m["rules"]["yc"]["fpl_field"], "yellow_cards")
    self.assertIsNone(m["rules"]["weird_stat"]["fpl_field"])

def test_ensure_never_overwrites(self):     # write custom file, ensure() -> False, contents unchanged
def test_load_bad_json_names_file(self)
def test_load_unknown_field_names_key(self):  # {"rules": {"g": {"fpl_field": "goalz", "points": 1}}} -> message has "g" and "goalz"
def test_score_match(self):
    rules = [ScoringRule("g", "goals_scored", 5), ScoringRule("app", "minutes", 2, min=60),
             ScoringRule("gc", "goals_conceded", -1, per=2, positions=["DEF"])]
    self.assertEqual(score_match({"goals_scored": 1, "minutes": 90, "goals_conceded": 3}, "DEF", rules), 5 + 2 - 1)
    self.assertEqual(score_match({"goals_scored": 1, "minutes": 59, "goals_conceded": 3}, "MID", rules), 5)
def test_coverage_splits_keys(self)
def test_probe_never_raises(self):          # fake get raising FetchError -> 2 lines, no exception
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_scoring_map -v`
- [ ] **Step 3: Implement.** Write the generated file with `indent=2` so it's easy to hand-edit.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: editable scoring map and coverage check`

---

### Task 7: Game week and pickup mode

**Files:**
- Create: `sleeperfpl/gameweek.py`, `tests/test_gameweek.py`

**Interfaces:**
- Produces:
  - `@dataclass GameweekInfo: gw: int | None; mode: str  # "instant" | "waiver"; first_kickoff: datetime | None`
  - `target_gameweek(events: list[dict], fixtures: list[dict], now: datetime) -> GameweekInfo`
  - `team_fixtures(fixtures: list[dict], teams: dict[int, str], gw: int) -> dict[str, list[Fixture]]` maps each FPL team short_name to its fixtures in `gw`, with `difficulty` from that team's side (`team_h_difficulty` when home). A team with no fixture (blank game week) is simply missing from the dict.

Logic (first kickoff = earliest non-null `kickoff_time` among that event's fixtures):
```
in_progress = an event whose first kickoff <= now and finished is false
if in_progress: gw = next event id after it; mode = "waiver"
else:           gw = earliest unfinished event whose first kickoff > now; mode = "instant"
no such event (season over): gw = None
```

- [ ] **Step 1: Write the failing tests**

```python
def test_thursday_before_kickoff_is_instant(self):   # gw9 first kickoff Sat, now Thu -> gw 9, "instant"
def test_after_first_kickoff_is_waiver_next_gw(self): # now Sat after kickoff, gw9 unfinished -> gw 10, "waiver"
def test_season_over(self):                           # all finished -> gw None
def test_team_fixtures_double_and_blank(self):        # ARS 2 fixtures, CHE none -> len 2, "CHE" not in dict; away difficulty used for away side
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_gameweek -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: game week targeting and pickup mode`

---

### Task 8: Projection

**Files:**
- Create: `sleeperfpl/projection.py`, `tests/test_projection.py`

**Interfaces:**
- Produces: `@dataclass Projection: avg: float | None; n: int; proj: float` and
  `project(history: list[float], difficulties: list[int], ruled_out: bool, multipliers: dict[int, float], window: int = 5) -> Projection`.
  This is the one swappable piece: `report.py` calls only this function for PROJ.
- Rules: `avg` = mean of `history[:window]` (history already holds only minutes > 0 matches, most recent first), or `None` if empty. `n = len(history[:window])`. `proj = 0.0` when `ruled_out`, `avg is None`, or `difficulties` is empty (blank GW). Otherwise `proj = sum(avg * multipliers[d] for d in difficulties)`, so a double game week sums.

- [ ] **Step 1: Write the failing tests**

```python
M = {1: 1.2, 2: 1.1, 3: 1.0, 4: 0.9, 5: 0.8}
def test_basic(self):
    p = project([10, 8, 6, 4, 2, 100], [2], False, M)
    self.assertEqual((p.avg, p.n), (6.0, 5)); self.assertAlmostEqual(p.proj, 6.6)
def test_zero_appearances(self):  # project([], [3], False, M) -> avg None, n 0, proj 0.0
def test_fewer_than_window(self): # [4, 2] -> avg 3.0, n 2
def test_blank_gw(self):          # difficulties [] -> proj 0.0, avg still computed
def test_double_gw(self):         # [5]*5, [1, 5] -> proj 5*1.2 + 5*0.8 = 10.0
def test_ruled_out(self):         # proj 0.0
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_projection -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: v1 projection`

---

### Task 9: Best legal XI

**Files:**
- Create: `sleeperfpl/lineup.py`, `tests/test_lineup.py`

**Interfaces:**
- Consumes: `PlayerCtx` (Task 1), `ConfigError`.
- Produces:
  - `NON_STARTING = {"BN", "IR", "TAXI"}`
  - `starting_slots(roster_positions: list[str], eligibility: dict[str, list[str]]) -> list[str]` drops `NON_STARTING` and raises `ConfigError` naming any remaining slot not in `eligibility` (with a hint to add it to `slot_eligibility` in config.json).
  - `@dataclass Lineup: starters: list[tuple[str, PlayerCtx | None]]; bench: list[PlayerCtx]; total: float; empty_slots: list[str]`
  - `best_xi(players: list[PlayerCtx], slots: list[str], eligibility: dict[str, list[str]]) -> Lineup`. Excludes `in_reserve` players. Player P can fill slot S when `set(P.positions) & set(eligibility[S])`. Maximizes the sum of `proj`. Unmatched players (`fpl_id is None`) take part with `proj` 0, so the lineup stays legal. A slot no one can fill is `None` and listed in `empty_slots`. Ties are broken by higher `avg`, then name, so the output is deterministic.
- Algorithm: an exact maximum-weight assignment (Hungarian algorithm, or DFS over slots ordered by fewest eligible players with branch-and-bound on the remaining best-possible sum). Rosters are ≤ 30 players and ≤ 15 slots. Greedy isn't acceptable; see the multi-position test.

- [ ] **Step 1: Write the failing tests**

```python
def test_unknown_slot_raises(self):
    with self.assertRaisesRegex(ConfigError, "SUPER_FLEX"):
        starting_slots(["GK", "SUPER_FLEX", "BN"], {"GK": ["GK"]})
def test_multi_position_beats_greedy(self):
    # slots MID, FWD; A(MID/FWD, 10), B(MID, 9), C(FWD, 1) -> A in FWD, B in MID, total 19
def test_reserve_excluded(self)
def test_unmatched_fills_slot_legally(self):  # only GK is unmatched -> starts with proj 0, empty_slots == []
def test_empty_slot_reported(self):           # no GK on roster -> empty_slots == ["GK"]
def test_bench_is_rest_sorted_by_proj(self)
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_lineup -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: best legal XI`

---

### Task 10: Swap tips and free-agent ranking

**Files:**
- Modify: `sleeperfpl/lineup.py`
- Test: `tests/test_lineup.py`

**Interfaces:**
- Consumes: `best_xi`, `PlayerCtx`.
- Produces:
  - `@dataclass Swap: drop: PlayerCtx; add: PlayerCtx; gain: float; kind: str  # "xi" | "bench"`
  - `swap_tips(roster: list[PlayerCtx], free_agents: list[PlayerCtx], slots, eligibility, min_gain: float, max_swaps: int) -> list[Swap]`
  - `rank_free_agents(free_agents: list[PlayerCtx], per_position: int) -> dict[str, list[PlayerCtx]]` groups by `positions[0]` and sorts by `proj` desc, then `avg` desc (None last), then name. It takes the top `per_position` from each group, excluding `flag == "out"` and unmatched players.

Swap algorithm:
```
base = best_xi(roster).total
drops = roster players that are matched (fpl_id not None) and not in_reserve
adds  = matched free agents not flagged "out", top 40 by proj
candidates = []
for d in drops, a in adds:
    new = best_xi(roster - d + a)
    if new.empty_slots ⊄ base.empty_slots: skip            # never make the XI illegal
    xi_gain = new.total - base
    if xi_gain >= min_gain: candidates.append(Swap(d, a, xi_gain, "xi"))
    elif d is on the bench of the base lineup and a.value() - d.value() >= min_gain:
        candidates.append(Swap(d, a, a.value() - d.value(), "bench"))
sort candidates by (kind == "xi" first, gain desc); take greedily so each drop and each add is used once, up to max_swaps
```

- [ ] **Step 1: Write the failing tests**

```python
def test_xi_upgrade_suggested(self):        # starter MID proj 2, FA MID proj 6, min_gain 1 -> Swap kind "xi", gain 4
def test_below_threshold_not_suggested(self):  # gain 0.5 with min_gain 1 -> []
def test_cross_position_swap_keeps_legal_xi(self):
    # dropping the only GK for a MID would empty the GK slot -> never suggested
def test_bench_upgrade(self):               # bench FWD avg 1 proj 1 vs FA FWD avg 4 proj 4, XI unchanged -> kind "bench", gain 3
def test_unmatched_never_dropped(self)
def test_each_player_used_once_and_max_swaps(self)
def test_rank_free_agents_top_n_per_position(self):  # 12 MIDs, per_position 10 -> 10, sorted; "out" excluded
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_lineup -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: smart swap tips and free-agent ranking`

---

### Task 11: Report assembly and web page

**Files:**
- Create: `sleeperfpl/report.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: everything above. `latest`/`latest_fetched_at` (T2), `load_overrides`/`match_players` (T5), `load_scoring_map`/`score_match`/`coverage` (T6), `target_gameweek`/`team_fixtures` (T7), `project` (T8), `starting_slots`/`best_xi`/`swap_tips`/`rank_free_agents` (T9–10), `rostered_ids` from `sleeper.py`.
- Produces:
  - `class BuildError(Exception)`
  - `build_report(conn, cfg: Config, now: datetime) -> dict`
  - `render_html(report: dict) -> str`
  - `write_outputs(report: dict, output_dir: Path) -> None` writes `report.json` and `index.html`, each through a temp file + `os.replace`.

`build_report` steps:
1. Raise `BuildError("no data yet; run fetch-daily and fetch-rosters first")` if latest league, players, rosters, or FPL elements are empty.
2. Owner roster: the league user whose `user_id` or `display_name` equals `cfg.owner`, then the roster with that `owner_id`. Not found → `BuildError` listing the league's display names.
3. Match players. Flags: FPL status in `{"i","s","u","n"}`, `chance_next == 0`, or Sleeper `injury_status` equal to `"Out"` gives `"out"`, which counts as ruled out. FPL status `"d"` gives `"doubtful"`. Unmatched gives `"no data"`.
4. History: `fpl_history` rows for the player with `minutes > 0`, ordered by `kickoff_time` desc, each scored with `score_match` using the player's FPL position.
5. `project(...)` with that team's `team_fixtures` difficulties for the target GW.
6. Free agents = latest Sleeper players not in `rostered_ids(rosters)`. Trending adds come from the latest `sleeper_trending`.
7. Lineup, swaps, ranked free agents.

Report shape (JSON-serializable; the later Claude review reads this):
```json
{"generated_at": "...", "gameweek": 9, "mode": "instant", "first_kickoff": "...",
 "sources": {"sleeper_rosters": "<fetched_at>", "sleeper_players": "...", "fpl_bootstrap": "...", "fpl_fixtures": "...", "fpl_history_gw": "8"},
 "coverage": {"covered": [...], "missing": [...]},
 "xi": {"total": 68.4, "starters": [{"slot": "GK", "player": {<player>}}], "bench": [{<player>}], "empty_slots": []},
 "swaps": [{"drop": {<player>}, "add": {<player>}, "gain": 1.9, "kind": "xi"}],
 "free_agents": {"MID": [{<player>}]},
 "unmatched_rostered": [{<player>}], "unmatched_count": 37}
```
`<player>` = `{"sleeper_id","name","positions","club","flag","avg","n","proj","trending_adds","fixtures":[{"opponent","home","difficulty","kickoff"}]}` with `avg`/`proj` rounded to 1 decimal.

`render_html`: a single self-contained page (inline CSS, plus a tiny inline script for click-to-sort columns). It follows mockup B:
- header: GW, projected XI total, "data as of" times per source
- an approximate-scoring banner when `coverage.missing` isn't empty, listing the keys
- Best XI table (Player + pos/club, vs, Avg5 (n), Proj, flag)
- swap callouts: "Drop X, add Y: +1.9 projected". Prefix them "Waiver claim:" when `mode == "waiver"`. Bench swaps say "bench upgrade".
- free agents grouped by position, with the Trend column
- the Unmatched section
- Difficulty colors: 1–2 green `#2e9e5b`, 3 grey `#888`, 4–5 red `#c0392b`. Opponent upper-case = home, lower-case = away (mockup convention, explained in a legend line).
- Use `<meta name="viewport" content="width=device-width, initial-scale=1">`, make tables scroll horizontally on narrow screens, and escape every string with `html.escape`.

- [ ] **Step 1: Write the failing tests** using an in-memory DB filled by a helper `seed(conn)` in the test file. It inserts one snapshot of each table: 2 teams, one upcoming GW with fixtures, an owner roster of 3 matched players + 1 unmatched, 2 free agents, history rows, and a trending row.

```python
def test_no_data_raises(self):          # empty DB -> BuildError "run fetch-daily"
def test_owner_not_found_lists_names(self)
def test_report_shape(self):
    r = build_report(conn, cfg, NOW)
    self.assertEqual(r["gameweek"], 9); self.assertEqual(r["mode"], "instant")
    self.assertEqual(len(r["unmatched_rostered"]), 1)
    self.assertEqual(r["unmatched_rostered"][0]["flag"], "no data")
    json.dumps(r)                        # serializable
def test_uses_latest_snapshot_after_failed_fetch(self):
    # add an older rosters snapshot with a different player -> report uses the newer one
def test_html_escapes_and_labels_waiver(self):
    # player name "<b>X</b>" -> escaped in output; mode "waiver" -> "Waiver claim:" present
def test_approximate_banner(self):       # coverage.missing non-empty -> banner text present
def test_write_outputs_creates_both_files(self)
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_report -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat: build report.json and index.html`

---

### Task 12: CLI, live smoke run, and deployment files

**Files:**
- Create: `sleeperfpl/__main__.py`, `deploy/crontab.example`, `deploy/sleeperfpl-web.service`, `README.md`

**Interfaces:**
- Consumes: all fetchers, `ensure_scoring_map`, `load_scoring_map`, `coverage`, `probe_sleeper_stats`, `build_report`, `write_outputs`.
- Produces: `python3 -m sleeperfpl [--config PATH] <command>`
  - `fetch-daily`: `fetch_league` → `fetch_players` → `fetch_bootstrap` → `fetch_fixtures`, then `ensure_scoring_map` from the league's `scoring_settings` (prints a notice when it creates the file).
  - `fetch-rosters`: `fetch_rosters` → `fetch_trending`.
  - `fetch-history [--force]`
  - `coverage`: prints covered/missing scoring keys, then each `probe_sleeper_stats` line for the current season and GW.
  - `build`: `build_report` → `write_outputs`, then prints the output path.
  - Exit code 1 with a one-line message on `ConfigError`, `BuildError`, or `FetchError`. No tracebacks for those.

- [ ] **Step 1: Run the full unit suite.** `python3 -m unittest discover -s tests -v`. Expected: all OK.

- [ ] **Step 2: Implement `__main__.py`.**

- [ ] **Step 3: Live smoke run** (needs the owner's league ID and Sleeper display name in `config.json`; ask the owner if not provided). Run in order:

```bash
cp config.example.json config.json   # owner fills league_id + owner
python3 -m sleeperfpl fetch-daily
python3 -m sleeperfpl fetch-rosters
python3 -m sleeperfpl fetch-history --force   # ~a few minutes, throttled
python3 -m sleeperfpl coverage
python3 -m sleeperfpl build
python3 sleeper.py rosters <league_id>        # existing CLI still works
```
Expected: each fetch prints counts; `coverage` lists keys; `build` writes `site/index.html`. Show the owner the `coverage` output and the unmatched count. Confirm the real Sleeper slot names are in `slot_eligibility` (if `build` raises a `ConfigError` for a slot, add it to `config.json` with the owner's input). Open the page and check it against mockup B.

- [ ] **Step 4: Write deployment files.**

`deploy/crontab.example` (times in server local time; `flock` prevents overlap; `cd` to repo path placeholder `/opt/sleeper_fpl`):
```
0 6 * * *       fetch-daily   && build
30 3 * * *      fetch-history && build      # no-op unless a new GW finished
0 */4 * * *     fetch-rosters && build
0 12-23 * * 4   fetch-rosters && build      # hourly on Thursdays
```
Each line is written in full as `cd /opt/sleeper_fpl && flock -n /tmp/sleeperfpl.lock sh -c 'python3 -m sleeperfpl <a> && python3 -m sleeperfpl build' >> data/cron.log 2>&1`.

`deploy/sleeperfpl-web.service`: a systemd unit running `python3 -m http.server 8080 --directory /opt/sleeper_fpl/site --bind 0.0.0.0`, `Restart=always`. *(Plan's call: simplest server that meets "home network only". The README notes to keep port 8080 closed on the router.)*

`README.md`: what it does, setup (copy config, find league ID/owner, first runs), editing `scoring_map.json` and `overrides.json`, commands, deploying to the server (copy repo, crontab, systemd), and a "Later additions" pointer to the spec.

- [ ] **Step 5: Commit** `feat: CLI, deployment files, README`

---

## Self-review notes

- Spec coverage: XI/swaps/pickup mode/free agents → T9–11; league scoring + editable JSON → T6; coverage check + Sleeper stats probe → T6/T12; cron + server → T12; append-only snapshots → T2–4; ID map + overrides → T5; swappable projection → T8; report.json for future Claude review → T11; tunables in config → T1; `sleeper.py` untouched → T12 step 3.
- Left open on purpose: exact Sleeper soccer scoring keys and slot names. They can only be learned from the owner's live league in T12 step 3, and both are handled through editable config, not code.
