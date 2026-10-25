# Expected Minutes: Design

Date: 2026-10-24
Status: approved by owner 2026-10-24
Builds on: `2026-10-24-report-card-backtest-design.md` (shipped). Project 2 of
the accuracy roadmap.
Research input: "Replace hard zeros with calibrated minutes scenarios", pasted
into the session on 2026-10-24. Where this design and the research differ, this
design wins; the differences are listed in "Departures from the research".

## Intent

The owner says Sleeper's injury labels are often wrong. Many times they benched a
player Sleeper called "Out", and that player went on to score or assist.

The tool copies the mistake. `report._flag` marks a player "out" when **any one**
source says so (Sleeper `injury_status == "Out"`, FPL status i/s/u/n, or FPL
chance 0), and `projection.project` then sets his projection to **0**, which also
removes him from the tool's XI, the swap tips and the free-agent list.

The latest fetch (2026-10-24) shows the size of it. Sleeper marks 111 players
"Out". 96 match an FPL player, and FPL disagrees for 35 of them:

| FPL says | Sleeper "Out" players |
|---|---|
| Fully available (`a`, chance null or 100) | 12 |
| 75% (`d75`) | 18 |
| 50% / 25% | 5 |
| Injured, 0% (`i0`) | 60 |
| Not in squad (`u`) | 1 |

There's a second, quieter problem. v1 averages points over weeks the player
**played**, so a rotation player who plays half the weeks is projected as if he
always plays. The backtest's MAE counts zeros, so that error is visible there.

This project replaces the yes/no "out" with two probabilities per fixture: will
he **start**, and will he **appear at all**. It turns those into a spread of
likely minutes, and feeds that into both projections.

## Goals

1. **Stop zeroing players only Sleeper calls "Out".** Live: only FPL's hard
   "out" zeroes. In the shadow model, labels become probability adjustments. FPL leads, Sleeper is a small modifier, and FPL's hard "out"
   (`s`, `u`, `n`, or `i` with chance 0) still means 0.
2. **A start/appearance model** for every player, from his recent weeks.
3. **Minutes-aware projections**, `v1m` and `v2m`, beside `v1` and `v2`.
4. **Measure it**: the backtest scores the minutes model and `v1m` against v1;
   the report card grades `v1m`/`v2m` and the minutes model on live weeks.
5. **Start building the label record.** Store enough FPL/Sleeper label history
   that "how often does an 'Out' player play?" can be answered from this league's
   own data in a few weeks, and let the label table learn from it.

## Non-goals

- Returns from injury, manager changes, double-week rotation discounts and
  European-midweek rest. Deferred to a later project (research milestone M5,
  except the short-history fallback below, which the model needs).
- Scraping predicted or confirmed lineups (research M6). Deferred.
- Shrunk per-90 rates, xG/xA. That's project 3. This project uses plain recent
  rates.
- Price, ownership or "selected by %" as model inputs. Ownership encodes the
  big-club bias the owner wants removed, and price isn't available for last
  season's Sleeper data.

## Anti-goals

- **Never zero a player on Sleeper's word alone.**
- **Never let new numbers change the owner's picks before the backtest says they
  help.** Goal 1 changes picks only by removing false zeros.
- No new paid or keyed data source. No downloads beyond what the app already
  fetches.

## Constraints (from the project)

- Python 3.10+, standard library only. `unittest`, tests never touch the network.
- Runs from cron on the owner's server; each step must stay seconds, not minutes.
- Sleeper's `/players` endpoint should be called at most once a day (Sleeper's
  docs). It stays in `fetch-daily`.
- Unknown config keys are ignored; partial dicts merge with defaults (existing
  convention).

## Facts checked against the owner's data

- **League scoring has no appearance points** (`min` scores 0 at every position)
  and no 60-minute appearance bonus.
- **Clean sheets need 60 minutes.** In stored Sleeper stats, every `cs` row has
  60+ minutes (485 rows; none under 60). `cs` is worth GK 8, DEF 6, MID 1, FWD 0.
  `hcs` (1 point) and `cs90` (0 points) also exist; `hcs` appeared once under 60.
- **Sleeper's weekly stats carry starts and unused weeks.** Each player row has
  `min`, `gs` (started) and `gp`. Week 5 of 2026 has 400 player rows, 98 of them
  with 0 minutes. This means the start model can train and be tested on Sleeper
  data alone, including last season once `fetch-history --season 2025` has run,
  with no outside dataset.
- `fpl_history.stats_json` has `starts`, `minutes` and `value`, and includes
  0-minute matches.
- The database holds **one** snapshot of FPL elements and of Sleeper players
  (2026-10-24T19:06:23Z). Label history begins now; nothing can be replayed.

## The model

### Match states

For each player, each **regular** past week (exactly one fixture for his club;
in past seasons, the `TEAM_` check the backtest already uses) is one state:

- **S** started (`gs` ≥ 1)
- **B** came off the bench (not started, `min` > 0)
- **U** no minutes, including no stats row once he has appeared in the season
- **X** flagged absent: the FPL snapshot before that week's first kickoff had
  hard-out status. Only identifiable for weeks after label snapshots began.
  X weeks are left out of the model's history; the label layer handles them.

Weeks before a player's first row of the season aren't history (he wasn't in the
league yet).

### P(start | available): logistic regression

Ten features from the research's tested set, minus price and rest days (neither
exists for last season's Sleeper data; the research found both nearly worthless):

`ewma3` and `ewma10` of starts (half-lives 3 and 10 weeks), `prev_st`,
`prev_sub`, `last3` start share, `prev_m90` (last week's minutes / 90),
`zero_streak` (consecutive 0-minute weeks, capped at 5, scaled to 0–1), and
GK / DEF / FWD indicators.

A player needs at least 3 non-X weeks of history in the season to be modelled;
otherwise see "Short history".

Fit by Newton's method (IRLS) with a small ridge penalty on standardised
features, in plain Python. The fit is stored in a new `minutes_model` table
(version, fitted_at, rows, seasons and weeks used, weights, means, scales, and
the holdout score).

- `fit-minutes` (new command) fits on every stored regular week and saves a new
  version. The build uses the newest version.
- A default fit ships in the repo as `sleeperfpl/minutes_coef.json`, fitted from
  2025 Sleeper stats during this project, so a fresh database still works. *(My
  call: this needs `fetch-history --season 2025` to run once on the dev machine.
  It's a call to Sleeper's public stats endpoint the app already uses.)*
- Cron runs `fit-minutes` after the weekly history fetch.

### P(sub | didn't start)

The player's share of non-starts (last 10 non-X weeks) in which he came on,
shrunk with 5 pseudo-weeks toward a position prior. The prior is that same share
measured from stored Sleeper weeks for the position, recomputed at fit time and
stored with the model *(my call: the research's priors came from FPL data;
measuring them on Sleeper data keeps one source)*.

### Minutes given each scenario

Starts and sub appearances each get a small table of minutes values with
probabilities, measured per position from stored Sleeper weeks at fit time.
Starts use buckets 90 / 75–89 / 60–74 / under 60. Subs use a handful of buckets
up to 60+. The research's FPL tables are a sanity check, not the source.

A player's own start buckets are his last 10 starts' share of 90-minute and
under-60 starts, shrunk with 10 pseudo-starts toward his position. This catches
players routinely subbed at 60–70.

### Short history

A player with fewer than 3 non-X weeks this season gets
P(start) = (starts + 3·p0) / (weeks + 3), where p0 is his start share last
season if stored (any club), else his position's start rate among short-history
players. *(My call: last season's Sleeper data has no club, so "same club" can't
be checked.)*

### Per fixture

For each of the player's fixtures this game week:

```
p_start, p_app  → after the label layer, with p_app ≥ p_start
scenarios       = start buckets × p_start  +  sub buckets × (p_app − p_start)  +  0 minutes × (1 − p_app)
P(1+)  = p_app
P(60+) = mass of scenarios with minutes ≥ 60
E[min] = Σ minutes × probability
```

A double week runs the model per fixture. The second fixture uses the same
inputs as the first *(my call; the research's "evaluate twice" refinement is
deferred with the other double-week work)*.

## The label layer

### Buckets

**Which chance field.** FPL's `chance_of_playing_next_round` means the next
game week only until that week's deadline passes; after it, the current week's
figure is `chance_of_playing_this_round`. The bucket reads `chance_this` when
the target game week is FPL's current event and `chance_next` when it is later
*(my call)*. Today's `_flag` reads `chance_next` regardless, which can apply next
week's doubt to this week once the deadline passes; this project fixes that too.

- FPL: `s`, `u`, `n`; otherwise `a100` if chance is null or 100 (**null is never
  0**); else `d` or `i` plus the chance, e.g. `d75`, `i25`, `i0`.
- Sleeper: `injury_status` lowercased, or `none`. Stored values seen so far:
  `Out`, `GTD`, `Sus`.

### Rule

1. FPL `s`, `u`, `n`, or `i0`/`d0` → p_start = p_app = 0. This matches what
   "out" means today, minus Sleeper.
2. Otherwise multiply p_start and p_app by the FPL bucket's relative risks,
   then by the Sleeper modifier for (FPL bucket, Sleeper bucket).

Starting values come from the research's prior table, all marked as guesses
there:

| FPL bucket | RR start | RR appear |
|---|---|---|
| a100 | 1.00 | 1.00 |
| d75 / i75 | 0.63 / 0.57 | 0.82 / 0.76 |
| d50 / i50 | 0.29 / 0.23 | 0.43 / 0.38 |
| d25 / i25 | 0.09 / 0.06 | 0.16 / 0.11 |

| (FPL, Sleeper) | start | appear |
|---|---|---|
| a100 + out | 0.80 | 0.95 |
| a100 + gtd | 0.90 | 1.00 |
| flagged + out | 0.90 | 0.95 |
| a100 + sus | 0.50 | 0.50 *(my call; Sleeper `Sus` is not in the research)* |
| anything else | 1.00 | 1.00 |

`sleeper_label_weight` (config, default 1.0) scales how far Sleeper's modifiers
move from 1; 0 ignores Sleeper.

**Staleness.** If FPL's `news_added` is older than the club's last kickoff and
the bucket is a short-term doubt (`d75`, `d50`, `i75`, `i50`), move the FPL
relative risks halfway back to 1. If FPL is stale and unflagged while Sleeper
says "Out", cap Sleeper's start modifier at 0.60 (Sleeper may be newer). When
`news_added` is null, treat the label as fresh.

### Learning from this league

The tables hold relative risks that multiply the model's output, so they learn
as **observed over expected** *(my call)*. Each build logs the model's own
p_start and p_app before labels (`p_start_model`, `p_app_model` in
`projection_log`). When a game week finishes, every player-fixture with a label
snapshot from before his kickoff and a logged model probability adds to its
cell: O = starts (or appearances) that happened, E = the sum of the probabilities
the previous layer gave those players. For FPL buckets, E is the model's p; for
Sleeper cells, E is the model's p times the FPL relative risk already applied.
The rate used is RR = (O + k × RR_prior) / (E + k), with k = `label_prior_k`
(default 20) and RR capped to 0–1.2. "Anything else = 1.00" cells and the hard-out
zeros are fixed, not learned. FPL buckets pool `d` and `i` at the same chance
until each has 30 rows.

`label-audit` (new command) prints, per FPL bucket, per Sleeper bucket and per
cross cell: n, start rate, appearance rate, 60+ rate, and 90% Wilson intervals.
The cell the owner cares about is **FPL a100 + Sleeper Out**.

The as-of label for a fixture is the newest snapshot at or before that player's
kickoff. If that snapshot is more than 72 hours older than kickoff, the row
isn't counted.

## What goes live, and what waits

| Part | Drives picks? |
|---|---|
| Hard-out rule replacing today's "out" (goal 1) | **Yes, from day one** (owner's choice, 2026-10-24) |
| Label relative risks and Sleeper modifiers | No. Shadow only, inside `p_start`/`p_app`, `v1m`, `v2m` |
| Minutes model, `v1m`, `v2m` | No. Computed, logged and shown as information only, until the owner sets `minutes_model: "on"` |

**Goal 1 live, exactly** (owner chose this over discounting doubtful players
now): `_flag` returns "out" only for FPL hard-out (`s`, `u`, `n`, or chance 0 in
the right chance field). Sleeper's label never makes a player "out". Everything
downstream that reads "out" today (`projection.project`, `_apply_market`,
`lineup.py`, `live.py`) keeps reading it unchanged. A player only Sleeper calls
"Out" gets ordinary v1/v2. Doubtful players' live numbers are the same as today.
FPL `i` with chance above 0 is not "out" *(follows from the rule; today `i` alone
is out)*.

`minutes_model` (config): `"shadow"` (default) or `"on"`. With `"on"`, the page,
XI, swaps and free agents use `v1m`/`v2m` in place of `v1`/`v2`.

Suggested rule for switching on (from the research; the owner decides): in the
backtest, `v1m` has lower all-players MAE than v1 with a 95% interval on the
weekly difference that excludes 0, and its average rank correlation is no more
than 0.01 worse.

## Projections

Points split by Sleeper stat code into two classes:

- **G60**, counted only in scenarios of 60+ minutes: `cs`.
- **LIN**, proportional to minutes: everything else, including `ga` (conceded
  while on the pitch) and `hcs`.

The class table lives in code. An unknown code counts as LIN and logs a warning
*(my call; the research said fail, but a cron build shouldn't stop over one new
key)*.

**v1m** for a fixture with difficulty multiplier `mult`:

```
lin90   = Σ LIN points / Σ minutes × 90   over his last 5 played weeks (ratio of sums)
cs60    = average cs points over those weeks with 60+ minutes (0 if none)
v1m     = mult × Σ over scenarios: prob × (lin90 × minutes/90 + [minutes ≥ 60] × cs60)
```

Summed over the week's fixtures. This is v1 with the gate replaced, so the
backtest isolates the minutes change.

**v2m** is v2 with three swaps: `p60` becomes the fixture's P(60+); the
goals-against term is scaled by E[min]/90; and the attacking and other buckets
become per-90 rates (ratio of sums, as above) times E[min]/90.

## Storage

- `projection_log` also gains `p_start_model` and `p_app_model` (before labels),
  which the label layer learns from.
- `fpl_elements` gains `news`, `news_added`, `chance_this` (FPL's
  `chance_of_playing_this_round`).
- `projection_log` gains `v1m`, `v2m`, `p_start`, `p_app`, `p60`, `e_min`,
  `fpl_bucket`, `slp_bucket`.
- New `minutes_model` table (above). New `label_counts` table holding O and E per
  cell and target (start, appear), rebuilt from snapshots and logs, not appended.
- Migrations use the existing `_add_missing_columns`.
- `report.json` player objects gain `p_start`, `p_app`, `p60`, `e_min`,
  `fpl_bucket`, `slp_bucket`, `proj_v1m`, `proj_v2m`.

## Fetching

- New `fetch-status`: one FPL bootstrap request writing a new `fpl_elements`
  batch. Added to `deploy/crontab.example` every 4 hours, and hourly on Thursday
  and Friday afternoons, when press-conference news lands.
- `matchday` also runs `fetch-status` in its pre-kickoff window, so the label
  snapshot nearest kickoff is fresh.
- Sleeper `/players` stays once daily.

## Measuring

### Backtest (`backtest --minutes`, plus a new method)

- `v1m` joins `METHODS`, using the minutes model with no labels (none can be
  replayed). Its usual rows show whether minutes help v1.
- `--minutes` adds a minutes section per season: Brier score for P(start),
  P(1+) and P(60+); the same for two baselines (started last week, clipped to
  0.05–0.95; and (starts in last 5 + 0.5) / 6); and a 10-bin reliability table
  for P(start). Population: players with a start in their last 10 weeks.
- **No look-ahead.** For week g, the model is fit only on weeks before g of the
  same season (and earlier seasons). Weeks with fewer than 1,500 training rows
  use the shipped default fit, if it came from an earlier season, or are skipped.
  *(My call.)*
- **Paired tests.** For `v1m` vs v1, per-week differences in MAE and rank
  correlation, with a paired t statistic, a sign-flip permutation p-value
  (fixed seed), and the weekly-difference 95% interval. Added to `metrics.py`.
- **How much error is minutes?** For each player-week, `v1m` recomputed with his
  actual minutes as a certain scenario. MAE(`v1m`) minus MAE(oracle) is the share
  of error due to minutes. Printed per season.

### Report card (live)

- `v1m` and `v2m` miss and order, paired with v1 and v2 on the same players, as
  the existing v2 column is.
- A minutes row per finished week: Brier for P(start) and P(1+), over logged
  players.
- A labels table: the `label-audit` numbers for buckets with any data, so the
  owner can see how often Sleeper-"Out" players played.

## Page

In shadow mode, the projection's hover text adds "starts: 85% · plays: 92%" and
the FPL/Sleeper labels, marked as an estimate. A player only Sleeper calls "Out"
shows a flag "Sleeper: out" instead of "out". *(My call: the owner decides lineups
by hand; seeing the estimate is the point.)*

## Departures from the research

- No vaastav download. Sleeper's own weekly stats carry starts and minutes, so
  the model trains on the league's real data source. The research's 0.142 Brier
  is not expected to carry over exactly; the backtest measures our own.
- Price and rest-day features dropped (unavailable for 2025 Sleeper data;
  near-zero value in the research's ablation). `now_cost` isn't stored.
- No separate `evalstats.py`: the new measures go in the existing `metrics.py`.
- No extended FPL history crawl: the model reads Sleeper stats.
- Scope stops at research M4 plus the short-history case.
- No XI-errors count and no team-sum check (both optional in the research).

## Left to the builder

- Exact minute bucket edges for sub appearances, and the ridge strength.
- The order of work, provided each step leaves the suite green and `build`
  output unchanged in shadow mode except the page additions above.
- How `label_counts` is rebuilt (on build, or in `fetch-status`), as long as it
  only counts finished fixtures.
