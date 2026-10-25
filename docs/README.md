# Sleeper EPL rankings: full documentation

The short version is in the [top-level README](../README.md). This page covers
setup, every command, configuration, how projections work, deployment and the
code layout.

- [What the page shows](#what-the-page-shows)
- [Setup](#setup)
- [Commands](#commands)
- [How projections work](#how-projections-work)
- [Injury labels and expected minutes](#injury-labels-and-expected-minutes)
- [Configuration (`config.json`)](#configuration-configjson)
- [`scoring_map.json`](#scoring_mapjson)
- [`overrides.json`](#overridesjson)
- [Running on a server](#running-on-a-server)
- [Known limitations](#known-limitations)
- [Outputs](#outputs)
- [Data sources](#data-sources)
- [Database](#database)
- [Code layout](#code-layout)
- [Tests](#tests)
- [The original script, `sleeper.py`](#the-original-script-sleeperpy)

## What the page shows

`build` writes `site/index.html` with:

- your **best starting XI**, using only lineups your league's roster slots allow;
- **swap tips**, either an XI gain ("Drop X, add Y: +1.9 projected") or a
  "bench upgrade" (its gain is measured on the average/projection blend, not
  "projected"; `swap_min_gain` applies to both). When several drops give the
  same gain, the tip drops the player with the lowest projection. Each tip is
  measured on the roster after the tips above it, so the XI gains add up to
  what making all of them gains, and a player one tip adds is never another
  tip's drop. Tips are labelled as waiver claims once the game week has started (players are instant free agents until the
  week's first kickoff, then go through waivers);
- the **top free agents** per position, with Sleeper trending adds;
- a **Matches** table from Kalshi and Polymarket odds: win/draw/win chances,
  each team's expected goals and clean-sheet chance;
- a **This week (live)** section during a game week: starters who may not
  play, and bench swaps for starters who got 0 minutes (each player locks at
  his own kickoff, so an unused starter can still be swapped for a bench
  player whose match hasn't started);
- a **Report card**: week by week, how far off past projections were (players
  who didn't play count as 0), how well they ordered players within each
  position, the real points of the lineup you set vs the one this page suggested
  vs the best possible from your roster, and how well the match odds did. Once
  minutes estimates have been logged, it also shows the minutes-aware
  projection beside today's, a start-odds score, and how often players with
  each injury label actually played.

Hover a projection to see the player's estimated chance of starting and of
playing, plus his FPL and Sleeper labels (see [Injury labels and expected
minutes](#injury-labels-and-expected-minutes)). The hover text appears only when
a start chance exists (a minutes model and Sleeper stats), and a label is shown
only if it isn't "a100" or "none".

Points are your league's exact scoring, computed from Sleeper's own weekly
stats. If those are unavailable, or you set `"stats_source": "fpl"`, it falls
back to official FPL stats mapped through `scoring_map.json`, which covers only
part of the league's scoring; the page then shows an "Approximate" banner.

## Setup

Requires Python 3.10+. Standard library only; nothing to install.

```bash
cp config.example.json config.json
```

Set the two required keys in `config.json`:

- `league_id`: from your league's URL (`sleeper.com/leagues/<league_id>/...`).
- `owner`: your Sleeper display name (or user id).

The repo ships a `scoring_map.json` made for the author's league, and
`fetch-daily` only creates one if the file is absent. If your league's scoring
differs, delete `scoring_map.json` before the first `fetch-daily` so it is
generated from your league (it then prints "created ..."). An unedited
placeholder `league_id` is not a config error; the Sleeper fetch fails with an
`error:` (a Sleeper HTTP error, or "League X not found or has no sport").
Run `fetch-daily` before `fetch-rosters`: otherwise rosters are fetched and the
command then fails with "no league data yet".

First run, in order:

```bash
python3 -m sleeperfpl fetch-daily            # creates scoring_map.json only if absent
python3 -m sleeperfpl fetch-rosters
python3 -m sleeperfpl fetch-history --force  # first time only; takes a few minutes
python3 -m sleeperfpl coverage               # which scoring rules FPL can't see
python3 -m sleeperfpl fetch-markets          # betting odds (free, no API key)
python3 -m sleeperfpl fetch-history --season 2025   # once: last season's year (2025 for the 2026 season), for backtest
python3 -m sleeperfpl build                  # writes site/index.html and site/report.json
```

Open `site/index.html` in a browser.

## Commands

```
python3 -m sleeperfpl [--config PATH] <command>
```

`--config` defaults to `$SLEEPER_FPL_CONFIG`, then `./config.json`. Relative
paths inside the config (`db_path`, `output_dir`, `scoring_map_path`,
`overrides_path`) are resolved against the config file's directory.

| Command | What it does |
|---|---|
| `fetch-daily` | Sleeper league, users and players; FPL bootstrap (players, teams, game weeks) and fixtures. Creates `scoring_map.json` from the league's scoring only if it doesn't exist (the repo ships one for the author's league; it is never overwritten). |
| `fetch-rosters` | Sleeper rosters and trending adds. |
| `fetch-status` | FPL bootstrap only (injury flags, chances, news), cheap enough to run around press conferences. |
| `fetch-history [--force]` | Sleeper weekly stats (`sleeper_stats`) plus the FPL per-match history crawl (`fpl_history`, always run regardless of `stats_source`). The Sleeper step fetches every stored-missing week up to the last finished game week and re-fetches the latest stored week on every run, so once at least one game week has finished the command is never a pure no-op (before that, it does nothing). The FPL crawl is skipped unless a new game week has finished since the last complete crawl; `--force` re-crawls. The crawl fetches every player with minutes > 0, one request per player at `fpl_request_interval`; if any player fails, the "last crawled" marker is not advanced and the next run retries. With `--season <year>` (a past season) it only fetches that season's Sleeper stats, weeks 1–38, for `backtest`; it skips the FPL crawl and doesn't change the stats week shown on the page. |
| `coverage` | Reads only `scoring_map.json` and the stored league (no DB writes). Lists the rules, which ones FPL can and can't cover, league scoring keys missing from the map or with different points, and makes two live probes of Sleeper's stats endpoints (`api.sleeper.app` and `api.sleeper.com`; see Data sources). |
| `fetch-markets` | Kalshi and Polymarket match odds (no player markets; those come from `matchday`). A source that fails with a fetch error (network, HTTP or bad JSON) warns, keeps the previous odds and doesn't stop the other. Any other kind of error (for example an unexpected response shape) is not caught and aborts the command with a traceback, so the second source may not run. |
| `matchday [--force]` | For match days. Runs only if a kickoff falls within the next `matchday_window_hours`, or a match kicked off between 2.5 and 5 hours ago (just finished; this post-match window is fixed, not configurable). `--force` runs anyway. When it runs it refreshes rosters, the FPL bootstrap (injury flags and chances), fixtures, Kalshi odds including player markets, Polymarket odds, and (if a game week is live) Sleeper stats for that game week, then runs `build`. It refreshes everything, not only fixtures kicking off soon; the window only decides whether it runs. A failure in `build` itself is not caught and exits 1. Each step that fails only prints `warning: <step> failed` and the rest continue. When not due it prints a "skipped" line, does not build, and exits 0. |
| `backtest [--season S] [--json PATH] [--minutes]` | Replays finished game weeks, starting at game week 2 (game week 1 has no earlier weeks and is never scored), to compare projection methods (`v1`, a naive `season_avg` and `v1m`). Needs Sleeper stats (`fetch-history`), else it exits with an error. For week *g* it uses only each player's Sleeper stats from earlier weeks of the same season, scores every player who played in one of the 5 weeks before, and prints per week the average miss (everyone / played only) and how well each method ordered players within each position, then how many weeks each method beat `v1`. Seasons are reported separately. `--season` (repeatable) picks seasons, default all stored. `--json` also writes the full results. Past seasons have no fixture data, so `v1` runs with a neutral multiplier there and blank or double weeks are skipped. `v1m` refits the minutes model for each week on earlier weeks only (needs 1,500 rows, else the shipped default if it is from an earlier season, else that week has no v1m); the output adds the per-week difference v1m − v1 with a 95% range and a sign-flip p-value, and the "minutes error share" (v1m's miss against v1m given each player's actual minutes). `--minutes` also prints Brier scores for P(start), P(appear) and P(60+) against two baselines (last week; last 5 weeks) and a reliability table. Read-only. |
| `fit-minutes [--force] [--export PATH]` | Refits the start model on every stored regular week and saves it (`minutes_model`); prints the rows used and a holdout Brier score on the newest season's last 4 weeks. Does nothing if no new week was stored since the last fit, unless `--force`. `--export` also writes the model as JSON (the shipped default came from a 2025-only fit). |
| `label-audit` | Rebuilds and prints, per injury label, how many logged players had it and how often they started, played (with a 90% range) and reached 60 minutes. "Sleeper says Out, FPL says available" comes first. Empty until weeks finish after builds have logged labels. |
| `build` | Writes `report.json` and `index.html` to `output_dir` (via a temp file and rename) and, when it uses Sleeper stats and there is a target game week, appends to `projection_log`, `recommendation_log` and `match_log`. When it uses Sleeper stats and a minutes model exists (a default one ships) it also rebuilds `label_counts` (a database write during a build). Prints `wrote <dir>/index.html (GW<n>, <k> swap tips)`. |

Every command first opens the database, creating `data/` and all tables if
missing. Commands exit with status 1 and print `error: ...` to stderr for
config, fetch and build errors (for example, `build` before `fetch-daily` and
`fetch-rosters`, or `fetch-rosters` before `fetch-daily`). Other exceptions are
not caught and show a traceback. Fetchers retry connection errors, HTTP 429 and
5xx responses up to 3 times with exponential backoff; other 4xx responses fail
immediately.

## How projections work

- **With betting odds** (tagged `mkt` on the page): a player's weekly league
  points are split into three buckets by Sleeper stat code.
  - Defensive (clean sheet, goals against): replaced by the market's
    clean-sheet chance and expected goals against. Clean-sheet points are
    scaled by how often the player plays 60+ minutes.
  - Attacking (Sleeper stat codes `g`, `at`, `sot`, `kp`, `acnc`, `cos`,
    `pkd`): scaled by his team's expected goals.
  - Everything else (tackles, saves, cards…): his recent average.
- **Without usable odds** for a match: recent average (last `avg_window`
  matches) × the fixture-difficulty multiplier. Hover a projection to see this
  number for comparison.
- **Team expected goals** come from fitting independent Poisson goal rates for
  each side to every usable quote's mid price (match result, totals, team
  totals, both teams to score); win, draw and clean-sheet chances are read off
  that fit.
- ⚑ marks players where history and the market disagree: a clean-sheet record
  at least `flag_cs_gap` from the market's clean-sheet chance (GK and DEF
  only), or (with player goal markets) market goal odds at least
  `flag_goal_gap` from how often he has
  scored.
- Quotes wider than `market_max_spread` or thinner than `market_min_depth` are
  ignored. Quotes older than `market_stale_hours` never drive projections; a
  fixture with only stale odds still appears in the Matches table, tagged stale.
- Market-adjusted projections need Sleeper stats. With `stats_source: "fpl"`
  (or the fallback below) every projection uses the difficulty method.
- The in-progress game week is excluded from the recent average.

Builds that use Sleeper stats and have a target game week log their
projections. After a game week finishes, the report card compares each
player's last projection made before his kickoff with his real league points.
Players who didn't play count too, as 0 points (or whatever their stats score),
so a projection for someone who sat out is a miss.
The report card needs those logs, so it is empty when stats come from FPL.

## Injury labels and expected minutes

**Who counts as out.** A player is "out" (projection 0, never picked, never
suggested) only when FPL says so: suspended (`s`), unavailable (`u`), not in
the squad (`n`), or a 0% chance of playing. FPL status injured (`i`) with no
chance value counts as a 0% chance, so it is out too. Sleeper's `injury_status` never
makes a player out: Sleeper's labels are often wrong, and a player only
Sleeper calls "Out" gets his normal projection with the flag "Sleeper: out" (in
the default `minutes_model: "shadow"`; with `"on"`, Sleeper's Out label scales
his start and appear chances by 0.80 and 0.95 in the minutes-based projection).
FPL injured or doubtful with a chance above 0 shows as "doubtful"; doubtful
(`d`) with no chance value is treated as a 50% chance.
FPL's "chance of playing next round" means the target game week only until its
deadline; after the deadline the tool reads FPL's "this round" chance instead.

**Expected minutes** (`minutes.py`, `minutes_fit.py`). For each player:

- His regular weeks this season (every club played once) become states:
  started, came off the bench, or no minutes. Weeks FPL had him out of are left
  out once the label history covers them.
- A logistic regression on recent states (start rates over 3 and 10 weeks,
  last week's minutes, a run of zero-minute weeks, position) gives P(start).
  It is fitted by `fit-minutes` on every stored regular week; a default fit
  from 2025 (`sleeperfpl/minutes_coef.json`) is used until the first fit.
- His bench record gives P(sub | no start); per-position minute tables,
  measured from stored weeks and moved toward his own last 10 starts, give a
  spread of likely minutes per fixture.
- With fewer than 3 weeks of history, P(start) leans on his start share last
  season (any club), else his position's rate for such players.
- **Labels** then adjust both chances. FPL's label leads (e.g. 75% doubtful
  multiplies P(start) by 0.63 and P(appear) by 0.82); Sleeper's adds a small
  modifier on top (Sleeper "Out" with FPL available: ×0.80 start, ×0.95
  appear). These starting values are guesses; after each finished week the
  table learns from how often logged players with each label actually started
  and played (observed over expected, with `label_prior_k` pseudo-players behind
  each guess). If FPL's news is older than the club's last match, a one-match
  doubt counts half.

**`v1m` and `v2m`.** v1m is v1 with minutes: points per 90 (from recent played
weeks) times each scenario's minutes, plus clean-sheet points only in scenarios
of 60+ minutes (in this league clean sheets need 60). v2m does the same to v2:
the market clean-sheet chance uses P(60+), and everything else scales with
expected minutes.

**Shadow vs on.** With `minutes_model: "shadow"` (default) the minutes numbers
are computed, logged and shown on hover, but picks use v1/v2. With `"on"`,
v1m/v2m drive the projection, XI, swaps and free agents. The author's own heuristic
for switching on (not enforced or tested by the code): in `backtest`, v1m has a
lower average miss than v1 with a 95% range on the weekly difference that
excludes 0, and its average order is not meaningfully worse.

## Configuration (`config.json`)

`league_id` and `owner` are required (a missing or empty value is an error);
everything else has a default. `config.example.json` lists every key with its
default. Unknown keys are silently ignored. `config.json` is git-ignored.

| Key | Default | What it does |
|---|---|---|
| `league_id` | (required) | Sleeper league id |
| `owner` | (required) | your Sleeper display name or user id |
| `db_path` | `data/fpl.db` | SQLite database |
| `output_dir` | `site` | where `index.html` and `report.json` go |
| `scoring_map_path` | `scoring_map.json` | FPL-to-league scoring map |
| `overrides_path` | `overrides.json` | manual matching fixes (optional file) |
| `stats_source` | `"sleeper"` | `"sleeper"`: exact league points from Sleeper's stats; `"fpl"`: approximate, via `scoring_map.json` |
| `difficulty_multipliers` | `1`→1.2, `2`→1.1, `3`→1.0, `4`→0.9, `5`→0.8 | how FPL fixture difficulty scales the fallback projection. Partial overrides keep the other defaults |
| `avg_window` | 5 | matches in the recent average |
| `swap_min_gain` | 1.0 | minimum projected gain before a swap is suggested |
| `max_swaps` | 3 | swap tips shown |
| `free_agents_per_position` | 10 | free agents listed per position |
| `undroppable` | `[]` | players never suggested as drops, by name or Sleeper id, e.g. `["Bukayo Saka"]`. Shown with a 🔒 |
| `slot_eligibility` | `{"GK":["GK"], "D":["D","DEF"], "M":["M"], "F":["F"], "FM_FLEX":["F","M"], "MD_FLEX":["M","D","DEF"], "FMD_FLEX":["F","M","D","DEF"], "DEF":["D","DEF"], "MID":["M","MID"], "FWD":["F","FWD"]}` | which player positions can fill each lineup slot. Flex slots are already included. If `build` reports an unknown slot, add it. A value you supply **replaces the whole default dict** (no merge), so copy the defaults and extend them |
| `fpl_request_interval` | 0.5 | seconds between FPL history requests |
| `market_max_spread` | 0.10 | ignore quotes whose bid/ask gap is wider than this |
| `market_min_depth` | `{"kalshi": 50, "polymarket": 1000}` | ignore thinner markets (Kalshi: contracts at best bid+ask; Polymarket: USD liquidity). Merged per key with the defaults |
| `market_stale_hours` | 24 | older quotes are marked stale and not used for projections |
| `matchday_window_hours` | 3 | how far ahead of a kickoff `matchday` will run (it only gates whether the command runs; a run refreshes everything). It also runs 2.5 to 5 hours after any kickoff, independent of this setting |
| `league_avg_goals` | 1.35 | expected-goals baseline when a team has no market history |
| `flag_cs_gap` | 0.25 | clean-sheet gap that earns a ⚑ |
| `flag_goal_gap` | 0.20 | goal-chance gap that earns a ⚑ |
| `category_low` | 3 | league-point cutoffs for the report card's low / mid / haul groups: low is under `category_low`, haul is `category_haul` or more. `category_low` must be less than `category_haul` |
| `category_haul` | 10 | see `category_low` |
| `minutes_model` | `"shadow"` | `"shadow"`: minutes estimates are logged and shown only; `"on"`: v1m/v2m drive picks |
| `label_prior_k` | 20 | how many pseudo-players stand behind each label's starting guess before this league's outcomes take over |
| `sleeper_label_weight` | 1.0 | 0 to 1: how much Sleeper's injury labels move the minutes estimate (0 ignores them) |
| `minutes_shrink_k` | 3 | how many 90-minute matches of the position's average stand behind each player's v1m/v2m points per 90, so a player with a few minutes doesn't get an absurd rate (0 turns it off) |

## `scoring_map.json`

`build` only uses it when stats come from FPL; `coverage` always reads it. `fetch-daily` generates it once from your
league's scoring settings and never overwrites it. Each key is a Sleeper
scoring key (real keys look like `pos_d_g`: a position prefix `gk`/`d`/`m`/`f`
plus a stat code).

```json
{"rules": {
  "pos_d_g":   {"fpl_field": "goals_scored", "points": 6, "positions": ["DEF"]},
  "app_60":    {"fpl_field": "minutes", "points": 2, "min": 60},
  "gc_2":      {"fpl_field": "goals_conceded", "points": -1, "per": 2},
  "pos_f_sot": {"fpl_field": null, "points": 1}
}}
```

The keys above are illustrative; generated keys look like `pos_<gk|d|m|f>_<code>`,
and `positions` values are `GK`/`DEF`/`MID`/`FWD`. The loader ignores any extra
field on a rule: the checked-in file has a `note` on four rules (`pos_*_int`),
which is for humans only and has no effect.

- `min`: award the points once if the stat is at least this.
- `per`: points for every N units.
- `positions`: only apply to these positions.
- `null`: FPL has no such stat, so the rule is skipped.

Valid `fpl_field` values: `minutes`, `starts`, `goals_scored`, `assists`,
`clean_sheets`, `goals_conceded`, `own_goals`, `penalties_saved`,
`penalties_missed`, `yellow_cards`, `red_cards`, `saves`, `bonus`, `bps`,
`tackles`, `recoveries`, `clearances_blocks_interceptions`,
`defensive_contribution` (see `FPL_FIELDS` in `sleeperfpl/scoring_map.py`).
Run `coverage` after editing.

## `overrides.json`

Optional. Fixes players, clubs or market teams that don't match automatically.
Player matching is conservative: an ambiguous name stays unmatched rather than
guessed, and the page lists unmatched rostered players.

```json
{"players": {"<sleeper_player_id>": <fpl_id>},
 "clubs": {"<sleeper club code>": "<FPL short name>"},
 "market_teams": {"kalshi:<code>": "<FPL short name>", "polymarket:<code>": "<FPL short name>"}}
```

The page also lists betting markets it couldn't match to a fixture; add those
under `market_teams`. `market_teams` keys are normalised to lowercase source and
uppercase code. A missing file means no overrides; invalid JSON or shape is a
config error. Overrides win over automatic matching, including when two Sleeper
players claim the same FPL id. FPL player ids can change between seasons, so
review `players` at the start of a new season (the database keys history by
season for the same reason).

## Running on a server

1. Copy the repo to `/opt/sleeper_fpl`, plus your `config.json`.
2. `mkdir -p /opt/sleeper_fpl/data`, then run the setup commands once.
3. `crontab -e` and paste `deploy/crontab.example`. It schedules (server local
   time, all guarded by `flock` so runs don't overlap; each fetch job is followed
   by `build`, while `matchday` builds only when it runs):
   - `fetch-daily` daily at 06:00;
   - `fetch-history` daily at 03:30 (the FPL crawl is skipped unless a game
     week has finished, but the Sleeper stats step runs every time; the
     comment in `crontab.example` calling it a no-op is not quite right);
   - `fetch-rosters` every 4 hours, and hourly 12:00–23:00 on Thursdays;
   - `fetch-status` every 4 hours at :45, and hourly 12:45–23:45 on Thursdays
     and Fridays (press conferences);
   - `fit-minutes` after `fetch-history` (skips itself when no new week is
     stored);
   - `fetch-markets` every 2 hours at :15;
   - `matchday` every 30 minutes Friday to Monday (it also refreshes FPL's
     injury flags before kickoffs). At 03:30 on those days it collides with the
     `fetch-history` job; they share one non-blocking `flock`, so whichever
     starts second silently doesn't run.
4. Install `deploy/sleeperfpl-web.service` (instructions at the top of the
   file) to serve `site/` at `http://<server>:8080/` with Python's
   `http.server`. Keep port 8080 closed on your router.

Logs go to `data/cron.log`. The cron entries assume the repo is at
`/opt/sleeper_fpl`; edit the paths otherwise. `matchday` rebuilds only when it
actually runs, so outside match windows the page is refreshed only by the other
jobs. The systemd unit runs `python3 -m http.server 8080 --directory
/opt/sleeper_fpl/site --bind 0.0.0.0` as `nobody`, with `Restart=always`.

## Known limitations

- **Silent stats fallback.** `stats_source: "sleeper"` falls back to FPL stats
  plus `scoring_map.json` if the database has no Sleeper stats for the league's
  season. `report.json` `stats_source` shows what was actually used. In that
  case `scoring_map.json` must exist or `build` fails.
- **Sleeper stats endpoint is undocumented**, and Sleeper's week number is
  assumed to equal the FPL game week.
- **Fixed labels.** The projection column header reads "Avg5" even if
  `avg_window` is changed.
- **No pruning.** Snapshot tables grow forever.
- **Unmatched players.** Ambiguous names stay unmatched rather than guessed;
  `unmatched_count` in `report.json` counts all unmatched Sleeper players, not
  only rostered ones. Roster players missing from Sleeper's player map show as
  `Sleeper player <id>` with flag "no data".
- **Market data parsing** depends on Kalshi and Polymarket response fields and
  on team-code aliases (`market_teams` overrides fix the rest).
- **Backtest approximations.** In the current season, fixture difficulty comes
  from the newest FPL snapshot and each player is placed at his current club, so a
  mid-season transfer is replayed at his new club. Past seasons have no fixture
  data (neutral multiplier), and the backtest can't replay market odds, injury
  flags or lineup decisions, because none of those were stored back then.
- **Minutes model gaps.** Returns from injury, manager changes and European
  midweek rotation aren't modelled; a double week uses the same minutes for
  both matches. Injury labels can't be backtested (no history of them before
  2026-10-24), and the label table starts from guesses until this league's
  outcomes build up.
- **Per-90 shrinkage is one number for every stat.** v1m and v2m pull each
  player's points per 90 toward his position's pooled average with
  `minutes_shrink_k` pseudo-90s (default 3), so a goal in 3 minutes no longer
  projects 38 points. The average is pooled over all of a position's players,
  not by team or role, and `k` is the same for every stat. In the backtest,
  k = 3 and k = 6 scored about the same; k = 0 (no shrinkage) lost to v1 in 2026.
- **No rollback past this version.** The database gains columns that older
  code can't write to; going back to an older version needs a database backup
  from before the upgrade.
- **Planned, not built:** a prediction model to replace the projection step, and
  a Claude "second opinion" that reads `site/report.json`.

## Outputs

- `site/index.html`: the page.
- `site/report.json`: the same data, structured, for other tools to read. Top-level keys:
  `generated_at`, `gameweek`, `mode`, `first_kickoff`, `stats_source`,
  `sources`, `coverage`, `projection_note`, `xi`, `swaps`, `free_agents`,
  `matches`, `markets_unmatched`, `live`, `report_card`,
  `undroppable_unmatched`, `unmatched_rostered`, `unmatched_count`, `in_progress`,
  `second_opinion`.

Both files are written atomically (temp file, then rename). The page is
self-contained (inline CSS and JS, no external assets) and tables sort when you
click a header.

Details of `report.json` worth knowing:

- `stats_source` is `"sleeper"` only if configured so and Sleeper stats exist,
  otherwise `"fpl"`.
- `mode` is `"waiver"` once any unfinished game week has had a kickoff, else
  `"instant"`.
- `projection_note` is `"market"` (all fixtures priced), `"mixed"`, or
  `"difficulty"`.
- `live` is `null` when there is no live game week.
- `second_opinion` is `null` until `second-opinion` has saved an answer for
  the target game week. Otherwise it has `made_at`, `model`, `summary`, `xi`
  (`slot`, `sleeper_id`, `name`, `club`, `reason`, `in_tool_xi`), `swaps`
  (`drop`, `add`, `reason`, `in_tool`), `notes` (`fixture`, `note`),
  `agree_xi`, `xi_size` and `changed` (names in the opinion that have since
  left your roster or been taken by another team).
- `report_card` has `weeks` (per finished week: `n`, `mae_v1` over everyone,
  `mae_v1_played`, `by_cat_v1` per outcome group `dnp`/`low`/`mid`/`haul`,
  `rank_v1`, the v2 and `ep_next` comparisons, `mae_v1m`/`rank_v1m` beside v1 on
  the same players, the same for v2m, and `brier_start`/`brier_app`), `labels`
  (the `label-audit` rows), `decisions` (`set_pts`,
  `tool_pts`, `best_pts` and swap outcomes), `matches` (`log_loss`, `rps`,
  `brier_cs`) and `summary` (a string). `weeks`, `decisions` and `matches` keep
  the last 8 entries; `labels` is not trimmed.
- `matches` entries always have `fixture_id`, `home`, `away`, `kickoff` and
  `market`. `market` is `null` when unpriced; when priced they also have
  `stale`, `sources`, `fetched_at`, `p_home`, `p_draw`, `p_away`, `lam_home`,
  `lam_away`, `cs_home`, `cs_away`.
- Player objects carry `sleeper_id`, `name`, `positions`, `club`, `flag`, `avg`,
  `n`, `proj`, `proj_v1`, `proj_source`, `trending_adds`, `undroppable`,
  `market_flags`, `p_goal_market`, `p_goal_hist`, `fixtures`, and the minutes
  estimate: `p_start`, `p_app`, `p60`, `e_min`, `fpl_bucket`, `slp_bucket`,
  `proj_v1m`, `proj_v2m` (all null without a model or Sleeper stats).
- `free_agents` is keyed by each player's first listed position.
- The "Approximate" banner appears only when stats come from FPL and some
  scoring rules have no FPL field or some league scoring keys are absent from
  the scoring map. A separate "League points changed" banner shows for rules
  whose points differ.

## Data sources

All free, no API keys.

| Source | Base URL | Used for |
|---|---|---|
| Sleeper API | `https://api.sleeper.app/v1` | league, users, players, rosters, trending adds |
| Sleeper stats (undocumented) | `https://api.sleeper.app/v1/stats/<sport>/regular/<season>/<week>` | weekly player stats, keyed like the league's scoring settings |
| Sleeper stats (coverage probe) | `https://api.sleeper.com/stats/<sport>/<season>/<week>?season_type=regular` | second probe made by `coverage` |
| Fantasy Premier League | `https://fantasy.premierleague.com/api` | players, teams, game weeks, fixtures, per-match history |
| Kalshi | `https://api.elections.kalshi.com/trade-api/v2` | match and player odds |
| Polymarket | `https://gamma-api.polymarket.com` | match odds |

Sleeper's week number is treated as the FPL game week.

## Database

SQLite at `db_path` (default `data/fpl.db`), created on first run. It keeps
every snapshot, which is the history a future prediction model would need.

**Snapshot tables** are append-only: each fetch inserts rows tagged with one
`fetched_at`, and readers use the newest batch.

| Table | Columns |
|---|---|
| `sleeper_league` | `league_id`, `data_json` |
| `sleeper_players` | `player_id`, `name`, `team`, `positions_json`, `status`, `injury_status`, `active` |
| `sleeper_rosters` | `roster_id`, `owner_id`, `players_json`, `starters_json`, `reserve_json` |
| `sleeper_users` | `user_id`, `display_name` |
| `sleeper_trending` | `direction`, `player_id`, `count` |
| `fpl_elements` | `fpl_id`, `first_name`, `second_name`, `web_name`, `team_id`, `element_type`, `status`, `chance_next`, `total_minutes`, `ep_next` (FPL's own expected points), `news`, `news_added`, `chance_this` (FPL's chance for the current round) |
| `fpl_teams` | `team_id`, `short_name`, `name` |
| `fpl_events` | `event_id`, `deadline_time`, `finished` |
| `fpl_fixtures` | `fixture_id`, `event`, `team_h`, `team_a`, `team_h_difficulty`, `team_a_difficulty`, `kickoff_time`, `finished`, `team_h_score`, `team_a_score` |
| `market_quotes` | `source`, `home`, `away`, `kickoff`, `market` (`1x2`/`total`/`team_total`/`btts`), `line`, `side`, `bid`, `ask`, `depth`, `ref` |
| `player_quotes` | `source`, `home`, `away`, `kickoff`, `player`, `team`, `market` (`goal1`/`goal2`/`soa`), `bid`, `ask`, `last`, `depth`, `ref` |

All snapshot tables also have `fetched_at`. Opening an older database adds any
missing columns in place.

**Other tables:**

| Table | Key | Contents |
|---|---|---|
| `fpl_history` | `(season, fpl_id, fixture_id)` | per-match FPL stats (`round`, `kickoff_time`, `minutes`, `stats_json`) |
| `sleeper_stats` | `(season, week, player_id)` | Sleeper weekly stats (`stats_json`) |
| `projection_log` | none (append-only) | every build's projections (`v1`, `v2`, `p_goal_market`, plus `position` and FPL's `ep_next`) for the report card, and the minutes estimate: `v1m`, `v2m`, `p_start`, `p_app`, `p60`, `e_min`, the labels used (`fpl_bucket`, `slp_bucket`, `label_at`), and the chances before labels (`p_start_model`, `p_app_model`) and after FPL's (`p_start_fpl`, `p_app_fpl`) |
| `minutes_model` | `version` | fitted start models (`model_json`, `n_rows`, `trained_on` = last stored week, `holdout_brier`); the newest is used |
| `label_counts` | `(season, kind, cell)` | per injury label: players, starts, appearances, 60+ and expected starts/appearances, rebuilt each build from `projection_log` and `sleeper_stats` |
| `match_log` | none (append-only) | every build's fitted odds per target-week fixture (`method`, `lam_home`, `lam_away`, win/draw/away and clean-sheet chances, `n_quotes`, `avg_spread`) |
| `recommendation_log` | none (append-only) | every build's suggested XI (`xi_json`) and swaps (`swaps_json`), with `roster_id` and the week's `first_kickoff` |
| `meta` | `key` | small key/value state |

## Code layout

```
sleeperfpl/
  __main__.py        CLI: the commands above
  config.py          load and validate config.json
  db.py              SQLite schema, snapshot helpers
  http.py            GET JSON with retries and throttling
  fetch_sleeper.py   Sleeper league, users, players, rosters, trending
  sleeper_stats.py   Sleeper weekly stats (exact league points)
  fetch_fpl.py       FPL bootstrap, fixtures, player history
  fetch_markets.py   Kalshi and Polymarket quotes
  markets.py         team-code aliases, quote usability, fixture matching
  pricing.py         Poisson fit: quotes -> expected goals, win/draw, clean sheets
  scoring_map.py     scoring_map.json, coverage, FPL-based points
  matching.py        link Sleeper players to FPL players; overrides.json
  gameweek.py        which game week to plan for; free agents vs waivers
  projection.py      v1: recent average x fixture difficulty; v1m: the same with expected minutes
  projection_v2.py   v2: market-adjusted projection; v2m: with expected minutes
  availability.py    FPL/Sleeper injury labels: who's out, label adjustments, learning, label-audit
  minutes.py         expected minutes: history, features, P(start)/P(appear), minute scenarios
  minutes_fit.py     fit the start model (IRLS); minutes_coef.json is the shipped default
  lineup.py          best legal XI, swap tips, free-agent ranking
  live.py            in-week alerts and 0-minute bench swaps
  report_card.py     projection accuracy per finished game week
  report.py          assemble report.json and render index.html
  metrics.py         scoring functions shared by the report card and backtest
  backtest.py        replay past weeks to compare projection methods
  models.py          shared dataclasses
```

Data flow: fetch commands store snapshots in SQLite → `build` reads the newest
snapshots, matches players, prices fixtures from odds, projects points, picks
the XI and swaps, logs projections → writes `report.json` and `index.html`.

`projection.py` is meant as the swappable piece: a prediction model can replace
or sit beside it without touching fetchers, lineup or report code.

## Tests

```bash
python3 -m unittest
```

Tests use `unittest`; fetchers take an injectable `get=` so they can run
without the network. `tests/test_main.py` also reads `deploy/crontab.example`
(it checks that no `fetch-status` entry runs at minute 0 or 30).

## The original script, `sleeper.py`

A standalone script that predates the package and still works. The package
also imports `player_name`, `positions` and `rostered_ids` from it, so keep it at the repo
root.

```bash
python3 sleeper.py rosters      <league_id>                     # every team with real player names
python3 sleeper.py free-agents  <league_id> [-o free_agents.csv] [--all] [--include-clubless]
python3 sleeper.py player       <league_id> 1011 2047 ...       # look up player ids
python3 sleeper.py refresh      <league_id>                     # re-download the player map
python3 sleeper.py --refresh rosters <league_id>                # force re-download first
```

`free-agents` writes CSV (to stdout, or `-o FILE`) sorted by Sleeper search
rank, active clubbed players only; `--all` adds inactive players and
`--include-clubless` adds players without a club. `--refresh` is a top-level
flag and must come before the sub-command. The ~5 MB player list is cached at
`$XDG_CACHE_HOME/sleeper/players_<sport>.json` (or `~/.cache/sleeper/...`) for
24 hours. The script exits with a message on errors and does not retry.
