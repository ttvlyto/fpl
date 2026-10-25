# Claude Second Opinion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once per game week, ask Claude (Opus 5.5, via the Anthropic API) for its own XI, drops/adds and matchup notes from raw data, store it, show it on the page next to the tool's picks, and grade it on the report card.

**Architecture:** A new `fetch-results` command stores past Premier League results from football-data.co.uk; `headtohead.py` turns them (plus this season's finished FPL fixtures) into head-to-head and form figures. `second_opinion.py` builds a JSON packet from the same player context `build_report` assembles (minus every tool projection and pick), calls the API through an injectable `call` function, validates the answer and appends it to `second_opinion_log`. `build` only reads that table; it never calls the API.

**Tech Stack:** Python 3.13, stdlib + `anthropic` (first third-party package, imported lazily), SQLite, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-25-claude-second-opinion-design.md`

## Global Constraints

- Model `claude-opus-5-5`, `thinking={"type": "adaptive"}`, `output_config.effort` from config (default `"high"`), streamed, `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, structured output via `output_config.format` (`json_schema`).
- `import anthropic` only inside `second_opinion._default_call`; every other command and every test runs without the package.
- The API key comes only from the `ANTHROPIC_API_KEY` environment variable; never read from or written to `config.json`.
- Tests never touch the network or the real API (inject `get` / `call` fakes, like `fetch_markets` does).
- The packet must not contain `proj`, `proj_v1`, `proj_v1m`, `proj_v2`, `proj_v2m`, `avg`, `p_start`, `p_app`, `p60`, `e_min`, `ep_next`, the tool's XI, swaps or free-agent ranking.
- `build` must produce a normal page when the table is empty, the feature is disabled, or the package is missing.
- One run per game week: due only when a target game week exists, its first kickoff is within `lead_hours` (default 48) and no row exists for that `(season, gw)`; `--force` overrides.
- Run tests with `python3 -m unittest` (no pytest on this machine). Match the existing code style: short docstrings, no type hints in signatures unless the file already uses them.

## Review Focus

1. **Claude's answer is shaped right but names the wrong things:** an unknown id, a slot name not in the league, a duplicate player, or a reserve player in the XI. Expected: validation lists every problem, there's one retry with that list, and nothing crashes. Tests: Task 5.
2. **The API answer isn't usable** (`stop_reason` is `"refusal"` or `"max_tokens"`, or the text isn't valid JSON). Expected: it counts as a failed attempt with a clear `error:` message and saves nothing. Tests: Task 6.
3. **The roster can't fill every slot,** so the tool's `best_xi` already leaves `empty_slots`. Expected: Claude's XI may leave exactly as many slots empty as `best_xi` does on the post-swap roster, and no more. Tests: Task 5.
4. **football-data.co.uk CSV quirks:** a UTF-8 BOM or latin-1 bytes, blank trailing rows, dates as `dd/mm/yy` or `dd/mm/yyyy`. Expected: rows are parsed and blank rows skipped. Tests: Task 1.
5. **A stored opinion names players missing from the current Sleeper player map, or players since picked up by another team.** Expected: the page shows the id instead of a name, and the change appears in "roster changes since". It never raises. Tests: Task 7.

---

### Task 1: Store historical results (`fetch-results`)

**Files:**
- Create: `sleeperfpl/fetch_results.py`
- Modify: `sleeperfpl/db.py` (`OTHER_SCHEMA`, `TABLES`), `sleeperfpl/matching.py:37-53` (`load_overrides`), `sleeperfpl/__main__.py` (command)
- Test: `tests/test_fetch_results.py`, `tests/test_matching.py`

**Interfaces:**
- Produces:
  - Table `match_results (season TEXT, date TEXT, home TEXT, away TEXT, home_goals INTEGER, away_goals INTEGER, PRIMARY KEY (season, date, home, away))`. `season` is the start year (`"2024"` for 2024-25), `date` is ISO `YYYY-MM-DD`, team names are FPL `fpl_teams.name` after aliasing.
  - `parse_results(text: str, season: str, aliases: dict[str, str]) -> list[tuple]`, giving rows in the table's column order.
  - `fetch_results(conn, from_season: int, last_season: int, overrides: dict, *, force=False, get_text=get_text) -> int`, returning the number of seasons fetched. A season that is already stored is skipped unless `force`, tracked by `meta` key `results_fetched_<season>`.
  - `sleeperfpl/http.py`: `get_text(url, *, retries=3, timeout=120) -> str`. It uses the same retry rules as `get_json`, decodes UTF-8 with `errors="replace"` after stripping a BOM, and falls back to latin-1 on a decode error.
  - `load_overrides` returns an extra `"results_teams": {str: str}`, which is the football-data name mapped to the FPL name.
  - `RESULTS_ALIASES` in `fetch_results.py`: at least `{"Man United": "Man Utd", "Tottenham": "Spurs", "Nott'm Forest": "Nott'm Forest", "Sheffield United": "Sheffield Utd"}`. Check every current club against a real CSV during Task 9's live run.
  - CLI `fetch-results [--from SEASON] [--force]`. `--from` defaults to `2010`. The last season is the league season minus 1 (from `_league(conn)["season"]`). It prints `results: <n> season(s) fetched`.

- [ ] **Step 1: Write failing tests** in `tests/test_fetch_results.py`:
  - `test_parse_results_handles_bom_short_dates_and_blank_rows`: the CSV text is `"﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,16/08/24,Man United,Fulham,1,0\nE0,17/08/2024,Arsenal,Wolves,2,0\n,,,,,\n"`. Assert that it returns `[("2024","2024-08-16","Man Utd","Fulham",1,0), ("2024","2024-08-17","Arsenal","Wolves",2,0)]`.
  - `test_override_beats_builtin_alias`: with aliases `{"Man United": "Manchester United"}`, the first row's home team is `"Manchester United"`.
  - `test_fetch_results_skips_stored_seasons`: a fake `get_text` records the URLs it's asked for. The first call with `from_season=2023, last_season=2024` fetches `.../mmz4281/2324/E0.csv` and `.../mmz4281/2425/E0.csv` and returns 2. A second call returns 0 and makes no request. With `force=True` it returns 2 again.
  - In `tests/test_matching.py`, `load_overrides` on `{"results_teams": {"Spurs FC": "Spurs"}}` returns that mapping. A missing file returns `"results_teams": {}`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_fetch_results tests.test_matching -v`. Expected: the new tests fail with an ImportError or KeyError.
- [ ] **Step 3: Implement** the table, `get_text`, `parse_results`, `fetch_results`, the overrides key and the CLI command. Parse with `csv.DictReader`, and skip rows with no `HomeTeam` or with an unparseable score.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: fetch-results stores past Premier League results from football-data.co.uk`.

### Task 2: Head-to-head and form figures

**Files:**
- Create: `sleeperfpl/headtohead.py`
- Test: `tests/test_headtohead.py`

**Interfaces:**
- Consumes: the `match_results` table from Task 1, and the `fpl_fixtures` and `fpl_teams` snapshots.
- Produces:
  - `Result = namedtuple("Result", "date home away hg ag")`.
  - `all_results(conn) -> list[Result]` gives every stored result plus this season's finished FPL fixtures that have scores (team names from `fpl_teams.name`, date from `kickoff_time[:10]`), with duplicates removed on `(date, home, away)` and sorted by date.
  - `season_results(conn) -> list[Result]` gives only the finished FPL fixtures.
  - `head_to_head(results, a: str, b: str, n=10) -> dict` returns `{"meetings": [{"date", "home", "away", "score": "2-1"}] (newest first, at most n), "a_wins", "draws", "b_wins"` (counted over those n), plus `"a_winless_run"` and `"b_winless_run"`. A winless run is the number of most recent meetings in a row, across all stored meetings, that the side didn't win.
  - `form(results, team: str, n=5) -> list[str]` returns the newest first, each item like `"W 2-1 v Chelsea (H)"`.

- [ ] **Step 1: Write failing tests.** Results are Arsenal v Chelsea, in order: A won 2-0, then a 1-1 draw, then C won 1-0, then a 0-0 draw. The tests:
  - `test_head_to_head_counts_and_winless_runs`: `a_wins == 1`, `draws == 2`, `b_wins == 1`, `a_winless_run == 3`, `b_winless_run == 1`, and `meetings[0]["score"] == "0-0"`.
  - `test_head_to_head_limits_meetings_but_not_runs`: with `n=2`, there are 2 meetings and `a_winless_run` is still 3.
  - `test_form_from_team_perspective`: `form(results, "Chelsea", 2) == ["D 0-0 v Arsenal (A)", "W 1-0 v Arsenal (H)"]`. Put the 1-0 at Chelsea's home.
  - `test_all_results_merges_fpl_fixtures_without_duplicates`: uses `seed` from `tests/test_report.py`, plus one finished fixture with scores and the same match also stored in `match_results`. Expect exactly one entry.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_headtohead -v`. Expected: it fails with an ImportError.
- [ ] **Step 3: Implement** `sleeperfpl/headtohead.py`.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: head-to-head and form figures from stored results`.

### Task 3: Config block and a context hook on `build_report`

**Files:**
- Modify: `sleeperfpl/config.py`, `config.example.json`, `sleeperfpl/report.py:126` (`build_report`)
- Test: `tests/test_config.py`, `tests/test_report.py`

**Interfaces:**
- Produces:
  - `Config.second_opinion: dict`, defaulting to `{"enabled": False, "model": "claude-opus-5-5", "effort": "high", "lead_hours": 48}`. A partial block in `config.json` merges over the defaults, the same way `market_min_depth` does. `effort` must be one of `low|medium|high|xhigh|max` and `lead_hours` must be greater than 0; otherwise raise `ConfigError`.
  - `build_report(conn, cfg, now, *, log=True, context=None)`. When `log` is False, the three `log_*` calls and the `rebuild_label_counts` write are skipped. When `context` is a dict, it gets filled with: `"ctx"` (sid → PlayerCtx), `"sleeper_players"` (sid → row dict), `"elements"` (fpl_id → row dict), `"match"` (sid → fpl_id), `"slots"`, `"scoring"`, `"season"` (Sleeper season), `"last_finished"`, `"gw"` (GameweekInfo), `"mine"` (roster dict), `"taken"` (set of all rostered ids), `"fixtures"` (the target week's fixture rows), `"teams"` (id → short name), `"fodds"`, `"lineup"` and `"swaps"`.

- [ ] **Step 1: Write failing tests:**
  - `test_second_opinion_defaults_and_partial_merge`: `{"second_opinion": {"enabled": true}}` gives `enabled` True and `lead_hours` 48.
  - `test_second_opinion_bad_effort_rejected`: raises `ConfigError`.
  - `test_build_report_fills_context_and_skips_logs_when_asked`: after `seed`, call `build_report(conn, cfg, NOW, log=False, context=c)`. Then `c["ctx"]["s2"].name == "Bukayo Saka"`, `c["gw"].gw == 9`, and `recommendation_log` has 0 rows.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_config tests.test_report -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement** the changes. Add the `second_opinion` block, with `enabled: false`, to `config.example.json`.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: second_opinion config; build_report can skip logs and expose its context`.

### Task 4: The input packet

**Files:**
- Create: `sleeperfpl/second_opinion.py`
- Test: `tests/test_second_opinion.py`

**Interfaces:**
- Consumes: `build_report(..., log=False, context=c)` (Task 3), plus `all_results`, `season_results`, `head_to_head` and `form` (Task 2).
- Produces:
  - `FA_PER_POSITION = 15`, `RECENT_WEEKS = 5`, `H2H_MEETINGS = 10`.
  - `build_packet(conn, cfg, now) -> tuple[dict, dict]` returns `(packet, context)`. It raises `BuildError` when there's no target game week or no Sleeper stats for the season.
  - The packet keys are:
    - `gameweek`, `first_kickoff`, `adds_are` (`"free agent pickups"` if `gw.mode == "instant"`, else `"waiver claims"`), `max_swaps`;
    - `slots`, a list of slot names, plus `slot_positions` (`{slot: eligible positions}`), and `scoring` (the flattened league scoring);
    - `roster` and `free_agents`, each a list of player dicts;
    - `fixtures`, a list of `{fixture_id, home, away, kickoff, p_home, p_draw, p_away, xg_home, xg_away, difficulty_home, difficulty_away, head_to_head, home_form, away_form}`, where the odds fields are null without fresh odds.
  - A player dict is `{id, name, club, positions, ir, undroppable, fpl_status, chance_of_playing, news, sleeper_injury, trending_adds, recent: [{gw, pts, min}], season: {apps, minutes, pts}, fixtures: [{opponent, home, kickoff}]}`.
    - `recent` covers the last `RECENT_WEEKS` finished game weeks up to `last_finished`. A week with no stats row shows `pts 0, min 0`.
    - `ir` and `undroppable` appear only on roster entries.
  - Free agents are the top `FA_PER_POSITION` per primary position by sum of `recent` points, plus every free agent with `trending_adds`. Players with no FPL match (`fpl_id is None`) are left out.
  - Head-to-head and form use FPL `fpl_teams.name`; the packet shows short names.

- [ ] **Step 1: Write failing tests.** They use `seed` from `tests/test_report.py` with `stats_source="sleeper"` and `sleeper_stats` rows inserted for weeks 6–8:
  - `test_packet_has_no_tool_projections_or_picks`: `json.dumps(packet)` contains none of `"proj"`, `"p_start"`, `"e_min"`, `"ep_next"`, `"avg"`, and `packet` has no `xi` or `swaps` key.
  - `test_recent_weeks_include_zero_minute_weeks`: a player with no week-7 row has `{"gw": 7, "pts": 0, "min": 0}` in `recent`.
  - `test_trending_free_agent_always_included`: `s5`, with 40 trending adds, is in `free_agents` even when `FA_PER_POSITION` is patched to 0.
  - `test_fixture_carries_head_to_head`: with one stored Arsenal–Chelsea result, `fixtures[0]["head_to_head"]["meetings"]` has length 1.
  - `test_no_target_week_raises_build_error`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_second_opinion -v`. Expected: it fails with an ImportError.
- [ ] **Step 3: Implement** `build_packet`. Read recent weeks straight from `sleeper_stats`, since `sleeper_weeks` drops 0-minute weeks. Fill the fixture odds from `context["fodds"]` (`p_home`, `p_draw`, `p_away`, `lam_home`, `lam_away`).
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: second-opinion input packet from raw data`.

### Task 5: Answer schema and validation

**Files:**
- Modify: `sleeperfpl/second_opinion.py`
- Test: `tests/test_second_opinion.py`

**Interfaces:**
- Produces:
  - `ANSWER_SCHEMA` (JSON schema with `additionalProperties: false` at every object level):
    `{"summary": str, "xi": [{"player_id": str, "slot": str, "reason": str}], "swaps": [{"drop_id": str, "add_id": str, "reason": str}], "matchup_notes": [{"fixture": str, "note": str}]}`, with every field required.
  - `validate_answer(answer: dict, packet: dict, context: dict, eligibility: dict) -> list[str]`, which returns readable problems, or `[]` when the answer is valid. The rules are:
    - at most `max_swaps` swaps;
    - each `drop_id` is a roster player that's neither `ir` nor `undroppable`;
    - each `add_id` is in `packet["free_agents"]`;
    - no player is both added and dropped, and nothing is repeated;
    - the XI is drawn from the post-swap roster (the roster minus drops plus adds, minus IR), with no player twice;
    - each `slot` is a name in `packet["slots"]`, used no more times than it appears there;
    - each player is eligible for their slot (use `lineup._eligible` with `ctx` players, or add the players for adds);
    - the number of unfilled slots is no more than `len(best_xi(post_swap_roster, slots, eligibility).empty_slots)`.

- [ ] **Step 1: Write failing tests**, each building a small packet and context by hand:
  - `test_valid_answer_has_no_problems`;
  - `test_xi_may_start_own_add_but_not_own_drop`;
  - `test_unknown_id_and_bad_slot_reported_together`, which expects 2 problems with both ids or slot names in the text;
  - `test_too_many_swaps`;
  - `test_undroppable_and_ir_drop_rejected`;
  - `test_empty_slot_allowed_only_when_roster_cannot_fill_it`;
  - `test_duplicate_player_in_xi`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_second_opinion -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement** `ANSWER_SCHEMA` and `validate_answer`.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: validate Claude's second-opinion answer against the league's rules`.

### Task 6: The call, the due check, the log table and the command

**Files:**
- Modify: `sleeperfpl/second_opinion.py`, `sleeperfpl/db.py` (`LOG_TABLES`), `sleeperfpl/__main__.py`
- Test: `tests/test_second_opinion.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `build_packet` (Task 4), and `ANSWER_SCHEMA` and `validate_answer` (Task 5).
- Produces:
  - `LOG_TABLES["second_opinion_log"]`: `made_at TEXT, season TEXT, gw INTEGER, first_kickoff TEXT, model TEXT, xi_json TEXT, swaps_json TEXT, notes_json TEXT, summary TEXT, input_tokens INTEGER, output_tokens INTEGER`. `xi_json` holds `[{player_id, slot, reason}]`, `swaps_json` holds `[{drop_id, add_id, reason}]`, and `notes_json` holds `[{fixture, note}]`.
  - `Reply = namedtuple("Reply", "text stop_reason input_tokens output_tokens")`.
  - `_default_call(request: dict) -> Reply` does a lazy `import anthropic`. If that import fails, it raises `BuildError("the anthropic package isn't installed: pip install -r requirements.txt")`. If `ANTHROPIC_API_KEY` isn't set, it raises `BuildError("ANTHROPIC_API_KEY isn't set")`. Otherwise it runs `with anthropic.Anthropic().beta.messages.stream(**request) as s: m = s.get_final_message()` and joins the `text` blocks. It turns `anthropic.APIError` into `BuildError`.
  - `make_request(packet, cfg, problems=None, previous=None) -> dict` returns `model`, `max_tokens=64000`, `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, `thinking={"type": "adaptive"}`, `output_config={"effort": ..., "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}}`, `system=SYSTEM_PROMPT` and `messages`.
    - The first message is the user turn with the packet as JSON.
    - On a retry, `messages` also holds the previous answer as the assistant turn, then a user turn listing `problems` and asking for a corrected full answer.
  - `SYSTEM_PROMPT` must say to:
    - pick the XI and up to `max_swaps` swaps that score the most league points this game week, using only the packet;
    - apply the swaps first, so the XI is picked from the roster after them;
    - judge on minutes, form, fixtures, odds and news, not club reputation or big names;
    - cite only numbers that appear in the packet;
    - write one sentence per reason;
    - write matchup notes only for fixtures involving the roster or the suggested adds.
  - `is_due(conn, season, gw, first_kickoff, now, lead_hours) -> bool`.
  - `run_second_opinion(conn, cfg, now, *, force=False, call=None, refresh=None) -> str` returns a one-line status. Its steps are:
    1. If `cfg.second_opinion["enabled"]` is false, return `"second opinion: disabled in config.json"`.
    2. Read the target game week cheaply: events and fixtures through `target_gameweek`. If no run is due and not `force`, return a `"... not due"` line. No fetch and no API call happen in that case.
    3. Call `refresh()` if one was given; the CLI passes rosters, trending and bootstrap fetches.
    4. Build the packet, then call once and validate.
    5. If `stop_reason` isn't `end_turn` or the text isn't JSON, raise `BuildError`.
    6. If validation finds problems, call once more with them. If the answer is still invalid, raise `BuildError` listing the problems, and insert nothing.
    7. Otherwise insert the row and return `"second opinion: GW<n> saved (<in>/<out> tokens)"`.
  - CLI `second-opinion [--force]` runs `run_second_opinion` with `refresh` doing `fetch_rosters`, `fetch_trending` and `fetch_bootstrap`, each wrapped like the warn-and-continue steps in `cmd_matchday`. It prints the status. A `BuildError` exits 1 through the existing handler.

- [ ] **Step 1: Write failing tests**, all with a fake `call` that records its requests:
  - `test_not_due_makes_no_call`: first kickoff 72 hours away gives 0 calls and 0 rows.
  - `test_due_saves_one_row_and_second_run_skips`: 30 hours away gives 1 call and 1 row. Running again gives 1 call in total. With `force=True` there are 2 rows.
  - `test_request_shape`: `model == "claude-opus-5-5"`, `fallbacks == "default"`, `"server-side-fallback-2026-07-01"` in `betas`, `output_config["effort"] == "high"`, `output_config["format"]["schema"] is ANSWER_SCHEMA`, and `"temperature"` not in the request.
  - `test_invalid_then_valid_retries_once`: the second request has 3 messages and its last user turn names the problem; 1 row.
  - `test_invalid_twice_raises_and_saves_nothing`.
  - `test_refusal_or_bad_json_raises`, covering `stop_reason="refusal"`, `stop_reason="max_tokens"` and non-JSON text.
  - `test_disabled_does_nothing`.
  - `test_missing_package_message`: patch `builtins.__import__` to raise for `anthropic`; `_default_call` raises `BuildError` mentioning `requirements.txt`.
  - In `tests/test_main.py`, `second-opinion` with `enabled` false prints `disabled` and exits 0.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_second_opinion tests.test_main -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement** the call, the due check, the log table and the command.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: second-opinion command asks Claude once per game week and stores the answer`.

### Task 7: Show it on the page and in `report.json`

**Files:**
- Modify: `sleeperfpl/report.py` (`build_report` return value, new `_second_opinion_block` and `_second_opinion_card`, `render_html`)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `second_opinion_log` (Task 6).
- Produces:
  - `report["second_opinion"]`, which is `None` when there's no row for the target `(season, gw)`. Otherwise it holds:
    - `made_at`, `model`, `summary`;
    - `xi: [{slot, sleeper_id, name, club, reason, in_tool_xi}]`;
    - `swaps: [{drop: {sleeper_id, name}, add: {sleeper_id, name}, reason, in_tool}]`;
    - `notes: [{fixture, note}]`;
    - `agree_xi` (an int), `xi_size` (an int), and `changed` (a list of names).
  - `changed` lists the XI players that are neither on the owner's roster now nor among Claude's adds, plus Claude's adds that are now on another team's roster. Names come from Sleeper players, falling back to the id.
  - `_second_opinion_card(r) -> str` renders a card headed "Claude's second opinion". It shows:
    - "Made <made_at> · Agrees on <agree_xi> of <xi_size> starters";
    - the summary;
    - the XI table (Slot, Player, Reason, plus a "Claude only" tag where `in_tool_xi` is false);
    - the swaps ("Drop X, add Y" with the reason, tagged "tool agrees" when `in_tool`);
    - the notes;
    - a banner line "Roster changes since this opinion: …" when `changed` is non-empty.
  - The card goes right after the swap-tips section. All text passes through `_e`.

- [ ] **Step 1: Write failing tests:**
  - `test_no_second_opinion_is_none_and_page_renders`;
  - `test_second_opinion_rendered_with_agreement`: insert a row for GW9 whose XI matches the tool on 2 of 3 starters. Expect `agree_xi == 2`, `"Agrees on 2 of 3 starters"` in the HTML, and the reason text in the HTML;
  - `test_changed_lists_players_now_elsewhere`: an XI id on rival roster 2 shows up in `changed`, and an unknown id shows up as the id;
  - `test_own_add_now_on_my_roster_is_not_a_change`;
  - `test_reason_text_is_escaped`: `<script>` comes out escaped.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement** the block, the card and the `render_html` change, and add the `second_opinion` key to the `report.json` key list in `docs/README.md` "Outputs".
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: show Claude's second opinion on the page`.

### Task 8: Grade it on the report card

**Files:**
- Modify: `sleeperfpl/report_card.py:134` (`score_decisions`), `sleeperfpl/report.py:598` (`_report_card`)
- Test: `tests/test_report_card.py`, `tests/test_report.py`

**Interfaces:**
- Produces:
  - Each `score_decisions` row gains:
    - `claude_pts`, the real points of the XI in the newest `second_opinion_log` row for that week with `made_at < first_kickoff`, or `None`;
    - `claude_swaps`, which has the same shape as `swaps`.
  - The decisions table in `_report_card` gets a "Claude (followed in full)" column after "tool XI", shown only when some row has a `claude_pts`. Weeks with no opinion show "–".

- [ ] **Step 1: Write failing tests:**
  - `test_claude_pts_scored_from_last_pre_kickoff_opinion`: there are two rows, and the one made after kickoff is ignored.
  - `test_claude_pts_none_without_opinion`.
  - In `tests/test_report.py`, the column header appears only when there's data.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report_card tests.test_report -v`. Expected: the new tests fail.
- [ ] **Step 3: Implement** the changes.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 5: Commit** with `feat: grade Claude's second opinion on the report card`.

### Task 9: Dependency, cron, docs, live run

**Files:**
- Create: `requirements.txt` (containing `anthropic>=1.0`; confirm the installed major version during the live run)
- Modify: `deploy/crontab.example`, `docs/README.md`

- [ ] **Step 1: Cron.** In `deploy/crontab.example`:
  - add `fetch-results` to the 03:30 line;
  - add an hourly line at minute 50 (so it never collides with the :45 and :00/:30 runs): `cd /opt/sleeper_fpl && flock -n /tmp/sleeperfpl.lock sh -c 'python3 -m sleeperfpl second-opinion ; python3 -m sleeperfpl build' >> data/cron.log 2>&1`;
  - add a comment line saying to put `ANTHROPIC_API_KEY=...` at the top of the crontab.
- [ ] **Step 2: README.** Update:
  - "What the page shows": the Claude card;
  - Commands: `fetch-results` and `second-opinion`;
  - Configuration: the `second_opinion` block;
  - `overrides.json`: `results_teams`;
  - Running on a server: `pip install -r requirements.txt`, the key in the crontab, and setting a spend limit at platform.claude.com;
  - Data sources: football-data.co.uk and the Anthropic API;
  - Database: `match_results` and `second_opinion_log`;
  - Code layout: the 3 new modules;
  - Known limitations: Claude can be wrong and its reasons aren't checked; head-to-head is league-only.
- [ ] **Step 3: Run** `python3 -m unittest`. Expected: all tests pass.
- [ ] **Step 4: Commit** with `docs: second opinion setup, cron and limitations`.
- [ ] **Step 5: Live run on dev (owner present, uses their key, about $0.30).**
  1. `pip install -r requirements.txt`.
  2. `python3 -m sleeperfpl fetch-results`, then check that every current FPL club name appears in `match_results` and add missing aliases.
  3. Set `"second_opinion": {"enabled": true}` in dev `config.json`.
  4. Run `ANTHROPIC_API_KEY=... python3 -m sleeperfpl second-opinion --force`, then `build`.
  5. Open `site/index.html` and check the card.
  6. Report the token counts and cost to the owner. Don't enable it on the server; the owner does that.
