# Claude Second Opinion: Design

Date: 2026-10-25
Status: approved by owner 2026-10-25
Builds on: the shipped v1 page, market odds, report card and expected-minutes work
(see the other specs in this folder).

## Intent

The owner sets their Sleeper lineup on Thursday nights, on their phone, after
checking this page. They know they lean toward big-club players and built this
tool to get a view that doesn't. The tool's own picks come from one projection
formula. The owner wants a **second, independent opinion** from Claude: its own
XI, its own drops and pickups, each with a reason, plus matchup context the
formula ignores, such as "Chelsea haven't beaten Arsenal in the league in N
meetings".

Claude **reasons for itself from raw data**. It is not shown the tool's
projections or picks (the owner: "claude should rely on its own reason and if
that doesn't work we can always tweak it"). Where the two agree, the owner can
act with more confidence. Where they differ, the owner reads the reasons and
decides.

Success: on Thursday night the page has a Claude section for the coming game
week, the owner finds the reasons useful, and the report card shows over the
season whether Claude's XI beat the tool's.

## Goals

1. **Claude's picks.** A legal starting XI from the owner's roster, up to
   `max_swaps` drop/add pairs from free agents, and a one-line reason for every
   starter, drop and add.
2. **Matchup notes.** A few short notes per relevant fixture built from
   head-to-head history, recent form and odds. Every number Claude cites must
   come from the data it was given.
3. **Head-to-head data.** Premier League results for past seasons, downloaded
   from football-data.co.uk and stored in the database. The tool computes the
   head-to-head and form figures itself and hands them to Claude.
4. **Shown on the page** as its own section, marking where Claude agrees or
   disagrees with the tool's XI and swaps.
5. **Graded.** The report card adds Claude's XI points per finished week, next
   to "your lineup", "tool XI" and "best XI", and grades Claude's swaps like the
   tool's.

## Non-goals

- No chat or follow-up questions; it is one answer per game week.
- No web search. All facts come from the database.
- The tool's own projections and picks stay unchanged.
- It never sets the lineup in Sleeper.
- It doesn't run on match-day or other hourly builds.

## Anti-goals (a failure even if it works)

- **It blocks or breaks the page.** If the key is missing, the API fails, the
  answer is invalid or the package isn't installed, `build` still produces the
  normal page. The section is just absent, or shows the last good opinion with
  its date.
- **It invents stats.** Head-to-head numbers that aren't in the data. The prompt
  forbids it, and the input carries every figure Claude may cite.
- **Big-club bias.** The prompt tells Claude to judge on minutes, points, form
  and fixtures, not reputation. This is the very bias the owner built the tool
  to counter.
- **Surprise bills.** One run per game week by default. A rerun needs `--force`.

## Constraints

- **Anthropic API on the owner's own account**, billed pay-as-you-go. A
  Claude.ai subscription doesn't cover it. The owner creates a key at
  platform.claude.com and should set a monthly spend limit there.
- **Model: `claude-opus-5-5`** (owner chose Opus). It runs with adaptive
  thinking and effort `high`. Expected cost is about $0.30 per run, so roughly
  $10–15 a season.
- **Runs on the owner's Linux server** via cron, like everything else. WSL is
  dev only.
- **The repo is standard-library only today.** This adds its first third-party
  package, `anthropic` (the official SDK), listed in a new `requirements.txt`.
  It is imported only inside the second-opinion module, so every other command
  works without it installed.
- **Secrets stay out of the repo.** The key is read from the `ANTHROPIC_API_KEY`
  environment variable, set in the server's crontab, never in `config.json`.
- Tests must not touch the network or the real API; the client is injected and
  faked.

## Prerequisite

The working tree has finished, staged but uncommitted work: sequential swap
tips and the "game week still in progress" banner. It gets committed on its own
before this project starts.

## How it works

### 1. Historical results: `fetch-results`

- New command `fetch-results [--from SEASON] [--force]` downloads
  `https://www.football-data.co.uk/mmz4281/<yyYY>/E0.csv` for each season from
  `--from` (default 2010, **builder's call** to change) to last season. It
  stores `season, date, home, away, home_goals, away_goals` in a new
  `match_results` table, keyed by `(season, date, home, away)`.
- Finished past seasons are fetched once (a `meta` marker per season).
  `--force` refetches. The current season comes from the FPL fixtures already
  stored, not from this source.
- football-data.co.uk names clubs differently from FPL ("Man United" vs
  "Man Utd"). A built-in alias table maps them to FPL `fpl_teams.name`.
  Overrides go in `overrides.json` under a new `results_teams` key, the same way
  `market_teams` works. A name that maps to no current club is stored as is,
  since relegated clubs are still valid opponents.
- Add it to the daily 03:30 cron line; it is a no-op once the seasons are
  stored.

### 2. The input packet

`second_opinion.py` builds one JSON packet for the target game week, using the
same target-week logic as `build`:

- **League rules:** starting slots and which positions fit each, scoring rules
  (from the league's `scoring_settings`), `max_swaps`, and whether adds are free
  agents or waiver claims right now.
- **Your roster.** For each player: name, club, positions, IR or undroppable
  status, FPL status, chance of playing and news text, Sleeper injury status,
  and the last 5 weeks of league points and minutes, plus season totals.
- **Free agents.** The same fields for the top 15 per position by league points
  over the last 5 weeks (raw points, not the tool's projection), plus every free
  agent on Sleeper's trending-adds list. **My call**, deliberately simple.
- **Fixtures** for the game week: kickoff, home/away, betting win/draw/loss
  chances and expected goals where the odds exist, FPL difficulty.
- **Head-to-head** per fixture: the last 10 league meetings (date, score), each
  side's wins, draws and losses in them, and how long each side has gone
  without a win against the other. Also each club's last 5 league results this
  season.
- **Not included:** the tool's projections, XI, swaps or free-agent ranking.

### 3. The call

- One Messages API request: `claude-opus-5-5`, adaptive thinking, effort
  `high`, streamed, with the server-side refusal fallback on (`fallbacks:
  "default"`). The answer is constrained to a JSON schema via structured
  outputs:
  `{xi: [{player_id, slot, reason}], swaps: [{drop_id, add_id, reason}],
  matchup_notes: [{fixture, note}], summary}`.
- **The system prompt** says to:
  - pick the XI and swaps that score the most league points this week, using
    only the packet's data;
  - judge on minutes, form, fixtures and odds, not club reputation;
  - cite only numbers that appear in the packet;
  - keep each reason to one sentence.
- **Validation in code.** Every id must exist, the XI must fit the slots
  (`best_xi` eligibility rules), drops must be on the roster and droppable, and
  adds must be free agents. If validation fails, it retries once, telling Claude
  what was wrong. If it still fails, nothing is saved and the command reports
  the error.

### 4. Command, storage and cron

- New command `second-opinion [--force]`. It runs only when due (below)
  or with `--force`. It exits 1 with `error: ...` if the key or package is missing or
  the call fails.
- New log table `second_opinion_log`: `made_at, season, gw, first_kickoff,
  model, xi_json, swaps_json, notes_json, summary, input_tokens,
  output_tokens`. Every run appends a row, and the newest row for the game week
  is the one used.
- `build` reads the newest opinion for the target game week and renders it
  every time. It never calls the API.
- **When it runs (owner agreed 2026-10-25):** once per game week, as soon as
  the target game week's first kickoff is **less than 48 hours away**. That's
  Thursday afternoon for a Saturday start, and Sunday or Monday for a midweek
  round, and no week is skipped. The 48 hours is a config value,
  `second_opinion.lead_hours`. An hourly cron line runs
  `second-opinion ; build`. `second-opinion` returns at once, with no fetch and
  no API call, unless a run is due: a target game week exists, its first
  kickoff is within `lead_hours`, and no opinion is saved for it yet. When it
  is due, it first refreshes rosters and FPL status (the same steps as
  `fetch-rosters` and `fetch-status`) so Claude sees the latest news. `--force` reruns by hand, for example after big injury news.
- **Config (my call):** a new `second_opinion` block in `config.json`:
  `{"enabled": false, "model": "claude-opus-5-5", "effort": "high", "lead_hours": 48}`. While it
  is disabled, the command does nothing.

### XI and swaps: which roster (my call)

Claude's XI is the lineup you'd set **if you followed its advice in full**: it
is picked from the roster *after* its own swaps, so a player it adds may start
and a player it drops may not. Validation checks the XI against that post-swap
roster. The prompt says so explicitly.

### 5. On the page

- **Placement (my call):** a "Claude's second opinion" card below the tool's XI
  and swap tips.
- **Contents:** the summary line, Claude's XI with each reason, its swaps with
  reasons, and the matchup notes.
- **Agreement markers:** each starter is marked "also in tool XI" or "Claude
  only", and each swap is marked if the tool suggests the same one. A header
  line counts them, for example "Agrees on 9 of 11 starters". The comparison is
  with the tool's picks **currently on the page** (my call): that's what the
  owner is looking at, and the "made at" time explains differences caused by
  later news.
- **Freshness:** it shows when the opinion was made. If any player it names has
  since left your roster or been picked up by someone else, a line says so,
  meaning roster changes since the opinion. Changes that *are* Claude's own
  swaps (its adds now on your roster, its drops gone) don't count.
- `report.json` gets a `second_opinion` key (null when there is none).

### 6. Report card

- `score_decisions` gains `claude_pts`, the real points of Claude's last XI
  saved before the week's first kickoff, and grades Claude's swaps the same way
  as the tool's. Because Claude's XI includes its own adds, `claude_pts` means
  "points if you'd followed Claude in full". The column is labelled that way.
  `tool_pts` stays as it is (my call).
- The report card's decisions table gets a "Claude XI" column. Weeks without an
  opinion show a dash.

## Left to the builder

- Module layout, function names and exact prompt wording, within the rules
  above.
- The club-name alias list for football-data.co.uk.
- The page styling, matching the existing cards.
- README updates: commands table, config, cron, data sources, outputs, known
  limitations (for example, that Claude can be wrong and its reasons aren't
  checked).

## Testing

- Unit tests with a fake client: packet building (no tool projections leak in),
  validation and the single retry, the skip-if-already-run rule, and a missing
  key or package giving a clean error.
- CSV parsing and team aliasing on a small fixture file, plus head-to-head
  figures (last 10 meetings, winless streak).
- `build` renders the section from a stored row and renders the page fine
  without one. The report card gains `claude_pts`.
- One manual live run on the dev machine with the owner's key before enabling
  it on the server.
