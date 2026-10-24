# Betting-Market Odds: Design

Date: 2026-10-24
Status: draft, awaiting approval
Builds on: `docs/superpowers/specs/2026-10-24-fpl-rankings-design.md` (v1, shipped)

## Intent

v1 projects a player as recent average × an FPL 1–5 fixture-difficulty
multiplier. The owner wants betting markets to (a) show what the markets
expect for each match, and for individual players such as strikers where a
market exists, (b) compare that with our own numbers, and (c) make the
predictions better. The owner asked for honest feedback on prediction
quality. The weaknesses we agreed matter most, roughly by impact:

1. Minutes risk is ignored. Rotation and benching move points more than matchups do.
2. One difficulty multiplier is applied to every position, but a hard fixture
   hurts a defender's clean sheet far more than a midfielder's key passes.
3. FPL difficulty is coarse and slow to change.
4. A 5-match average is noisy.
5. Nothing measures whether the projections were right, so no change can be judged.

This feature goes after 2, 3 and 5 directly, and after 1 on match days.

## How the league works (from the owner)

- Each player locks **when he steps onto the pitch**, not at the start of the
  week. A starter whose Saturday match kicked off but who got **0 minutes**
  can still be replaced before Sunday's matches.
- Free-agent pickups are instant until the match week starts, then waivers.
  So on match days the only swaps that make sense are from the owner's own bench.
- The owner sets the lineup on Thursday nights, on a phone, after checking the
  page on a computer.

## What exists (recon done 2026-10-24)

| Source | Free / no key | Match markets | Player markets | Verdict |
|---|---|---|---|---|
| Kalshi `api.elections.kalshi.com/trade-api/v2` | yes | `KXEPLGAME` (W/D/L), `KXEPLTOTAL` (O/U), `KXEPLTEAMTOTAL` (team over X goals), `KXEPLBTTS`: live two-sided quotes on every listed match; game markets ~90k contracts each | `KXEPLGOAL` (1+/2+ goals), `KXEPLSOA` (score or assist), `KXEPLAST`: trade well for stars (Haaland 1+ goals ≈ 88k), but **open only ~3 h before kickoff** | Primary source |
| Polymarket gamma API, EPL series `10188` | yes | W/D/L per team (Yes/No markets), "More Markets" (spreads, O/U), Exact Score; $0.8–1.2M liquidity per match | Listed (incl. shots on target) but untraded: placeholder prices 0/1/0.5 | Second source for match markets; ignore player props unless priced and liquid |
| Novig | no public API (paid resellers only) | – | – | Out |
| FBref | blocked by a Cloudflare challenge for scripts | – | – | Out. Sleeper's own weekly stats already carry xG, xA, shots, shots on target, key passes and tackles |
| openfootball/england | yes | – | – | Out for now: FPL already supplies fixtures and exact kickoff times |

**Thursday availability (checked 2026-10-24):** both Kalshi and Polymarket already
list next game week's matches about a week ahead (GW9 markets for Oct 31–Nov 2
were open on Oct 24), so the Thursday page can use market data. Only player
markets are match-day-only.

## Goals

1. **Match view (Thursday page).** For every fixture in the target game week, show:
   - win/draw/loss probabilities and each team's expected goals,
   - each team's clean-sheet probability,
   - where the price came from (Kalshi / Polymarket / both) and how old it is.
2. **Market-aware projection.** Replace the single difficulty multiplier with
   position-aware market inputs (see "Projection v2"). Fall back to v1 for any
   fixture without usable market data, and label which one was used.
3. **History vs market comparison.** Next to each projection, show what the
   player's own history says and what the market implies, and highlight big
   disagreements, e.g. a defender with a strong clean-sheet history facing a team
   the market expects to score 2+. For strikers with a priced player market,
   compare the market's chance he scores with his historical scoring rate.
4. **Match-day runs.** About 2–3 hours before each kickoff, refresh that match's
   odds and pull player markets where Kalshi lists them. Rebuild the page with:
   - updated projections,
   - an "unlikely to play" hint for the owner's starters (see below),
   - after a match finishes: any owner starter who got 0 minutes, with the best
     **bench** replacement whose own match hasn't kicked off and who can legally
     fill that slot.
5. **Report card.** Each week, save what was projected (v1, v2) and the market's
   final prices. Once the week's stats are in, compare both with what actually
   happened, and show a small accuracy section on the page that builds up over time.

## Non-goals

- No paid data, no API keys, no account sign-ups.
- No notifications. Everything is on the same web page.
- No remote access. The owner checks the page only at home (home network), match days included, as in v1.
- No automatic lineup changes or bets. The owner acts in the Sleeper app.
- No new prediction model. v2 is still a transparent formula. The report card is
  what will justify a model later.
- No Novig, FBref or openfootball.

## Anti-goals

- **Stale prices shown as current.** Every market number carries its fetch time,
  and old ones are visibly marked.
- **A thin market overriding good data.** Markets without enough liquidity, or
  with a wide bid/ask spread, are ignored, not trusted.
- **Silent team or player mismatches** between market names and FPL/Sleeper.
  Unmatched markets are listed, like v1's Unmatched section.
- **A circular comparison.** Comparing v2 with the market it was built from proves
  nothing. The comparison is history-vs-market, and the report card judges v1, v2
  and the market against real results.
- **Overwriting history.** Odds snapshots are append-only, like v1's snapshots.

## Projection v2

Split each player's recent weekly league points into three buckets, using
Sleeper's per-week stat keys (`pos_<p>_<code>`, codes as in the scoring map):

1. **Defensive-outcome points:** clean sheet (`cs`) and goals against (`ga`).
   Replace the recent average with the market's expectation:
   `cs_points × P(clean sheet) + ga_points × E[goals conceded]`.
2. **Attacking points:** goals, assists, shots on target, key passes (`g`, `at`,
   `sot`, `kp`) and similar attacking codes. Scale the recent average by
   `team expected goals this match / team's typical expected goals`.
   *(The baseline is the builder's call: the team's season-average market xG,
   or a league average when that's missing.)*
   **Minutes risk** *(my call, addressing weakness 1)*: scale the clean-sheet
   expectation by the player's chance of playing 60+ minutes (the share of his
   recent appearances that reached 60), since clean sheets need 60 minutes.
3. **Everything else** (tackles, interceptions, cards, saves, dribbles, …):
   recent average unchanged. *(Builder's call whether saves scale with the
   opponent's expected goals.)*

Double game weeks sum over fixtures. A player with 0 minutes in his first match
of a double week is still eligible for the 0-minute replacement only if his
second match hasn't started. *(My call.)* Blank game weeks project 0. Ruled-out
players project 0, as in v1. With no usable market for a fixture, use v1 for
that fixture. v2 lives behind the same swappable `project` boundary v1 uses.

**From prices to expected goals.** Combine 1X2, total-goals and team-total prices
into each team's expected goals (λ). Remove the bookmaker margin (normalize) first. The
method is the builder's call (e.g. fit a Poisson or Dixon-Coles model to the
available prices). Clean-sheet probability comes from the same fit. When both
Kalshi and Polymarket price a match, blend them, weighted by liquidity. *(The
builder converts Kalshi contract volume and Polymarket USD liquidity onto a
comparable scale; equal weights are an acceptable start.)*

## Which week the page shows *(my call)*

v1 moves to the next game week once the current one's first match kicks off
(waiver mode). Keep that for the planning sections (Best XI, swaps, free agents,
match view). While a game week is in progress, add a **"This week (live)"** section
at the top of the same page, covering only the in-progress week: refreshed odds,
player markets, "unlikely to play" hints and 0-minute replacements. Two game-week
contexts in one report: `report.json` gets a separate `live` block.

## Match-day runs

- **Schedule.** *(Owner's agreement / my call:)* cron every 30 minutes across match
  days. The command does nothing unless a fixture kicks off within the next ~3 hours,
  or one has finished since the last run. Kickoff times come from FPL fixtures,
  which v1 already stores.
- **Player markets.** Fetch Kalshi player markets for the owner's players and the
  top free agents in matches about to start. Show the market's chance he scores
  (and score-or-assist) next to his historical rate.
- **"Unlikely to play" hint.** *(My guess at a signal; builder to validate against
  real data:)* When Kalshi has listed player markets for a match and one of the
  owner's starters in it has none while teammates do, flag him as possibly not in
  the lineup. Also use Sleeper's injury status. This is a hint, never an automatic
  swap.
- **"Owner's starters" means the lineup actually set in Sleeper** (`starters`
  from the roster), not our suggested Best XI. Each match-day run re-fetches
  rosters first. *(My call.)*
- **0-minute replacement.** After a match finishes (FPL `finished`, or kickoff +
  ~2.5 h), read that week's Sleeper stats. For each owner starter in that match
  with 0 minutes, suggest the best eligible bench player whose match hasn't kicked
  off, using the existing slot eligibility.

## Report card

- History excludes the week in progress: partial match-day stats never enter
  AVG/v2 inputs until that game week is finished. *(My call.)*
- Store every build's projections with a timestamp. The report card scores the
  last projection made before each player's kickoff. *(My call.)*

- Store per week, per player: the v1 projection, the v2 projection, and (when
  present) the market's last price before kickoff for each player market.
- After the week's Sleeper stats arrive, score them:
  - projection error (e.g. mean absolute error) for v1 and v2,
  - calibration (e.g. Brier score) for market goal probabilities vs actual goals.
- Show a running table on the page. The owner judges every future change against it.

## Constraints

- Python 3.10+, standard library only, same package and style as v1.
- New fetchers follow v1's patterns: injectable `get`, one `fetched_at` per run,
  append-only snapshots, failures never wipe old data, and a failed fetch still
  lets `build` run.
- Polite request rates. Both APIs are public; throttle them like FPL.
- Team names differ across sources ("Brighton & Hove Albion FC", "Brighton",
  `BHA`). Map them to FPL teams, with an editable override in `overrides.json`.
  Report unmapped names.
- Config-driven thresholds: minimum liquidity, maximum bid/ask spread, how old a
  price can be before it counts as stale, the match-day window.

## Left to the builder

- Exact Kalshi/Polymarket endpoints, pagination, and fields (recon above shows
  they work without keys).
- The expected-goals fitting method and the blending details.
- How to classify Sleeper stat codes into the three buckets (list them in one place).
- Database schema for odds snapshots and the report card.
- Page layout for the match view, match-day alerts and the report card, within
  v1's page style.
- Tests.
