# Sleeper EPL rankings

Builds a static web page for a Sleeper Premier League fantasy league, using
your league's exact scoring:

- a head-to-head matchup card (needs a `schedule` in `config.json`): you against
  this week's opponent, with projected points, and a live score while the week
  is played
- your best starting XI and drop/add swap tips
- top free agents per position
- fixtures with club badges, kickoff times in your local time zone, and match
  odds from Kalshi and Polymarket, used to adjust projections
- a live match-day section
- optionally, a second opinion from Claude on your XI and swaps
- a report card on past projections
- an expected-minutes estimate per player (chance of starting and of playing),
  so a player only Sleeper calls "Out" isn't thrown away

## Requirements

Python 3.10+. The core tool uses the standard library only and needs no API
keys. The optional Claude second opinion is the exception: it needs the
`anthropic` package (`pip install -r requirements.txt`) and an
`ANTHROPIC_API_KEY` environment variable (see below).

## How to use it

Run every command from the repo root.

Note: the repo ships a `scoring_map.json` made for the author's league. The
tool only creates one if the file is absent, so if your league's scoring
differs, delete `scoring_map.json` before your first `fetch-daily`.

### 1. One-time setup

1. Copy the example config: `cp config.example.json config.json`
2. Edit `config.json` and set the two required keys:
   - `league_id`: the number in your league's URL
     (`sleeper.com/leagues/<league_id>/...`).
   - `owner`: your Sleeper display name (or user id). This picks which roster
     is "yours".

   Both must be edited: with the placeholder `league_id` there is no config
   error, you just get an `error:` from the Sleeper fetch (a Sleeper HTTP
   error, or "League X not found or has no sport").

   Everything else has a default; see [docs/README.md](docs/README.md) for the
   full list.

   Careful: `config.example.json` is not all defaults. Its `schedule` holds the
   author's league opponents (game weeks 9-31); the code default is no
   schedule. Replace it with your league's head-to-head schedule, or delete the
   key to have no matchup card. If you leave it, the matchup card shows a
   "matches no team" notice instead of an error.

### 2. First run

In this order:

```bash
python3 -m sleeperfpl fetch-daily             # league, players, FPL data; creates scoring_map.json only if absent
python3 -m sleeperfpl fetch-rosters           # who is on which roster (needs fetch-daily first)
python3 -m sleeperfpl fetch-history --force   # first time only; takes a few minutes
python3 -m sleeperfpl coverage                # check which scoring rules FPL can't see
python3 -m sleeperfpl fetch-results           # past results for head-to-head records (optional, once)
python3 -m sleeperfpl fetch-markets           # betting odds
python3 -m sleeperfpl build                   # writes site/index.html
```

`fetch-rosters` before `fetch-daily` fetches the rosters and then fails with
"no league data yet", so keep the order.

A wrong `owner` is only caught at `build`, which fails with "No roster for
owner ... League members: ..."; copy your name exactly from that list. Until
Sleeper stats have been fetched (`fetch-history` only does this once a game week
has finished; `matchday` also fetches the live week) the page falls back to FPL
stats. It shows an "Approximate" banner only if
some scoring rules have no FPL stat or are missing from the scoring map
(`coverage` lists them).

Optional but useful once: `python3 -m sleeperfpl fetch-history --season 2025`
(use last season's year, e.g. 2025 for the 2026 season; the current season is
refused. It fetches last season's stats, so the backtest has more to work with) and
`python3 -m sleeperfpl fit-minutes` (fits the expected-minutes model on your
stored weeks; a default model ships, so this isn't required).

### 3. View the page

Open `site/index.html` in a browser (it is one HTML file with inline CSS and JS,
and club badge images load from the Premier League's site; click a table
header to sort). To open it from your phone too, serve `site/` from a
computer or server on your network; see "Run it automatically" below.

### 4. Weekly routine

Before you set your lineup (for example Thursday night), refresh and rebuild:

```bash
python3 -m sleeperfpl fetch-daily       # optional; once a day is plenty
python3 -m sleeperfpl fetch-rosters
python3 -m sleeperfpl fetch-status      # latest injury flags and news
python3 -m sleeperfpl fetch-markets     # latest odds
python3 -m sleeperfpl fetch-history     # picks up the week that just finished
python3 -m sleeperfpl build
```

Then read the page on your computer and set the lineup on your phone in the
Sleeper app. Until the game week's first kickoff, players are instant free
agents, so swap tips are direct add/drops. After the first kickoff, adds go
through waivers and the tips are labelled as waiver claims.

During a match week, run `python3 -m sleeperfpl matchday` (or `matchday --force`
outside its normal window). It refreshes rosters, injury flags, odds and live
stats and rebuilds the page, which fills in the **This week (live)** section:
starters who may not play, and bench swaps for starters who got 0 minutes. Each
player locks at his own kickoff, so a starter who didn't play can still be
swapped for a bench player whose match hasn't started.

### 5. Reading the page

- **Matchup** (only with a `schedule`): you against this week's opponent, each
  with their strongest legal lineup and projected total, plus what your
  suggested swaps would add. While that week is being played it becomes a live
  score from Sleeper's stats, with the starters each side still has to play.
  Below it, both starting XIs show on a **Pitch / List** switch.
- **Best XI and Bench:** the highest-projected lineup your league's slots
  allow. A flag next to a player explains a zero or a doubt (for example "out"
  is FPL-confirmed unavailable; "Sleeper: out" is only Sleeper's label and, in
  the default shadow mode, the player still gets a normal projection; with
  `minutes_model: "on"` it trims his start and appear chances).
- **Swap tips:** either an XI gain ("Drop X, add Y: +N projected") or a bench
  upgrade (shown as "bench upgrade", with the gain measured on the average/
  projection blend rather than "projected"). Either appears only when the gain
  is at least `swap_min_gain`.
- **Top free agents:** per position, with Sleeper trending adds.
- **Claude's second opinion** (optional): Claude's own XI and swaps, marked
  where they agree with the page. See "Second opinion" below.
- **Fixtures:** club badges, kickoff in your local time, win/draw/win chances,
  expected goals and clean-sheet chance from Kalshi and Polymarket. With a
  matchup, each fixture also shows how many of your starters and theirs play
  in it. A projection tagged `mkt` used those odds; otherwise it
  is recent average times fixture difficulty.
- **Hover a projection** to see the player's chance of starting and of
  playing, and his FPL and Sleeper labels. This appears only when a minutes
  model and Sleeper stats give a start chance, and a label is shown only if it
  isn't "a100" or "none".
- **Report card:** how past projections and your lineup choices scored.

### 6. Checking accuracy

- The page's **Report card** grades each finished week (it fills in as weeks
  finish after you have built the page at least once before kickoff).
- `python3 -m sleeperfpl backtest` replays past weeks to compare projection
  methods. Add `--minutes` to also score the minutes model, and `--season 2025`
  to pick a season (a past season's year, e.g. 2025 for the 2026 season).
- `python3 -m sleeperfpl label-audit` shows how often players with each injury
  label actually played.

The `minutes_model` config key defaults to `"shadow"`: minutes estimates are
shown and logged but don't change picks. Set it to `"on"` to let them drive the
XI and swap tips.

### 7. Second opinion (optional)

`python3 -m sleeperfpl second-opinion` asks Claude for its own XI, swaps and
per-fixture notes for the coming game week, using raw data only (recent points,
injury news, fixtures, odds, head-to-head records), never this tool's
projections. It is off by default and billed per run by Anthropic. To use it:

1. `pip install -r requirements.txt`
2. Create an API key with Anthropic and put it in the `ANTHROPIC_API_KEY`
   environment variable (never in `config.json`).
3. Set `"second_opinion": {"enabled": true}` in `config.json`.

It runs once per game week, when the first kickoff is within `lead_hours`
(default 48); `--force` asks again. `fetch-results` feeds the head-to-head
records it quotes; run it once (the cron job repeats it daily as a no-op once
seasons are stored, and it picks up a newly finished past season).

### 8. Run it automatically

To keep the page fresh without typing commands, run it on an always-on machine.
`deploy/crontab.example` holds the cron schedule (fetches plus `build`, with
extra runs on Thursday and Friday-Monday match days) and
`deploy/sleeperfpl-web.service` is a systemd unit that serves `site/` on port
8080. Install steps are in the headers of those files and in
[docs/README.md](docs/README.md#running-on-a-server). Run `python3 -m unittest`
to run the tests.

## Documentation

See [docs/README.md](docs/README.md) for every command and config key, how
projections work, the scoring map and overrides files, server deployment
(cron + systemd), the database, and the code layout.
