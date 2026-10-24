# Report Card and Backtest: Design

Date: 2026-10-24
Status: approved by owner 2026-10-24
Builds on: `2026-10-24-fpl-rankings-design.md` (v1) and `2026-10-24-market-odds-design.md` (v2), both shipped.

## Intent

The owner does their own research before setting a lineup, and knows they lean
heavily toward big-club players. They're building this tool to get a view that
doesn't share that bias. Before that view can be trusted, it has to show when
it's right.

A research review (pasted into the session on 2026-10-24) proposed a set of
accuracy improvements. The owner agreed to this order, one project at a time:

1. **This project:** a report card that measures what lineup decisions depend
   on, plus a backtest so nobody has to wait weeks for evidence.
2. Expected-minutes model.
3. Per-90 rates with xG/xA and shrinkage.
4. Later, smaller: fitted team ratings for matches without odds, Dixon-Coles in
   the odds fit. Confirmed lineups and tree models are skipped for now.

Projects 2 and 3 each add a parameter (decay half-life, shrinkage strength) and
make a claim ("this is better"). This project is the instrument that settles
both. It changes no projection.

## What's wrong with today's report card

`report_card.py` compares each logged player's last pre-kickoff projection with
his real league points and reports mean absolute error (MAE) for v1 and v2, plus
a Brier score for market goal odds. It has four problems:

1. **It skips every player who got 0 minutes** ("didn't play: not a projection
   miss"). That hides exactly the error the minutes model is meant to fix. A
   projection of 8 for a player who sat out is a miss the owner pays for.
2. **MAE is dominated by big hauls** and says nothing about ordering. Picking an
   XI, a swap or a free agent depends only on whether players are ranked in the
   right order.
3. **It doesn't grade decisions.** Nothing says whether the suggested XI or swaps
   beat the alternatives, or whether the tool beats the owner's own picks.
4. **It needs logs, and there are almost none.** `projection_log` has 183 rows
   from three builds, all on 2026-10-24, for one game week. Measuring anything
   this way would take a whole season.

## Goals

### 1. Score zeros

Every logged player counts. A player with no Sleeper stats row, or 0 minutes,
scores whatever the league's scoring gives him for those stats (normally 0).
Report MAE two ways: over everyone (new, the headline number) and over players
who played (today's number, kept so old weeks stay comparable).

### 2. Error by outcome category

Split each week's error into four groups by what actually happened:
**didn't play** (0 minutes), **low**, **mid**, and **haul**. This shows whether a
method misses because of minutes or because of output once on the pitch.

The research's FPL cutoffs (≤2, 3–4, 5+) don't fit this league. League points
are on a bigger scale: among played weeks in GW1–7, the median is 4.0, the 75th
percentile 9.5 and the 90th 15.0. *(My call: defaults of low < 3, mid 3–10,
haul ≥ 10 league points, set in `config.json`. The builder may adjust the
defaults after looking at the distribution, as long as each group has a
meaningful share.)*

### 3. Rank correlation

Per game week and per position (GK, DEF, MID, FWD), the Spearman rank
correlation between projected and actual points, over the players in the sample.
Report the per-week average across positions, and a season average. Positions
with fewer than ~5 players in a week are skipped that week *(my call)*.

### 4. Decision metrics (live logs only)

These need to know what the tool recommended and what the owner's roster was at
the time, so they come only from live builds, not the backtest.

- **Your lineup vs the tool vs hindsight.** For each finished week: real points
  of (a) the starters the owner actually set in Sleeper, (b) the tool's
  recommended XI, and (c) the best legal XI from the same roster, chosen with
  hindsight using `lineup.best_xi` on actual points. Show (b − a) and (c − b).
  (b − a) is the direct answer to the owner's question: does following the tool
  beat their own picks?
- **Swap outcomes.** For each swap tip shown: real points of the player added
  minus the player dropped that week. Shown whether or not the owner made the swap.

Each build logs its recommendation (the XI by slot, and the swap tips) next to
its projections. The week's recommendation is the last one logged before the
game week's first kickoff *(my call: per-player locks make later builds a mix of
locked and unlocked choices; the Thursday decision is the one that matters)*.
The owner's set lineup is the `starters` of the newest `sleeper_rosters` snapshot
before that same first kickoff. *(Limitation, accepted: a lineup change made
after the last snapshot before kickoff is missed. Rosters are fetched hourly on
Thursdays.)*

### 5. FPL's `ep_next` as a free baseline

Add `ep_next` to the `fpl_elements` snapshot and log it in `projection_log` next
to `v1` and `v2`. It's in FPL points, not league points, so it is **graded only
on rank correlation**, never MAE *(my call)*.

### 6. Match report card

Nothing grades the match side today.

- Add `team_h_score` and `team_a_score` to `fpl_fixtures` (the FPL fixtures feed
  has them).
- New `match_log` table, written by every build for each target-week fixture
  with usable odds: fixture id, `made_at`, kickoff, how the numbers were made
  (`market` today; later `blend` or `rating`), `lam_home`, `lam_away`, `p_home`,
  `p_draw`, `p_away`, `cs_home`, `cs_away`, number of quotes used and their
  average spread.
- For each finished fixture, use the last entry before kickoff and score
  **log loss** (primary) and **RPS** on win/draw/loss, and **Brier** on each
  side's clean sheet. Show a per-week row with the fixture count.

### 7. Backtest command

New CLI command, `python3 -m sleeperfpl backtest`. It replays finished game
weeks using only data from before each week, and prints the metrics from goals
1–3 for each method side by side. Its purpose is comparing methods, now and in
the next two projects.

- **Point in time.** For week *g*, a player's inputs are his Sleeper stats from
  weeks before *g* only. Nothing from week *g* or later enters.
- **Seasons are replayed separately.** History never crosses a season boundary,
  matching live v1, which only reads the current season. Each season's results
  are reported on their own and never pooled into one count *(my call)*. Week 1
  of each season has no history and is not scored. A future method that wants
  last season's data (per-90 may) can read it explicitly, but that's a choice
  that method makes, not the default.
- **Who's scored.** Every player with a played week in the 5 weeks before *g*
  *(my call)*. This drops players who left the league, were loaned out or
  stopped playing, without using any week-*g* information. Since that's far more
  than the live log's ~60 players, the backtest also reports the metrics
  restricted to players who appear in `projection_log` for that week, when there
  are any.
- **Methods.** A method is a function from (player's prior weekly stats, his
  week-*g* fixtures, scoring) to projected league points. Methods are listed in
  one place so a new one is a one-line addition. Initial methods: `v1` (calling
  the real `projection.project`, not a copy) and `season_avg` (mean of all prior
  played weeks, as a naive reference). The minutes and per-90 projects add theirs.
- **Paired comparison.** Methods are always compared on the same player-weeks.
  **The reference is `v1`** (it's what the page uses today; `season_avg` is only
  a sanity floor). For each method and season the summary gives two counts:
  weeks it beat v1 on average rank correlation, and weeks it beat v1 on
  all-players MAE. It also gives the season averages of both. Rank correlation
  is listed first because XI, swap and free-agent picks depend only on order
  *(my call)*. Neither count alone declares a winner; the owner reads both.
- **What it can't replay**, stated in its output: v2 and market numbers (no
  historical odds were stored), injury and status flags (no point-in-time
  snapshots), and the decision metrics (no historical rosters or
  recommendations). Fixture difficulty comes from the newest `fpl_fixtures`
  snapshot, which may differ slightly from what was published at the time;
  this is a small, accepted leak.
- **Output.** A readable table on stdout. `--json PATH` also writes the full
  results *(my call)*. The backtest doesn't appear on the web page and doesn't
  write to the database apart from the backfill below.

### 8. Last season's Sleeper stats

The stats endpoint serves last season (checked 2026-10-24:
`stats/clubsoccer:epl/regular/2025/10` returned 420 players). Fetch season 2025's
weeks once into `sleeper_stats` (for example `fetch-history --season 2025`).
That gives the backtest about 38 more weeks, and the per-90 project gets a
longer history. *(My call; cheap and stdlib-only.)* FPL's API only serves the
current season's fixtures, so 2025 has no difficulty data. In 2025 weeks, v1
runs with a neutral multiplier and the output says so. The number of matches a
club plays in a 2025 week (blank, single or double) is read from that week's
stats: the most `gp` any of its players recorded, and 0 if none of its players
has a row *(my call)*. That isn't a leak, because the fixture schedule is
public in advance. Only an individual player's own week-*g* stats would leak.

## Page changes

The Report card section stays compact for phone width. Per finished week:
players scored, MAE all/played for v1 (and v2 where present), the average rank
correlation, and, once logged, your lineup / tool XI / best XI points. The
category breakdown, swap outcomes and match scores go in `report.json`, with a
short collapsed or second table on the page *(layout is the builder's call)*.
The summary line names the comparison it makes. While decision metrics have
fewer than 2 weeks, it keeps today's v2-vs-v1 count. After that, it reports how
many weeks the tool's XI beat the owner's set lineup *(my call)*. Every new number gets a
plain-words explanation under the table, as today.

## Non-goals

- No changes to how projections are made. That starts in the next project.
- No parameter tuning or grid search yet. The backtest only has to make that
  possible.
- No new dependencies, API keys or paid data.
- No pruning of snapshot tables. The point-in-time history is what makes leak-free
  testing possible.

## Anti-goals

- **Future data leaking into the backtest.** Any week-*g* input that uses data from
  *g* or later makes every result worthless.
- **Comparing methods on different players.** Comparisons are always paired.
- **Claims from one week.** The page and backtest never call a method better from
  a single week's result; they show counts of weeks won.
- **Breaking the existing card or old logs.** Existing `projection_log` rows still
  score, and existing databases migrate in place.

## Constraints

- Python 3.10+, standard library only, same package and style.
- `db.py` creates tables with `CREATE TABLE IF NOT EXISTS`, so new columns on
  existing tables (`fpl_elements.ep_next`, `fpl_fixtures` scores,
  `projection_log` additions) need an in-place migration for the server's
  existing database.
- Tests use `unittest` and never touch the network.
- Sleeper's week number is still treated as the FPL game week.

## Left to the builder

- Table and column names, and how the recommendation is logged (columns on
  `projection_log` or a new table).
- Exact Spearman tie handling, and RPS/log-loss details (for example clamping
  probabilities away from 0).
- How `season` is passed to the 2025 backfill and the backtest, and how 2025
  players are matched to positions (the Sleeper player map's positions are fine).
- Page layout within the current style.
- Tests.
