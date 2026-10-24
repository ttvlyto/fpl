# Sleeper EPL rankings

Collects data from your Sleeper Premier League league and the official
Fantasy Premier League site, then builds one web page with:

- your **best starting XI** (only lineups your league's rules allow),
- **swap tips** ("Drop X, add Y: +1.9 projected"), labelled as waiver
  claims once the match week has started,
- the **top free agents** per position, with Sleeper trending adds.

Points are calculated with **your league's scoring**, using the editable
`scoring_map.json`. Design: `docs/superpowers/specs/2026-10-24-fpl-rankings-design.md`.

Python 3.10+, standard library only. Nothing to install.

## Setup

```bash
cp config.example.json config.json
```

Edit `config.json`:

- `league_id`: from your league's URL in Sleeper (`sleeper.com/leagues/<league_id>/...`).
- `owner`: your Sleeper display name (or user id).

Then run, in order:

```bash
python3 -m sleeperfpl fetch-daily            # also creates scoring_map.json the first time
python3 -m sleeperfpl fetch-rosters
python3 -m sleeperfpl fetch-history --force  # first time only; takes a few minutes
python3 -m sleeperfpl coverage               # which scoring rules FPL can't see
python3 -m sleeperfpl build                  # writes site/index.html and site/report.json
```

Open `site/index.html` in a browser.

## Things you can edit

**`config.json`**: every tunable.

| Key | Default | What it does |
|---|---|---|
| `difficulty_multipliers` | 1→1.2 … 5→0.8 | how fixture difficulty scales projections |
| `avg_window` | 5 | matches in the recent average |
| `swap_min_gain` | 1.0 | minimum gain before a swap is suggested |
| `max_swaps` | 3 | swap tips shown |
| `free_agents_per_position` | 10 | free agents listed per position |
| `slot_eligibility` | GK/DEF/MID/FWD | which positions can fill each lineup slot. If `build` names an unknown slot (a flex slot, say), add it here, e.g. `"FLEX": ["DEF", "MID", "FWD"]` |

**`scoring_map.json`**: how FPL stats turn into your league's points.
It's generated once from your league's scoring and never overwritten.

```json
{"rules": {
  "goal_def": {"fpl_field": "goals_scored", "points": 6, "positions": ["DEF"]},
  "app_60":   {"fpl_field": "minutes", "points": 2, "min": 60},
  "gc_2":     {"fpl_field": "goals_conceded", "points": -1, "per": 2},
  "shots":    {"fpl_field": null, "points": 1}
}}
```

- `min`: award the points once if the stat is at least this.
- `per`: points for every N units.
- `positions`: only apply to these positions.
- `null`: FPL has no such stat, so the rule is skipped and the page shows an "Approximate" banner.

Valid fields are listed by `coverage` errors and in `sleeperfpl/scoring_map.py`.

**`overrides.json`** (optional): fix players or clubs that don't match.

```json
{"players": {"<sleeper_player_id>": <fpl_id>}, "clubs": {"<sleeper club code>": "<FPL short name>"}}
```

## Running on the server

1. Copy the repo to `/opt/sleeper_fpl`, plus your `config.json`.
2. `mkdir -p /opt/sleeper_fpl/data`, then run the setup commands above once.
3. `crontab -e` and paste `deploy/crontab.example`.
4. Install `deploy/sleeperfpl-web.service` to serve the page at
   `http://<server>:8080/`. Keep that port closed on your router.

Logs go to `data/cron.log`. At the start of each new season, check `overrides.json`, because FPL renumbers its player ids. The database is `data/fpl.db`. It keeps every
snapshot, which is the history a future prediction model would need.

## The original script

`sleeper.py` still works on its own: `python3 sleeper.py rosters <league_id>`.

## Later additions

See "Later additions" in the design doc: a prediction model, Sleeper's own
stats, a Claude "second opinion" (it would read `site/report.json`), and
betting odds.
