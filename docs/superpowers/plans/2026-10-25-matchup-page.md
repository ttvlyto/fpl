# Matchup Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put this week's head-to-head matchup at the top of the page: the
opponent from a schedule, both lineups (projected strongest XI, or set
lineups and points so far during a live week) on a pitch or as a list,
fixtures with badges, local kickoff times and starter counts, and a
restyled page.

**Architecture:**
- **Pure functions** go in a new `sleeperfpl/matchup.py`: the schedule
  lookup, opponent matching, pitch rows, formation, and live points.
- **`build_report`** also projects the opponent's roster into a separate
  `opp_ctx` that never mixes with the owner's `ctx`. It adds a `matchup`
  block, a `badges` map and per-fixture starter counts to `report.json`.
- **`render_html`** gains a matchup card, a Starting XIs card (pitch/list),
  and a fixtures card in place of the Matches card. The CSS is restyled
  with dark mode kept.

**Tech Stack:** Python 3 standard library, SQLite, a static HTML page with
small inline JS. Tests run with `python3 -m unittest` (pytest is not
installed).

**Spec:** `docs/superpowers/specs/2026-10-25-matchup-page-design.md`
(approved). Mockup: https://claude.ai/artifact/VUC4AWvBsTjf4pGva4zGf2.

## Global Constraints

- The schedule is a config block: `{"start_gw": 9, "end_gw": 31, "opponents": ["Machine Gun Skelly", "2nd again ole ole ole", "firmala firmala", "briantrovato", "HORMIGOOOOOL"]}`.
  - GW `g` with `start_gw <= g <= end_gw` faces `opponents[(g - start_gw) % len(opponents)]`.
  - Outside that range there's no opponent and no card.
- **Name matching:** first against the Sleeper team name (`metadata.team_name`), then the username (`display_name`). It ignores case and leading or trailing spaces.
  - No match gives a notice.
  - A match to the owner gives a notice.
- **Opponent's projected XI:** `best_xi` over their roster minus their reserve, with the same projections as the owner's.
- **No leaks:** opponent players never appear in:
  - free agents, `swap_tips`, `rank_free_agents`;
  - the second-opinion `context["ctx"]`;
  - `log_projections`, the report card, or live-card goal-odds logging.
- **Post-swap total:** `best_xi` on the roster after every suggested swap, never a sum of gains.
- **Badge URL:** `https://resources.premierleague.com/premierleague/badges/70/t<code>.png`. A circle with the club code is the fallback.
- **Kickoff times:** shown in the viewer's time zone by JS. The UTC text is the no-JS fallback.
- **Colours:** you `#1d4ed8`, them `#c2410c` (dark mode: `#7aa2ff`, `#ff9a5c`). Card radius 14 px, card headings 17 px.
- Touch targets are at least 44 px.
- No sideways page scroll at 390 px.
- Dark mode is kept.
- Nothing the page shows today is dropped.
- Tests never touch the network.

## Review Focus

1. **Opponent leaks.** A schedule is set, and the opponent owns a player who would otherwise top the free agents. Expected: free agents, swaps, the second-opinion context and the logs are identical to a build without a schedule. Test in Task 3.
2. **Old databases.** No `team_name`, `wins` or `code` values yet (NULL). Expected: the page builds, the record shows "–", the badge falls back to the code circle, and the name match falls back to the username. Tests in Tasks 1 and 3.
3. **Odd Sleeper starters.** `starters_json` holds `"0"` (an empty slot) or an id missing from Sleeper's player map. Expected: an empty slot on the pitch and list, or a "Sleeper player <id>" name, with no crash. Test in Task 4.
4. **Degenerate opponent rosters.** The opponent's roster is empty or all on IR. Expected: an XI of empty slots with total 0.0 and no crash. Test in Task 3.
5. **Live week with no scheduled opponent** (GW8 now, since the schedule starts at GW9). Expected: the card falls back to the target week in projected mode, not blank. Test in Task 4.

## Rulings made while planning (spec gaps)

- **Live mode needs a scheduled opponent.** It applies only when the week being played has one. Otherwise the card uses the target week in projected mode. This follows the owner's "i don't mind we are starting with 9".
- **What the Starting XIs card shows.** It shows the matchup's lineups.
  - **Projected mode:** the List view for "you" is today's Best XI table plus bench, so there's no separate Best XI card.
  - **Live mode:** the XIs card shows both set lineups with points so far, and today's Best XI card follows separately, titled `Best XI · GW<target>`.
  - **No matchup:** today's Best XI card, unchanged.
- **Fixture starter counts** ("You n" / "Them n") appear only when the matchup is for the same week as the fixtures card (projected mode). In live mode the live card already lists the live week's fixtures.

---

### Task 1: Store team names, records and badge codes

**Files:**
- Modify: `sleeperfpl/db.py` (SNAPSHOT_TABLES)
- Modify: `sleeperfpl/fetch_sleeper.py`: `fetch_league`, `fetch_rosters`
- Modify: `sleeperfpl/fetch_fpl.py`: the `fpl_teams` insert
- Modify: `tests/test_report.py`, `tests/test_report_card.py`: positional inserts into these 3 tables
- Test: `tests/test_fetch_sleeper.py`, `tests/test_fetch_fpl.py`, `tests/test_db.py`

**Interfaces:**
- Produces:
  - `sleeper_users` gains `team_name`.
  - `sleeper_rosters` gains `wins, losses, ties` (integers or NULL).
  - `fpl_teams` gains `code`.
  - Columns are appended at the end, so `connect` migrates old databases.

- [ ] **Step 1: Write failing tests.** Add:
  - `test_fetch_league_stores_team_names`: users `[{"user_id":"u1","display_name":"me","metadata":{"team_name":"noSHOWdegaard"}},{"user_id":"u2","display_name":"bt","metadata":None}]` give `team_name` "noSHOWdegaard" and None.
  - `test_fetch_rosters_stores_record`: a roster with `"settings":{"wins":4,"losses":3,"ties":0}` gives `(4, 3, 0)`; a roster with no `settings` gives `(None, None, None)`.
  - `test_bootstrap_stores_team_code`: a team `{"id":1,"short_name":"ARS","name":"Arsenal","code":3}` stores `code == 3`.
  - In `tests/test_db.py`, `test_old_snapshot_tables_gain_new_columns`: a database created with the old `sleeper_users (fetched_at, user_id, display_name)` gains `team_name` after `connect`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_fetch_sleeper tests.test_fetch_fpl tests.test_db`. Expected: the 4 new tests FAIL.
- [ ] **Step 3: Implement.**
  - Append the columns to SNAPSHOT_TABLES.
  - Switch the three fetcher inserts to explicit column lists (`INSERT INTO sleeper_users (fetched_at, user_id, display_name, team_name) VALUES ...`), so they are robust to future columns.
  - Change the positional test inserts into these three tables to name their columns (`seed()` in `tests/test_report.py`, and `tests/test_report_card.py`). Their values stay the same.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: OK, with every old test still passing.
- [ ] **Step 5: Commit** `feat: store Sleeper team names and records, and FPL badge codes`.

### Task 2: Schedule config and opponent lookup

**Files:**
- Create: `sleeperfpl/matchup.py`
- Modify: `sleeperfpl/config.py` (DEFAULTS `"schedule": None`, `Config.schedule: dict | None = None`, a `_schedule(raw, cfg_path)` validator)
- Modify: `config.example.json`
- Test: `tests/test_matchup.py`, `tests/test_config.py`

**Interfaces:**
- Produces, in `sleeperfpl/matchup.py`:
  - `opponent_for(schedule: dict | None, gw: int | None) -> str | None`
  - `find_opponent(name: str, users: list[dict], rosters: list[dict], my_roster_id) -> tuple[dict | None, str | None]` returns `(roster, None)` or `(None, notice)`. `users` rows have `user_id, display_name, team_name`; `rosters` rows have `roster_id, owner_id` and the decoded lists.
  - `team_label(user: dict) -> str`: the team name, else the username.
- Produces, in `Config`: `schedule` is `{"start_gw": int, "end_gw": int, "opponents": list[str]}` or None.

- [ ] **Step 1: Write failing tests** in `tests/test_matchup.py`, using `S = {"start_gw": 9, "end_gw": 31, "opponents": ["A","B","C","D","E"]}`:
  - `test_cycle`: `opponent_for(S, 9) == "A"`, `opponent_for(S, 13) == "E"`, `opponent_for(S, 14) == "A"`, `opponent_for(S, 31) == "A"`.
  - `test_outside_range_is_none`: GW 8, GW 32, gw None and schedule None all give None.
  - `test_match_team_name_ignoring_case_and_spaces`: `"  machine gun SKELLY "` finds the roster whose owner's `team_name` is "Machine Gun Skelly".
  - `test_falls_back_to_username`: "briantrovato", for a user with `team_name` None, finds their roster.
  - `test_no_match_notice`: "nobody" gives `(None, "...nobody...")`. The notice names the input and lists the league's team labels.
  - `test_owner_is_not_an_opponent`: matching the owner's own team gives `(None, notice)`.

  In `tests/test_config.py`:
  - `test_schedule_defaults_to_none`.
  - `test_schedule_end_defaults_to_38`.
  - `test_bad_schedule_rejected`, covering an empty `opponents`, `end_gw < start_gw`, and a non-integer `start_gw`. Each raises `ConfigError`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_matchup tests.test_config`. Expected: FAIL, because `sleeperfpl.matchup` is missing.
- [ ] **Step 3: Implement** the functions and the config validator. `end_gw` defaults to 38. Add the owner's schedule (Global Constraints) to `config.example.json`, and to the dev `config.json`, which isn't committed.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: OK.
- [ ] **Step 5: Commit** `feat: schedule config and opponent lookup`.

### Task 3: Project the opponent and add the projected matchup to report.json

**Files:**
- Modify: `sleeperfpl/report.py` (`build_report`, new `_matchup_block`, new `_side`)
- Modify: `sleeperfpl/matchup.py` (add `formation`, `pitch_row`, `record_text`)
- Test: `tests/test_report.py` (new class `MatchupReportTests`), `tests/test_matchup.py`

**Interfaces:**
- Consumes: `opponent_for`, `find_opponent`, `team_label` (Task 2) and the Task 1 columns.
- Produces, in `matchup.py`:
  - `pitch_row(positions: list[str]) -> int`: 0 for F/FWD, 1 for M/MID, 2 for D/DEF, 3 for GK. It uses the first listed position; anything else gives 1.
  - `formation(position_lists: list[list[str]]) -> str`: the counts of rows 2, 1 and 0 joined by "-", for example "3-5-2". GK isn't counted.
  - `record_text(roster: dict) -> str`: "4–3", or "4–3–1" when ties > 0, or "–" when wins is NULL.
- Produces in `report.json`:
  - `"matchup"`: either None, or `{"gw", "mode": "projected", "notice": str | None, "me": side | None, "them": side | None, "after_swaps": float | None}`.
    - `side` is `{"name", "username", "record", "total", "formation", "starters": [{"slot", "player": player_dict | None, "points": None, "state": None}], "bench": [player_dict]}`.
  - `"badges"`: `{short_name: code | None}`.
  - Each entry in `"matches"` gains `"mine"` and `"theirs"`: integer counts, or None when the matchup isn't for this week.

- [ ] **Step 1: Write failing tests.**

  In `tests/test_matchup.py`:
  - `test_pitch_row_and_formation`: `formation([["D"],["D"],["D"],["M"],["M","D"],["F","M"],["GK"]]) == "3-2-1"`.
  - `test_record_text`: `{"wins":4,"losses":3,"ties":0}` gives "4–3"; ties 1 gives "4–3–1"; `{"wins":None}` gives "–".

  In `MatchupReportTests`, use `seed()`. Opponent u2 ("rival", team "Rivals FC") owns s5 (Palmer) and s6 (Cucurella). Set `cfg.schedule = {"start_gw": 9, "end_gw": 31, "opponents": ["Rivals FC"]}`.
  - `test_projected_matchup_block`: `r["matchup"]["gw"] == 9`, mode "projected", them.name "Rivals FC", and them.starters' player ids ⊆ {"s5","s6"}. `them.total` equals the sum of their starters' `proj`.
  - `test_opponent_players_do_not_leak`: with the schedule, `r["free_agents"]`, `r["swaps"]` and `context["ctx"]` keys match a build without the schedule. Also, `projection_log` rows for this build contain no s5 or s6.
  - `test_opponent_ir_excluded`: s6 in u2's `reserve_json` never appears in them.starters.
  - `test_empty_opponent_roster`: u2 with an empty roster gives `them.total == 0.0`, every starter `player` None, and no error.
  - `test_after_swaps_total`: with a swap tip present (Palmer free, a weak MID on my roster), `after_swaps` equals `best_xi` over (roster − drops + adds) `.total`, rounded to 1 decimal.
  - `test_no_schedule_no_matchup`: `r["matchup"] is None`.
  - `test_unmatched_name_gives_notice`: `opponents ["Nobody"]` gives matchup with a notice, and me/them None.
  - `test_badges_and_fixture_counts`: `fpl_teams` code 3 for ARS gives `r["badges"]["ARS"] == 3`, and CHE with code NULL gives None. The fixture ARS–CHE shows `mine` as the count of my XI starters from ARS/CHE, and `theirs` likewise.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_matchup tests.test_report`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - **Read the new columns.** `_rows` already returns them. Decode `wins/losses/ties` as they are.
  - **Pick the opponent** before the player loop:
    - `playing = live_gameweek(...)` (exists).
    - The matchup week is `gw.gw` for now; Task 4 adds the live branch.
    - `opp_name = opponent_for(cfg.schedule, gw.gw)`.
    - `opp_roster, notice = find_opponent(...)`.
    - `opp_ids = set(opp_roster["players"]) if opp_roster else set()`.
  - **Player loop:**
    - Change the skip test to `if sid not in my_ids and sid not in opp_ids and (sid in taken or not p["team"] or not p["active"])`.
    - Store each built `pl` in `opp_ctx` when `sid in opp_ids`, else in `ctx`.
    - Opponent ids missing from Sleeper's map get the same "Sleeper player <id>" fallback as the owner's roster.
    - Set `pl.in_reserve` from the opponent's reserve.
    - Leave `on_roster` False on opponent players.
  - **`_matchup_block(...)`:** builds both sides from `best_xi`.
    - `after_swaps` uses `best_xi([pl for pl in roster if pl not in drops] + adds, slots, cfg.slot_eligibility).total`.
    - Fixture counts come from both XIs' clubs.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: OK.
- [ ] **Step 5: Commit** `feat: project the week's opponent and add the matchup to report.json`.

### Task 4: Live mode

**Files:**
- Modify: `sleeperfpl/report.py` (extract `_club_states`, use it in `_live_block`, add the live branch to `_matchup_block`)
- Modify: `sleeperfpl/matchup.py` (add `live_side`)
- Test: `tests/test_matchup.py`, `tests/test_report.py` (subclass `LiveReportTests` as `LiveMatchupTests`)

**Interfaces:**
- Produces:
  - `_club_states(conn, season, gw, fixtures, teams, now, club_of) -> dict[str, str]`: a state for every club with a fixture in `gw`, one of "done", "playing" or "to_play".
    - "done" is exactly today's `finished` set: over, stats fetched after the match ended, and the club trusted.
    - "playing" means started but not done.
    - "to_play" means not started.
    - `_live_block` derives `finished`, `started` and `upcoming` from it, with its behaviour unchanged.
  - `matchup.live_side(starters: list[tuple[str, object | None]], week_stats: dict[str, dict], scoring: dict, states: dict[str, str]) -> tuple[list[dict], float, int]` returns `([{"slot", "pl", "points", "state"}], total, to_play_count)`.
    - A starter's points are `sleeper_points(week_stats[sid], scoring)` when their club's state is "done" or "playing" and a stats row exists. Otherwise the points are 0.0.
    - `state` is the club's state, or "to_play" when the club is unknown.
  - The matchup in live mode is `{"gw": playing, "mode": "live", ...}`. Each side gains `"to_play"`, each starter's `"points"` and `"state"` are filled, `bench` is [], and `after_swaps` is None.

- [ ] **Step 1: Write failing tests.**

  In `tests/test_matchup.py`:
  - `test_live_side_points_and_states`: a starter with state "done" and stats `{"pos_m_g":1}` with scoring `{"pos_m_g":9}` scores 9.0; a starter with "to_play" scores 0.0 and adds 1 to `to_play`; a starter slot holding None stays None with 0.0.

  In `LiveMatchupTests`, set the schedule to start at GW9 with u2 "Rivals FC" as the opponent, and use `after = 2026-10-24T15:00Z`.
  - `test_live_week_shows_set_lineups_and_points`: mode "live", gw 9; my starters are `starters_json` ["s1","s2","s3"]; totals equal the summed live points.
  - `test_live_week_without_scheduled_opponent_falls_back`: with `start_gw` 10, `r["matchup"]["mode"] == "projected"` and `gw == r["gameweek"]`.
  - `test_odd_starter_ids`: u2 `starters_json` `["0", "s99"]` gives the slot for "0" with player None, and "s99" named "Sleeper player s99" with 0.0 points.
  - `test_live_card_unchanged`: today's `test_report_live_block_only_when_in_progress` assertions still pass, and `live["replacements"]` is unchanged.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_matchup tests.test_report`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - Extract `_club_states` from `_live_block` with no behaviour change.
  - In `build_report`, choose the matchup week: `playing` when `opponent_for(cfg.schedule, playing)` resolves; otherwise `gw.gw`.
  - Read `sleeper_stats` for that week from the database.
  - Build each side's starters from its roster's `starters` zipped with `slots`.
    - The owner's players come from `ctx`, the opponent's from `opp_ctx`.
    - Ids missing from both get a minimal `PlayerCtx` named "Sleeper player <id>".
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: OK.
- [ ] **Step 5: Commit** `feat: live score on the matchup card during the week being played`.

### Task 5: The page — matchup, Starting XIs, fixtures, badges, local times

**Files:**
- Modify: `sleeperfpl/report.py` (`render_html`, new `_matchup_card`, `_xis_card`, `_fixtures_card`, `_badge`, `PAGE_JS`; remove `_matches_card` once `_fixtures_card` covers its content)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: the `report["matchup"]`, `report["badges"]` and `matches[*].mine/theirs` from Tasks 3 and 4.
- Produces, in `report.py`:
  - `_badge(club: str, code) -> str`:
    - `<span class="badge"><img src="…t<code>.png" alt="<club>" loading="lazy" onerror="this.remove()"><b>CLUB</b></span>`.
    - With no code, the `<img>` is left out.
    - CSS layers the img over the code text, so a failed load shows the code.
  - `PAGE_JS` (appended to `SORT_JS`) does two things:
    - **XI view switch:** buttons with `data-view="pitch|list"` toggle `[data-xi-view]` blocks. The choice is stored in `localStorage["xi-view"]` inside try/catch, and Pitch is the default.
    - **Local times:** every `<time datetime="ISO">` gets local text from `toLocaleString(undefined, {weekday:"short", day:"numeric", month:"short", hour:"2-digit", minute:"2-digit"})`.

**Page order** (spec §5):
1. header lines (unchanged)
2. banners
3. live card
4. matchup card
5. Starting XIs card (or today's Best XI card when there's no matchup; in live mode, both, as in the Rulings)
6. swap tips
7. fixtures card
8. second opinion
9. free agents
10. report card
11. unmatched

**Matchup card.** As in the mockup:
- Names: "You"'s team label in blue, theirs in orange, each with its record and `@username` in small type.
- Both totals.
- A bar with widths proportional to the totals (50/50 when both are 0).
- "You lead by N" / "You trail by N" / "Level".
- Projected mode: the swap line "With all K suggested swaps your XI projects X (lead/trail by N)." It's left out when there are no swaps.
- Live mode: the heading is "GW9 · live", and each side shows "n to play".
- With a notice: the card shows only the notice.

**Pitch.**
- One `.pitch` per side, with 4 rows from `pitch_row`: F at the top, GK at the bottom.
- Each player shows a badge, surname (ellipsis at 64 px) and points (`proj`, or live points). Empty slots show a dashed circle labelled with the slot.
- A formation label sits above each pitch.

**List.**
- Projected mode, "you": today's Best XI rows and bench (reuse the existing code).
- Live mode, and "them": a table of Slot, Player (with club badge), and Proj or Pts plus state.

**Fixtures card.** Each fixture has:
- a `<time>`;
- both badges and names, with win chances;
- "You n" / "Them n" when present;
- a second muted line with draw %, expected goals, clean-sheet chances, sources + fetched time, and the stale tag (everything today's Matches table shows). No-market fixtures keep today's "no market — using FPL difficulty" text.

The projection note stays under the card.

- [ ] **Step 1: Write failing tests** in `MatchupReportTests` / `LiveMatchupTests`:
  - `test_page_matchup_card`: contains "Rivals FC", both totals, "trail by" or "lead by", and the swap line when swaps exist.
  - `test_page_xis_pitch_and_list`: contains `data-xi-view="pitch"` and `data-xi-view="list"`, `class="pitch"`, and the formation label.
  - `test_page_badges_with_fallback`: contains `badges/70/t3.png` and `<b>ARS</b>`. CHE (no code) has `<b>CHE</b>` and no CHE img.
  - `test_page_fixtures_keep_market_details`: with quotes (reuse `MarketReportTests.quotes`), the page still shows the expected-goals text and "Clean sheet". It has a `<time datetime="2026-10-24T11:30:00Z">`.
  - `test_page_notice_only`: an unmatched name shows the notice and no `class="pitch"`.
  - `test_page_live_mode`: shows "GW9 · live", "to play", and the separate `Best XI · GW` card.
  - `test_page_without_schedule_unchanged_sections`: "Best XI", "Swap tips", "Top free agents" and the report card are present, with no matchup markup.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report`. Expected: the new tests FAIL; the existing page tests still pass.
- [ ] **Step 3: Implement** the cards, `_badge` and `PAGE_JS` per the Interfaces, in the mockup's layout.
- [ ] **Step 4: Run** `python3 -m unittest`. Expected: OK.
- [ ] **Step 5: Commit** `feat: matchup, starting XIs on a pitch, fixtures with badges and local times on the page`.

### Task 6: Visual refresh, docs, dev build check

**Files:**
- Modify: `sleeperfpl/report.py` (`CSS`)
- Modify: `docs/README.md`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing test** `test_css_has_dark_mode_and_new_tokens`. `CSS` must contain `prefers-color-scheme:dark` and `--me:#1d4ed8` and `--them:#c2410c`, and the dark block must contain `--me:#7aa2ff` and `--them:#ff9a5c`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_report`. Expected: FAIL.
- [ ] **Step 3: Restyle `CSS`.** Keep every existing class name used by the renderers.
  - New tokens: `--me`, `--them`, `--pitch:#2e7d4f`, `--pitchline`.
  - Cards: radius 14px, padding 14px 16px, `h2` 17px.
  - `.badge`: 26 px circle; the img is absolute over the `<b>`.
  - `.pitch`: stripes via `repeating-linear-gradient`, rows as flex `space-evenly`, chips at 64 px width.
  - Two-pitch grid: `repeat(auto-fit, minmax(300px, 1fr))`.
  - Segmented switch: `button` with min-height 44px.
  - Dark versions of every new colour, with text at 4.5:1 or better.
  - Tables keep their `.scroll` box.
- [ ] **Step 4: Update `docs/README.md`.**
  - "What the page shows": the new card order, pitch/list, live mode, fixtures.
  - "Configuration": the `schedule` block and its rules.
  - "Outputs": the `matchup`, `badges` and `matches[*].mine/theirs` keys.
  - "Database": the new columns.
  - "Data sources": the badge image URL.
  - "Known limitations": the schedule is entered by hand because Sleeper's API has no soccer matchups; badges need the Premier League image site.
- [ ] **Step 5: Run** `python3 -m unittest`. Expected: OK.
- [ ] **Step 6: Dev build check.**
  - Run `python3 -m sleeperfpl fetch-daily` (stores team names, records and codes), then `python3 -m sleeperfpl fetch-rosters`, then `python3 -m sleeperfpl build`.
  - Expected: `site/index.html` shows the matchup for the current week, with Machine Gun Skelly from GW9.
  - Check `site/report.json` `matchup`: no notice, 11 starters per side, and badges present for all 20 clubs.
- [ ] **Step 7: Commit** `feat: restyle the page; document the matchup page`.
