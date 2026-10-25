# Sleeper EPL rankings: full documentation

The short version is in the [top-level README](../README.md). This page covers
setup, every command, configuration, how projections work, deployment and the
code layout.

- [What the page shows](#what-the-page-shows)
- [Setup](#setup)
- [Commands](#commands)
- [How projections work](#how-projections-work)
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
- **swap tips** ("Drop X, add Y: +1.9 projected"), labelled as waiver claims
  once the game week has started (players are instant free agents until the
  week's first kickoff, then go through waivers);
- the **top free agents** per position, with Sleeper trending adds;
- a **Matches** table from Kalshi and Polymarket odds: win/draw/win chances,
  each team's expected goals and clean-sheet chance;
- a **This week (live)** section during a game week: starters who may not
  play, and bench swaps for starters who got 0 minutes (each player locks at
  his own kickoff, so an unused starter can still be swapped for a bench
  player whose match hasn't started);
- a **Report card**: how far off past projections were, week by week.

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

First run, in order:

```bash
python3 -m sleeperfpl fetch-daily            # also creates scoring_map.json the first time
python3 -m sleeperfpl fetch-rosters
python3 -m sleeperfpl fetch-history --force  # first time only; takes a few minutes
python3 -m sleeperfpl coverage               # which scoring rules FPL can't see
python3 -m sleeperfpl fetch-markets          # betting odds (free, no API key)
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
| `fetch-daily` | Sleeper league, users and players; FPL bootstrap (players, teams, game weeks) and fixtures. Creates `scoring_map.json` from the league's scoring if it doesn't exist. |
| `fetch-rosters` | Sleeper rosters and trending adds. |
| `fetch-history [--force]` | Sleeper weekly stats (`sleeper_stats`) plus the FPL per-match history crawl (`fpl_history`, always run regardless of `stats_source`). The Sleeper step fetches every stored-missing week up to the last finished game week and re-fetches the latest stored week on every run, so once at least one game week has finished the command is never a pure no-op (before that, it does nothing). The FPL crawl is skipped unless a new game week has finished since the last complete crawl; `--force` re-crawls. The crawl fetches every player with minutes > 0, one request per player at `fpl_request_interval`; if any player fails, the "last crawled" marker is not advanced and the next run retries. |
| `coverage` | Reads only `scoring_map.json` and the stored league (no DB writes). Lists the rules, which ones FPL can and can't cover, league scoring keys missing from the map or with different points, and makes two live probes of Sleeper's stats endpoints (`api.sleeper.app` and `api.sleeper.com`; see Data sources). |
| `fetch-markets` | Kalshi and Polymarket match odds (no player markets; those come from `matchday`). A failing source warns and writes nothing; it doesn't stop the other. |
| `matchday [--force]` | For match days. Runs only if a kickoff falls within the next `matchday_window_hours`, or a match kicked off between 2.5 and 5 hours ago (just finished; this post-match window is fixed, not configurable). `--force` runs anyway. When it runs it refreshes rosters, fixtures, Kalshi odds including player markets, Polymarket odds, live Sleeper stats for the current game week, then runs `build`. Each step that fails only prints `warning: <step> failed` and the rest continue. When not due it prints a "skipped" line, does not build, and exits 0. |
| `build` | Writes `report.json` and `index.html` to `output_dir` (via a temp file and rename) and, when it uses Sleeper stats and there is a target game week, appends to `projection_log`. Prints `wrote <dir>/index.html (GW<n>, <k> swap tips)`. |

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
The report card needs those logs, so it is empty when stats come from FPL.

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
| `matchday_window_hours` | 3 | how far ahead of a kickoff `matchday` will run. It also runs 2.5 to 5 hours after any kickoff, independent of this setting |
| `league_avg_goals` | 1.35 | expected-goals baseline when a team has no market history |
| `flag_cs_gap` | 0.25 | clean-sheet gap that earns a ⚑ |
| `flag_goal_gap` | 0.20 | goal-chance gap that earns a ⚑ |

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
     week has finished, but the Sleeper stats step runs every time);
   - `fetch-rosters` every 4 hours, and hourly 12:00–23:00 on Thursdays;
   - `fetch-markets` every 2 hours at :15;
   - `matchday` every 30 minutes Friday to Monday.
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
- **Planned, not built:** a prediction model to replace the projection step, and
  a Claude "second opinion" that reads `site/report.json`.

## Outputs

- `site/index.html`: the page.
- `site/report.json`: the same data, structured, for other tools to read. Top-level keys:
  `generated_at`, `gameweek`, `mode`, `first_kickoff`, `stats_source`,
  `sources`, `coverage`, `projection_note`, `xi`, `swaps`, `free_agents`,
  `matches`, `markets_unmatched`, `live`, `report_card`,
  `undroppable_unmatched`, `unmatched_rostered`, `unmatched_count`.

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
- `matches` entries have `market: null`, or `"ok"` with `stale`, `p_home`,
  `p_draw`, `p_away`, `lam_home`, `lam_away`, `cs_home`, `cs_away`.
- Player objects carry `sleeper_id`, `name`, `positions`, `club`, `flag`, `avg`,
  `n`, `proj`, `proj_v1`, `proj_source`, `trending_adds`, `undroppable`,
  `market_flags`, `p_goal_market`, `p_goal_hist` and `fixtures`.
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
| `fpl_elements` | `fpl_id`, `first_name`, `second_name`, `web_name`, `team_id`, `element_type`, `status`, `chance_next`, `total_minutes` |
| `fpl_teams` | `team_id`, `short_name`, `name` |
| `fpl_events` | `event_id`, `deadline_time`, `finished` |
| `fpl_fixtures` | `fixture_id`, `event`, `team_h`, `team_a`, `team_h_difficulty`, `team_a_difficulty`, `kickoff_time`, `finished` |
| `market_quotes` | `source`, `home`, `away`, `kickoff`, `market` (`1x2`/`total`/`team_total`/`btts`), `line`, `side`, `bid`, `ask`, `depth`, `ref` |
| `player_quotes` | `source`, `home`, `away`, `kickoff`, `player`, `team`, `market` (`goal1`/`goal2`/`soa`), `bid`, `ask`, `last`, `depth`, `ref` |

All snapshot tables also have `fetched_at`.

**Other tables:**

| Table | Key | Contents |
|---|---|---|
| `fpl_history` | `(season, fpl_id, fixture_id)` | per-match FPL stats (`round`, `kickoff_time`, `minutes`, `stats_json`) |
| `sleeper_stats` | `(season, week, player_id)` | Sleeper weekly stats (`stats_json`) |
| `projection_log` | none (append-only) | every build's projections (`v1`, `v2`, `p_goal_market`) for the report card |
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
  projection.py      v1: recent average x fixture difficulty
  projection_v2.py   v2: market-adjusted projection
  lineup.py          best legal XI, swap tips, free-agent ranking
  live.py            in-week alerts and 0-minute bench swaps
  report_card.py     projection accuracy per finished game week
  report.py          assemble report.json and render index.html
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

Tests use `unittest` and don't touch the network.

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
