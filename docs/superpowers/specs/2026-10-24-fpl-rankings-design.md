# Sleeper Premier League Rankings: Design

Date: 2026-10-24
Status: approved by owner 2026-10-24

## Intent

The owner plays in a Sleeper fantasy soccer league (English Premier League) and
sets their lineup on Thursday nights on their phone. Before they do, they check
their computer for information. Right now nothing tells them:

- who has an easy or hard fixture this week (e.g. a big club expected to blow
  out a small one),
- which free agents are actually performing (the existing script sorts by
  Sleeper's `search_rank`, which is popularity, not performance),
- how many points a player averages in *their league's* scoring.

This project builds a small system that collects that data on a schedule and
shows one web page with the best lineup and the best free agents.

## Goals

1. **One web page** showing, for the upcoming game week:
   - **Best XI** from the owner's current roster: for each player, next
     opponent (home/away), fixture difficulty (color-coded), average points
     over the last 5 matches played (AVG5), and projected points (PROJ).
     Total projected points for the XI.
   - **Smart swap tips** (drop / add): find the owner's weakest roster
     players, meaning both low recent output (AVG5) and low PROJ, whether
     they're starters or bench. Pair each with a free agent who'd be a
     clear upgrade ("Drop X, add Y: +1.9 projected"). The drop candidate
     doesn't have to be in the same position as the add, as long as the
     resulting roster can still field a legal XI. *(My call: only suggest
     a swap when the gain clears a minimum threshold, tunable in one place,
     so the page doesn't churn over tiny differences.)*
   - **Pickup timing**: in this league, players are instant free agents
     until the match week officially begins, and go to waivers after that.
     Before the match week starts, tips read as instant pickups. After it
     starts, the page labels them as waiver claims.
   - **Top free agents** ranked by PROJ, with position, club, opponent, and
     Sleeper trending adds.
   Approved mockup: option B in
   `.superpowers/brainstorm/*/content/output-format.html`.
2. **Points in the league's own scoring.** Player history from FPL is
   re-scored using the league's `scoring_settings` from Sleeper.
3. **Scheduled collection** on the owner's dedicated Linux server via cron,
   into a database.
4. **Built to grow.** Adding a new data source or replacing the projection
   with a prediction model later must not require rewriting the rest.

## Non-goals (this round)

- No prediction/ML model. The v1 projection is a simple formula (below).
- No betting odds. FPL fixture difficulty is the matchup signal for v1.
- Does not make moves in Sleeper. The owner still sets lineups and makes
  claims in the Sleeper app.
- No login or public internet access. Page is reachable on the home network
  only.
- One league, one user.

## Later additions (not this round, but the design must leave room)

- **Prediction model** replacing or running alongside the v1 projection
  (see the swappable projection step and append-only snapshots).
- ~~**Sleeper per-match stats** as an alternative stats source~~ — built 2026-10-24 and now the default (`stats_source: "sleeper"`): Sleeper's weekly stats use the same keys as the league's scoring, so points are exact. FPL + scoring_map.json remain the fallback. Also added: an `undroppable` list of players never suggested as drops.
- **Claude "second opinion"**: after the scoring step, send the page's data
  (XI, swap tips, free agents, fixtures, injury flags) to Claude and show its
  notes in a section on the page. Provider is either Anthropic's API directly
  or Claude on Microsoft Foundry, chosen in the JSON config. Check whether
  web search is available on the chosen provider before relying on it for
  injury news. v1 only needs the scoring step's output to be available as
  structured data (e.g. a JSON file next to the HTML) so this can plug in.
- **Betting odds** as a sharper matchup signal than fixture difficulty.

## Anti-goals

- Suggesting a lineup the league's rules don't allow (wrong formation/slots).
- Ranking players with Sleeper popularity (`search_rank`) instead of
  performance.
- Silently dropping or mismatching players between Sleeper and FPL. Unmatched
  players must be visible, not hidden.
- Overwriting history. Data that can't be downloaded again later (rosters,
  trending, fixture difficulty at the time) must be kept.

## Existing work

`sleeper.py` (Python 3, standard library only) already:

- reads the league and its `sport` slug from `league/<id>`,
- downloads and caches `/players/<sport>` for 24h,
- lists rosters with real names,
- lists free agents (everyone not on any roster) to CSV, sorted by
  `search_rank`.

Build on this script's code and style rather than starting over. Its CLI
commands should keep working.

## Data sources

**Sleeper API** (`https://api.sleeper.app/v1`, public, no key):
- `league/<id>`: sport, `scoring_settings`, `roster_positions`, settings.
- `league/<id>/rosters`, `league/<id>/users`.
- `players/<sport>`: player map (large; fetch at most daily).
- `players/<sport>/trending/add` (and `/drop`): trending players. Confirm this
  endpoint works for the soccer sport slug. If it doesn't, leave the trend
  column empty rather than failing.
- `state/<sport>`: current week, if available for soccer.

**Official Fantasy Premier League API**
(`https://fantasy.premierleague.com/api/`, public, no key):
- `bootstrap-static/`: all players, teams, game weeks (events).
- `fixtures/`: every fixture with `team_h_difficulty` / `team_a_difficulty`
  (1–5).
- `element-summary/<id>/`: one player's match-by-match history (minutes,
  goals, assists, clean sheets, saves, cards, bonus, etc.). This is ~700
  requests per full refresh, so throttle requests and only refresh after a
  round of matches finishes.

## Architecture

```
Sleeper API ─┐                                  ┌─> scoring step ─> static web page
             ├─> fetchers (cron) ─> SQLite DB ──┤
FPL API ─────┘                                  └─> (future: prediction model)
```

- **Fetchers**: one per source, each runnable on its own. Each writes only to
  its own tables. Adding a source = adding a fetcher and its tables.
- **Database**: one SQLite file. *(Builder's call / my call: SQLite because
  it's single-user, small, and built into Python. Move to Postgres only if
  needed.)*
- **Snapshots**: anything that changes over time (rosters, trending, player
  status/injury, fixture difficulty) is stored append-only with a
  `fetched_at` timestamp. The page uses the latest snapshot; history is kept
  for future prediction work.
- **Player ID map**: one table linking each player across sources
  (`sleeper_id`, `fpl_id`, and room for future source IDs). This table is
  what lets new sources plug in.
- **Scoring step**: reads the DB, computes AVG5 and PROJ, picks the best XI,
  ranks free agents, and writes the web page. Runs after fetches. The
  projection is one swappable function/module with a clear input (player,
  upcoming fixture(s), history) and output (projected points), so a model
  can replace or run alongside it later.
- **Web page**: *(my call)* the scoring step writes a static HTML file;
  any simple web server on the dedicated server serves it on the home
  network. No live backend needed for v1. Must be readable on a phone.

### Schedule (cron on the dedicated server)

| Job | When |
|---|---|
| Sleeper players map, FPL bootstrap + fixtures | daily |
| Sleeper rosters, trending | several times a day; more often on Thursday, before the match week begins |
| FPL match histories | once after each game week's matches finish |
| Scoring step + page | after any fetch above |

Exact times are the builder's call. Development happens on the owner's WSL
dev machine; production runs on the dedicated server, so config (league ID,
DB path, output path) must not be hard-coded to either machine.

## Ranking logic (v1)

1. **Match players** Sleeper ↔ FPL by name + club + position, with a manual
   override list for cases that don't match. Report unmatched players
   (rostered ones especially) on the page or in a log, never drop silently.
2. **Check coverage first.** *(My call.)* Before building on FPL re-scoring,
   pull the league's `scoring_settings` and measure how much of it FPL's
   stats cover. Also check whether Sleeper exposes per-match soccer
   stats/points. The per-match stats source is a swappable fetcher, so the
   rest of the system doesn't depend on FPL specifically.
3. **Re-score history**: for each FPL match a player appeared in, compute
   points using the league's Sleeper `scoring_settings`. Sleeper's soccer
   scoring keys must be mapped to FPL stat fields. Stats Sleeper scores but
   FPL doesn't provide are skipped, and the page notes the projection is
   approximate.
4. **AVG5**: average of the last 5 matches with minutes > 0. *(My call:
   skipping 0-minute matches so injuries/benchings don't drag the average.)*
   Players with fewer than 5 appearances use what they have, and the page
   shows the count.
5. **PROJ**: AVG5 × a multiplier for fixture difficulty. *(My call, tunable
   in one place: difficulty 1 → 1.2, 2 → 1.1, 3 → 1.0, 4 → 0.9, 5 → 0.8.)*
   Blank game week → 0. Double game week → sum of both fixtures. Injured /
   suspended (from Sleeper or FPL status) → flagged, and 0 if ruled out.
6. **Best XI**: highest total PROJ subject to the league's
   `roster_positions`. Bench shown below.
7. **Free agents**: everyone not rostered in the league (existing logic),
   ranked by PROJ. Show top N per position. *(My call: top 10 per position.)*

## Editable scoring map (owner's decision)

How FPL stats turn into league points lives in a JSON file the owner can edit
by hand, not in code. For each Sleeper `scoring_settings` key it records which
FPL stat field it maps to (or that FPL has no equivalent), and the points
value used. Editing the file and re-running the scoring step must be enough
to change how points are calculated, with no code changes and no re-download.

The file is generated the first time from the league's `scoring_settings`,
pre-filled with the best FPL match for each key, and then left alone, so
the owner's edits are never overwritten. The coverage check (ranking step 2)
reports which keys have no FPL equivalent.

*(My call: the other tunables, meaning difficulty multipliers, the swap
threshold, and top-N per position, go in the same kind of JSON config, so
everything the owner might adjust is in one place.)*

**Sleeper's undocumented stats: "maybe."** The owner is open to it as a
fallback but hasn't committed. Build on FPL + the JSON map for v1. Keep the
per-match stats source swappable so a Sleeper-stats fetcher can be added
later. Don't build it this round.

## Left to the builder

- How to tell when the match week has begun (e.g. first kickoff of the game week from FPL fixtures, or Sleeper league/state data).

- Which roster is the owner's: a config value (Sleeper user ID or roster ID).
- Flex / multi-position slot handling within `roster_positions`.

- Database schema, table names, and file layout.
- How to line up Sleeper's week number with FPL's game week. Pick a reliable
  approach (e.g. by fixture dates).
- Name-matching method and where the manual overrides live.
- Web server choice and page styling, within the approved mockup.
- Request throttling, retries, and logging.
- Tests.
