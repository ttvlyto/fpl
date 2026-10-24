# Betting-Market Odds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull free Kalshi and Polymarket odds into the rankings: a match view, position-aware market projections (v2), history-vs-market flags, a match-day "live" section, and a weekly report card.

**Architecture:** New fetchers write append-only quote snapshots (one table for match markets, one for player markets). A pure pricing module fits each fixture's expected goals (λ home, λ away) from those quotes. `projection_v2` splits Sleeper weekly points into defensive/attacking/other buckets and re-prices the first two with λ. `report.py` gains a match view, flags, a `live` block and a report card. A `matchday` CLI command runs from cron every 30 minutes.

**Tech Stack:** Python 3.10+ stdlib, sqlite3, unittest (unchanged).

**Spec:** `docs/superpowers/specs/2026-10-24-market-odds-design.md` (builds on `docs/superpowers/specs/2026-10-24-fpl-rankings-design.md`)

## Global Constraints

- Standard library only. Tests: `python3 -m unittest discover -s tests`. No network calls in tests; fetchers take an injectable `get`.
- Free public endpoints only, no keys: Kalshi `https://api.elections.kalshi.com/trade-api/v2`, Polymarket `https://gamma-api.polymarket.com` (EPL series id `10188`).
- Snapshots are append-only with one `fetched_at` per run. A failed fetch writes nothing and never blocks `build`.
- Every market number shown on the page carries its fetch time. Prices older than `market_stale_hours` are marked stale and not used for projections.
- Thin markets are ignored: a quote is usable only if `ask - bid <= market_max_spread` and its depth clears `market_min_depth` (Kalshi: `yes_bid_size + yes_ask_size` in contracts; Polymarket: `liquidityNum` in USD).
- New config keys and defaults: `market_max_spread: 0.10`, `market_min_depth: {"kalshi": 50, "polymarket": 1000}`, `market_stale_hours: 24`, `matchday_window_hours: 3`, `league_avg_goals: 1.35`, `flag_cs_gap: 0.25`, `flag_goal_gap: 0.20`.
- Team codes map to FPL `short_name`. Built-in Kalshi aliases `{"BRI": "BHA", "CFC": "CHE", "LFC": "LIV"}`. Owner overrides go in `overrides.json` under `"market_teams": {"<source>:<code>": "<FPL code>"}`. Unmapped codes are listed on the page.
- v2 needs Sleeper stats (`stats_source: "sleeper"` with stats present). Otherwise every fixture uses v1 and the page says so.
- History excludes the game week in progress: Sleeper stats for weeks after the last finished FPL game week never enter averages.
- Polymarket player props are ignored (untraded placeholders). Kalshi player series used: `KXEPLGOAL` (floor 0.5 → "1+ goals"), `KXEPLSOA`.

## Review Focus

1. **A fixture with odds from only one source, or only the 1X2 market.** The fit still works from whatever usable quotes exist; with zero usable quotes the fixture falls back to v1, labelled. Test owner: Task 3.
2. **Kalshi/Polymarket list a match under a code we can't map, or for a postponed fixture.** The quote is listed as unmapped/unmatched and never attached to the wrong FPL fixture (match on home+away code AND kickoff within ±36 h). Test owner: Task 1 / Task 2.
3. **Match-day run with Sleeper stats for the current week partly in.** Averages ignore the in-progress week; the 0-minute check reads it. Test owner: Task 4 / Task 6.
4. **A starter's match finished with 0 minutes, but every bench player's match has also started.** No replacement is suggested, and the page says so instead of suggesting a locked player. Test owner: Task 6.
5. **Odds fetched long ago** (cron stopped). Stale quotes aren't used and the page marks them. Test owner: Task 3 / Task 5.

---

## File Structure

```
sleeperfpl/
  markets.py        team-code mapping, quote usability, fixture matching (pure)
  fetch_markets.py  Kalshi + Polymarket fetchers -> market_quotes / player_quotes
  pricing.py        fit (λ_home, λ_away) from quotes; P(clean sheet); player probs (pure)
  projection_v2.py  bucket Sleeper stats; v2 projection per fixture (pure)
  live.py           in-progress week: unlikely-to-play hints, 0-minute replacements (pure)
  report_card.py    projection_log writes + weekly scoring
  (modified) db.py, config.py, sleeper_stats.py, report.py, __main__.py, README.md, deploy/crontab.example
tests/
  test_markets.py test_fetch_markets.py test_pricing.py test_projection_v2.py test_live.py test_report_card.py
  (modified) test_report.py
```

---

### Task 1: Market schema, config keys, team mapping, fixture matching

**Files:**
- Create: `sleeperfpl/markets.py`, `tests/test_markets.py`
- Modify: `sleeperfpl/db.py`, `sleeperfpl/config.py`, `sleeperfpl/matching.py` (`load_overrides` keeps `market_teams`), `tests/test_config.py`

**Interfaces:**
- Produces:
  - Snapshot tables (add to `SNAPSHOT_TABLES`):
    `market_quotes(fetched_at, source, home, away, kickoff, market, line, side, bid, ask, depth, ref)`, where `market` is one of `"1x2"`, `"total"`, `"team_total"` or `"btts"`; `side` is a team code (`"1x2"`, `"team_total"`) or `"draw"`/`"over"`/`"yes"`; `line` is a float or NULL; `ref` is the source ticker/slug.
    `player_quotes(fetched_at, source, home, away, kickoff, player, team, market, bid, ask, last, depth, ref)`, where `market` is `"goal1"`, `"goal2"` or `"soa"`.
  - Config fields: `market_max_spread: float`, `market_min_depth: dict[str, float]`, `market_stale_hours: float`, `matchday_window_hours: float`, `league_avg_goals: float`, `flag_cs_gap: float`, `flag_goal_gap: float`.
  - `KALSHI_ALIASES = {"BRI": "BHA", "CFC": "CHE", "LFC": "LIV"}`
  - `team_code(source: str, raw: str, fpl_codes: set[str], overrides: dict) -> str | None`: override `f"{source}:{raw.upper()}"` first, then the built-in aliases for that source, then `raw.upper()` if it's in `fpl_codes`, else None.
  - `usable(q: dict, cfg) -> bool`: bid and ask present, `0 < bid <= ask < 1`, `ask - bid <= cfg.market_max_spread`, `depth >= cfg.market_min_depth[q["source"]]`.
  - `mid(q) -> float` = `(bid + ask) / 2`.
  - `find_fixture(home: str, away: str, kickoff: str, fixtures: list[dict], teams: dict[int, str]) -> dict | None`: the FPL fixture with the same home and away codes whose kickoff is within ±36 h, else None.

- [ ] **Step 1: Write failing tests**

```python
def test_team_code_aliases_and_overrides(self):
    codes = {"BHA", "CHE", "ARS"}
    self.assertEqual(team_code("kalshi", "cfc", codes, {}), "CHE")
    self.assertEqual(team_code("kalshi", "ARS", codes, {}), "ARS")
    self.assertIsNone(team_code("kalshi", "XYZ", codes, {}))
    self.assertEqual(team_code("polymarket", "bri", codes, {"market_teams": {"polymarket:BRI": "BHA"}}), "BHA")
def test_usable_filters_spread_and_depth(self):  # spread 0.15 -> False; depth 10 kalshi -> False; good quote -> True; bid 0 -> False
def test_find_fixture_requires_codes_and_time(self):
    # same codes, kickoff 30 h apart -> fixture; 40 h apart -> None; swapped home/away -> None
def test_config_market_defaults(self):     # every new key has the Global Constraints default
def test_overrides_keep_market_teams(self) # load_overrides returns "market_teams" dict (empty when absent)
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_markets tests.test_config -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite, verify pass.**
- [ ] **Step 5: Commit** `feat: market quote schema, team mapping, fixture matching`

---

### Task 2: Kalshi and Polymarket fetchers

**Files:**
- Create: `sleeperfpl/fetch_markets.py`, `tests/test_fetch_markets.py`

**Interfaces:**
- Consumes: Task 1 tables; `now_iso`, `get_json`, `FetchError`.
- Produces:
  - `KALSHI = "https://api.elections.kalshi.com/trade-api/v2"`, `POLY = "https://gamma-api.polymarket.com"`
  - `fetch_kalshi(conn, *, players: bool = False, get=get_json) -> int`, which returns the number of quote rows written.
  - `fetch_polymarket(conn, get=get_json) -> int`
  - Both are non-fatal: a `FetchError` warns on stderr and returns 0. All rows of one call share one `fetched_at`.

Kalshi (`GET {KALSHI}/markets?series_ticker=<S>&status=open&limit=1000`, follow `cursor` until empty):
- The event ticker is `<SERIES>-<YYMONDD><HOME3><AWAY3>`, e.g. `KXEPLGAME-26OCT25SUNLEE`. Home and away are the raw codes. Kickoff is `expected_expiration_time` minus 2 h *(plan's call: Kalshi gives expected end, not start; ±36 h fixture matching absorbs the error)*.
- Prices: `bid = float(yes_bid_dollars)`, `ask = float(yes_ask_dollars)`, `depth = float(yes_bid_size_fp) + float(yes_ask_size_fp)`.
- `KXEPLGAME`: `market="1x2"`. The side comes from the ticker's last segment: a team code, or `TIE` → `"draw"`.
- `KXEPLTOTAL`: `market="total"`, `line=floor_strike`, `side="over"`.
- `KXEPLTEAMTOTAL`: `market="team_total"`, `line=floor_strike`. The side is the team code from the last ticker segment with its trailing digits stripped (`SUN3` → `SUN`).
- `KXEPLBTTS`: `market="btts"`, `side="yes"`.
- With `players=True`, also `KXEPLGOAL` and `KXEPLSOA` → `player_quotes`:
  - The player name is the `title` before `":"`.
  - The team is the code at the start of the last ticker segment, before the player key (`TOTBDAVIE33-1` → `TOT`; first 3 letters).
  - `KXEPLGOAL` with `floor_strike` 0.5 gives `goal1`, 1.5 gives `goal2`. `KXEPLSOA` gives `soa`.
  - `last = float(last_price_dollars)`.

Polymarket (`GET {POLY}/events?series_id=10188&closed=false&limit=500`):
- Use only events whose title has no `" - "` suffix, plus `"... - More Markets"` events.
- Home and away come from the slug `epl-<home>-<away>-<YYYY-MM-DD>...`. Kickoff is the market's `gameStartTime`.
- Each market has `bid=bestBid`, `ask=bestAsk`, `depth=liquidityNum`.
- `sportsMarketType` `"moneyline"` → `"1x2"`. The side is the team code from the market slug's last segment, or `"draw"` when the question contains "draw".
- `"totals"` → `"total"` with `line=line` and `side="over"`. Use the first outcome's price; check that `outcomes[0] == "Over"`.
- Ignore other types.

- [ ] **Step 1: Write failing tests** with canned payloads built from the recon samples (Kalshi 1x2/total/team_total/btts/goal market dicts, a Polymarket moneyline event and an O/U market):

```python
def test_kalshi_game_market_parsed(self):        # row: market 1x2, home SUN, away LEE, side SUN, bid .09 ask .12, depth 3111.41
def test_kalshi_tie_and_team_total_sides(self):  # TIE -> draw; SUN3 -> side SUN line 2.5
def test_kalshi_pagination_follows_cursor(self)
def test_kalshi_players_parsed(self):            # Ben Davies, team TOT, goal1
def test_polymarket_moneyline_and_totals(self):  # 3 rows 1x2 (incl draw) + 1 total line 2.5 side over
def test_fetch_failure_non_fatal(self):          # FetchError -> 0, no rows
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_fetch_markets -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite, verify pass.**
- [ ] **Step 5: Commit** `feat: Kalshi and Polymarket quote fetchers`

---

### Task 3: Pricing: expected goals per fixture

**Files:**
- Create: `sleeperfpl/pricing.py`, `tests/test_pricing.py`

**Interfaces:**
- Consumes: `usable`, `mid` (Task 1).
- Produces:
  - `@dataclass FixtureOdds: home: str; away: str; lam_home: float; lam_away: float; p_home: float; p_draw: float; p_away: float; cs_home: float; cs_away: float; sources: list[str]; fetched_at: str; n_quotes: int`
  - `fit_fixture(quotes: list[dict], home: str, away: str) -> FixtureOdds | None` takes already-usable quotes for one fixture and returns None when there are none.
  - `fixture_odds(quotes: list[dict], fixtures: list[dict], teams: dict[int, str], cfg, now: datetime) -> tuple[dict[int, FixtureOdds], list[str]]` returns (`fixture_id` → odds, unmatched descriptions). It drops quotes older than `market_stale_hours` and quotes that aren't `usable`, matches the rest with `find_fixture`, and fits each fixture.

Fit (independent Poisson goals): grid search `λh, λa ∈ {0.05, 0.10, …, 4.00}` minimizing the sum of squared errors between model and market probabilities over every usable quote. Use `mid` as the market probability. First, normalize the three 1x2 mids of each source to sum to 1. Model probabilities:
- `P(home win) / P(draw) / P(away win)` from the outer product of the two Poisson pmfs, truncated at 10 goals
- `P(total > line)`, `P(team goals > line)`, `P(BTTS) = (1 - e^-λh)(1 - e^-λa)`

Clean sheet: `cs_home = e^-λa`, `cs_away = e^-λh`. Each source's quotes count equally *(spec: equal weights to start)*.

- [ ] **Step 1: Write failing tests**

```python
def test_fit_recovers_known_lambdas(self):
    # build quotes from Poisson(1.8, 0.9): 1x2, total 2.5, team totals 0.5/1.5, btts, bid=p-0.01 ask=p+0.01
    o = fit_fixture(quotes, "ARS", "HUL")
    self.assertAlmostEqual(o.lam_home, 1.8, delta=0.1); self.assertAlmostEqual(o.lam_away, 0.9, delta=0.1)
    self.assertAlmostEqual(o.cs_home, math.exp(-0.9), delta=0.05)
def test_fit_with_only_1x2(self):        # still returns odds; p_home > p_away when home favoured
def test_no_quotes_returns_none(self)
def test_stale_and_thin_quotes_dropped(self):   # fixture_odds ignores 30h-old and spread-0.2 quotes -> fixture absent
def test_unmatched_quote_reported(self):        # code with no fixture -> listed in unmatched, not attached
def test_two_sources_both_used(self):           # sources == ["kalshi", "polymarket"]
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_pricing -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite, verify pass.**
- [ ] **Step 5: Commit** `feat: fit fixture expected goals from market quotes`

---

### Task 4: Projection v2 (bucketed Sleeper points)

**Files:**
- Create: `sleeperfpl/projection_v2.py`, `tests/test_projection_v2.py`
- Modify: `sleeperfpl/sleeper_stats.py`: add `sleeper_weeks(conn, season, player_id, max_week) -> list[dict]` (raw stats per played week, most recent first, `week <= max_week`, `min > 0`). Add `max_week: int | None = None` to `sleeper_history`. Test both in `tests/test_sleeper_stats.py`.

**Interfaces:**
- Consumes: `FixtureOdds` (Task 3), `project`/`Projection` (v1), `Fixture` (models).
- Produces:
  - `DEF_CODES = {"cs", "ga"}`, `ATT_CODES = {"g", "at", "sot", "kp", "acnc", "cos", "pkd"}`. Every other scored code is "other".
  - `split_points(stats: dict, scoring: dict) -> tuple[float, float, float]` returns (defensive, attacking, other). It uses keys `pos_<p>_<code>`, attributing each scored key by its code.
  - `@dataclass V2Inputs: att_avg: float; other_avg: float; p60: float; cs_points: float; ga_points: float; n: int`
  - `v2_inputs(weeks: list[dict], scoring: dict, prefix: str, window: int) -> V2Inputs | None` returns None when `weeks` is empty. `prefix` is the player's scoring prefix (`"pos_d_"` etc.). `cs_points = scoring.get(prefix+"cs", 0)`, `ga_points = scoring.get(prefix+"ga", 0)`. `p60` = the share of the window's weeks with `min >= 60`.
  - `project_v2(inp: V2Inputs, own_lam: float, opp_lam: float, team_baseline: float) -> float`, for one fixture:

```
defensive = inp.cs_points * exp(-opp_lam) * inp.p60 + inp.ga_points * opp_lam
attacking = inp.att_avg * (own_lam / team_baseline)
return defensive + attacking + inp.other_avg
```

  - `SLEEPER_PREFIX = {"GK": "pos_gk_", "DEF": "pos_d_", "MID": "pos_m_", "FWD": "pos_f_"}`, keyed by FPL position.

- [ ] **Step 1: Write failing tests**

```python
SC = {"pos_d_cs": 6, "pos_d_ga": -2, "pos_d_g": 10, "pos_d_tkw": 1, "pos_f_g": 9, "pos_f_sot": 2}
def test_split_points(self):
    self.assertEqual(split_points({"pos_d_cs": 1, "pos_d_ga": 0, "pos_d_g": 1, "pos_d_tkw": 3}, SC), (6, 10, 3))
def test_v2_inputs(self):          # 2 weeks (min 90, min 45): p60 0.5, att_avg/other_avg are means, cs_points 6
def test_project_v2_defender(self):
    inp = V2Inputs(att_avg=1.0, other_avg=3.0, p60=1.0, cs_points=6, ga_points=-2, n=5)
    self.assertAlmostEqual(project_v2(inp, 1.35, 0.5, 1.35), 6*math.exp(-0.5) - 1.0 + 1.0 + 3.0, places=4)
def test_project_v2_attacker_scales_with_team_goals(self):  # own_lam 2.7 vs baseline 1.35 doubles att part
def test_sleeper_history_max_week(self):   # week 8 stats exist; max_week 7 excludes them
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_projection_v2 tests.test_sleeper_stats -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite, verify pass.**
- [ ] **Step 5: Commit** `feat: market-aware projection v2`

---

### Task 5: Report: match view, v2 wiring, history-vs-market flags

**Files:**
- Modify: `sleeperfpl/report.py`, `sleeperfpl/models.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: Tasks 3–4; `latest(conn, "market_quotes")`, `latest(conn, "player_quotes")`.
- Produces (report.json additions):
  - `"matches": [{"fixture_id", "home", "away", "kickoff", "p_home", "p_draw", "p_away", "lam_home", "lam_away", "cs_home", "cs_away", "sources", "fetched_at", "stale": bool} | {"fixture_id", "home", "away", "kickoff", "market": null}]` for every target-GW fixture
  - `"markets_unmatched": [str]`
  - `PlayerCtx` gains `proj_v1: float`, `proj_source: str` (`"market"` | `"difficulty"`), `market_flags: list[str]`, `p_goal_market: float | None`, `p_goal_hist: float | None`; all appear in `_player_dict`.
  - `"projection_note"`: `"market"` / `"mixed"` / `"difficulty"` (all fixtures v2 / some / none)
- Rules:
  - v2 is used only when the Sleeper stats source is active.
  - Per fixture: v2 when `FixtureOdds` exists, else v1 (the v1 multiplier on that fixture's difficulty).
  - `proj` = the sum over the player's target-GW fixtures. `proj_v1` is always computed alongside.
  - The ruled-out → 0 rule still applies.
  - `team_baseline` = mean `lam` for that team over all `FixtureOdds` computed this build, else `cfg.league_avg_goals`.
- Flags:
  - `"cs"` (DEF/GK): `|historical CS rate − market P(CS)| >= cfg.flag_cs_gap`. Historical CS rate = share of played weeks with `cs >= 1`.
  - `"goal"`: `p_goal_market` exists and `|p_goal_market − p_goal_hist| >= cfg.flag_goal_gap`. `p_goal_hist` = share of played weeks with `g >= 1`.
  - `p_goal_market` = mid of the newest usable `goal1` quote whose normalized player name and team match the player.
- HTML:
  - a "Matches" card listing each fixture: W/D/L %, expected goals, clean-sheet %, source and age (or "no market — using FPL difficulty"; "stale" when stale)
  - in player tables, a small "mkt" tag when `proj_source == "market"`, and a ⚑ with a tooltip listing the flags
  - Proj cell tooltip shows v1
  - unmatched market list inside the Unmatched card

- [ ] **Step 1: Write failing tests** (extend `seed()` with `market_quotes` rows for the GW9 ARS–CHE fixture and Sleeper stats incl. `pos_m_cs`/`pos_d_*` keys):

```python
def test_matches_section_from_quotes(self)       # one match with p_home etc., sources ["kalshi"], stale False
def test_projection_uses_market_when_available(self)  # Saka proj_source "market", proj != proj_v1
def test_fixture_without_market_uses_v1(self)    # remove quotes -> proj_source "difficulty", proj == proj_v1, note "difficulty"
def test_stale_quotes_marked_and_unused(self)    # quotes 30h old -> match stale True, proj_source "difficulty"
def test_cs_flag(self)                           # defender with 100% CS history vs market cs 0.3 -> "cs" in market_flags
def test_goal_flag_from_player_quote(self)       # goal1 quote mid 0.6 vs hist 0.0 -> "goal"
def test_html_shows_match_card_and_flag(self)
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_report -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite, verify pass.**
- [ ] **Step 5: Commit** `feat: match view, v2 projections and market flags on the page`

---

### Task 6: Live week (match-day) logic

**Files:**
- Create: `sleeperfpl/live.py`, `tests/test_live.py`
- Modify: `sleeperfpl/gameweek.py` (add `live_gameweek`), `tests/test_gameweek.py`, `sleeperfpl/report.py` (`"live"` block + HTML card), `tests/test_report.py`

**Interfaces:**
- Produces:
  - `live_gameweek(events, fixtures, now) -> int | None`: the event whose first kickoff `<= now` and that isn't finished.
  - `@dataclass Replacement: starter: PlayerCtx; slot: str; bench: PlayerCtx | None; reason: str`
  - `zero_minute_replacements(starters: list[tuple[str, PlayerCtx]], bench: list[PlayerCtx], finished_teams: set[str], started_teams: set[str], minutes: dict[str, float], eligibility) -> list[Replacement]`:
    - A starter whose club is in `finished_teams` and whose `minutes.get(sid, 0) == 0` needs a replacement.
    - The bench candidate must be eligible for the slot, not `in_reserve`, have a club not in `started_teams`, and the highest `proj`.
    - Each bench player is used once.
    - With no candidate: `bench=None`, `reason="no bench player whose match hasn't started"`.
  - `unlikely_to_play(starters, player_quotes_by_team: dict[str, set[str]], injury: dict[str, str]) -> dict[str, str]` maps sleeper_id → reason:
    - `"injury: <status>"` when Sleeper injury status is not None.
    - `"not in market lineup"` when the starter's team has player quotes but his normalized name isn't among them.
  - report `"live": {"gameweek": int, "starters": [{slot, player}], "unlikely": {sid: reason}, "replacements": [{"starter","slot","bench","reason"}], "fixtures": [...same shape as matches...]} | null`
  - Starters come from the owner roster's `starters` (Sleeper-set lineup, slot order = `roster_positions` without BN). Minutes come from Sleeper stats for the live week (`min`). Finished/started teams come from FPL fixtures (finished flag, or kickoff + 2.5 h ≤ now; started = kickoff ≤ now).

- [ ] **Step 1: Write failing tests**

```python
def test_live_gameweek(self)                     # after first kickoff of 9 -> 9; before -> None
def test_zero_minute_gets_bench_from_later_match(self)
def test_no_candidate_when_all_bench_started(self)  # bench None, reason text
def test_played_starter_not_replaced(self)       # minutes 12 -> no replacement
def test_bench_used_once(self)
def test_unlikely_not_in_market_and_injury(self)
def test_report_live_block_only_when_in_progress(self)  # live null before kickoff; present after
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_live tests.test_gameweek tests.test_report -v`
- [ ] **Step 3: Implement.** The HTML puts a "This week (live) — GW<n>" card at the top, with replacements as `.tip` callouts and unlikely-to-play flags next to names.
- [ ] **Step 4: Run full suite, verify pass.**
- [ ] **Step 5: Commit** `feat: live match-day section`

---

### Task 7: Report card

**Files:**
- Create: `sleeperfpl/report_card.py`, `tests/test_report_card.py`
- Modify: `sleeperfpl/db.py` (table), `sleeperfpl/report.py` (log on build; `"report_card"` block + HTML card)

**Interfaces:**
- Produces:
  - Table `projection_log(made_at, season, gw, sleeper_id, kickoff, v1, v2, p_goal_market)` (not a snapshot table; append-only).
  - `log_projections(conn, season: str, gw: int, players: list[PlayerCtx], made_at: str) -> int` writes one row per rostered player and listed free agent. `kickoff` = the first fixture's kickoff, and `v2` is NULL when `proj_source != "market"`.
  - `score_weeks(conn, season: str, scoring: dict, last_finished: int) -> list[dict]` returns per finished GW `{"gw", "n", "mae_v1", "mae_v2", "n_v2", "brier_goal", "n_goal"}`. It uses each player's latest log row with `made_at <= kickoff`. Actual points = `sleeper_points` from that week's Sleeper stats; players with 0 minutes are excluded. `brier_goal` = mean of `(p_goal_market − scored)²`.
- HTML: a "Report card" card with a table of the last 8 GWs, plus a one-line reading such as "v2 beat v1 in 3 of 4 weeks", or "not enough weeks yet" when there are fewer than 2 weeks with v2 rows.

- [ ] **Step 1: Write failing tests**

```python
def test_log_and_score(self):   # 2 players, v1 4/6, v2 5/6, actual 5 and 8 -> mae_v1 1.5, mae_v2 1.0
def test_uses_last_row_before_kickoff(self)    # later row after kickoff ignored
def test_zero_minute_players_excluded(self)
def test_brier(self)                           # p .6 scored 1 -> .16
```

- [ ] **Step 2: Run, verify fail.** `python3 -m unittest tests.test_report_card -v`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite, verify pass.**
- [ ] **Step 5: Commit** `feat: weekly projection report card`

---

### Task 8: CLI, schedule, docs, live smoke run

**Files:**
- Modify: `sleeperfpl/__main__.py`, `deploy/crontab.example`, `README.md`, `config.example.json`

**Interfaces:**
- Produces:
  - `fetch-markets`: `fetch_kalshi(players=False)` + `fetch_polymarket`.
  - `matchday`: exits quietly unless a fixture kicks off within `matchday_window_hours`, or one finished in the last 2.5 h. Otherwise it runs `fetch_rosters`, `fetch_fixtures`, `fetch_kalshi(players=True)`, `fetch_polymarket`, then Sleeper stats for the live week (`fetch_sleeper_stats` with `last_week` = live GW, `force=False`), then `build`.
  - Cron additions: `0 */2 * * *` fetch-markets + build; `*/30 * * * 5,6,0,1` matchday. Rebuild uses `;` like the other lines.
- [ ] **Step 1: Implement and run the full suite.** `python3 -m unittest discover -s tests` → OK
- [ ] **Step 2: Live smoke run** against the real league:

```bash
python3 -m sleeperfpl fetch-markets
python3 -m sleeperfpl build
python3 -m sleeperfpl matchday
```

Expected:
- `fetch-markets` prints row counts for both sources.
- `site/report.json` has `matches` with market odds for most GW9 fixtures, `projection_note` of `"market"` or `"mixed"`, and `markets_unmatched` either empty or explained.
- `matchday` either no-ops with a message or builds a `live` block.
- Check a few λ values for plausibility. Strong home favourites should have λ_home > λ_away.
- [ ] **Step 3: Docs.** README covers the new commands, config keys, the "mkt" tag, flags, the live section, the report card, and `market_teams` overrides.
- [ ] **Step 4: Commit** `feat: market CLI commands, schedule, docs`
