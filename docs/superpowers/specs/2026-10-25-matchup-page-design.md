# Matchup Page: Design

Date: 2026-10-25
Status: draft, awaiting owner approval
Mockup (approved by owner 2026-10-25: "exactly what i imagined"):
https://claude.ai/artifact/VUC4AWvBsTjf4pGva4zGf2. It is built from real GW9
data, and the page should look and read like it.

## Intent

The owner plays head-to-head in a 6-team Sleeper league and sets the lineup on
Thursday nights, on a phone, after checking this page. Today the page shows
only their own side: best XI, swap tips, free agents. They want to see the
**week's opponent** as well. That means the opponent's strongest lineup from the
players the opponent actually has, and both teams' projected points, so they
know the best case for each side before deciding on swaps. They also want to see
the starting elevens **on a pitch** when they choose, the fixtures with **day
and kickoff time**, and **club badges**. The page should look nicer overall.

Success: on Thursday night the owner opens the page on their phone and, at the
top, sees "you 78.3 vs them 81.9" with both lineups. They see what the
suggested swaps would do to that gap, and which fixtures matter to each side.

## Goals

1. **Opponent from a schedule.** The owner gives the schedule once. The page
   picks this week's opponent from it.
2. **Opponent's strongest XI.** It is picked from the opponent's own roster
   (not their IR) with the same projections and lineup rules as the owner's.
3. **Matchup card at the top.** It shows both team names, records, both
   projected totals and a bar comparing them. One line says what the suggested
   swaps would do: "With all 3 suggested swaps your XI projects 95.1, ahead
   by 13.2."
4. **Starting XIs** for both teams, with a **Pitch / List** switch. Pitch is a
   green pitch with players in rows by position; each shows a badge, short
   name and projected points. List is tables.
5. **Swap tips** stay as they are, directly under the XIs.
6. **Fixtures** for the game week. Each shows day and kickoff time in the
   viewer's own time zone, both badges, win chances, and how many of your
   starters and theirs play in it ("You 3", "Them 2").
7. **Club badges** wherever a club appears in these sections.
8. **A visual refresh of the whole page** in the mockup's style.

## Non-goals

- No projections for the week being played: during a live week the card
  shows the real score, not a forecast (see section 6).
- No schedule fetching. Sleeper's public API returns `null` for this league's
  matchups (checked 2026-10-25 for weeks 1, 7, 8 and 9).
- No other opponents' lineups and no league table.
- No change to how the owner's XI, swaps or free agents are picked.
- No local copies of badge images.

## Anti-goals (a failure even if it works)

- **A schedule problem breaks the page.** A missing schedule, a name that
  matches no team, or a week before the schedule starts must still produce the
  normal page. The matchup card is absent, or it shows a one-line notice naming
  the problem.
- **Opponent players leak into the owner's decisions.** Projecting the
  opponent's roster must not make their players appear as free agents, in
  swap tips, in "Top free agents", in Claude's second-opinion packet, or in
  the owner's XI.
- **The report card shifts.** Projection logs and report-card scores must keep
  today's population (the owner's roster and free agents), so the season's
  numbers stay comparable week to week.
- **Losing what the page shows today.** The current Best XI table (avg,
  fixtures, flags, minutes hints, market tags, bench), the Matches table's
  market details (expected goals, clean-sheet chances, sources, stale tag) and
  every other card keep their information. They are restyled, not dropped.
- **Losing dark mode.** The current page follows the phone's dark mode, and it
  must keep doing so.
- **A broken page offline.** If badge images don't load, a club-code circle
  shows instead (as in the mockup) and nothing shifts.

## Constraints

- Static page built by the existing `build` command. It runs on the owner's
  server via cron and is viewed at home, mostly on a phone. Python standard
  library only for the page. Small inline JS is fine (there's already a
  table-sort script).
- The page must work at phone width (about 390 px) with no sideways page
  scroll. Wide tables scroll inside their own box.
- Tests must not touch the network.

## How it works

### 1. The schedule (owner's input, 2026-10-25)

The owner gave the schedule as a 5-week cycle that repeats for the rest of the
season:

| GW | Opponent (team name in Sleeper) |
|----|---------------------------------|
| 9  | Machine Gun Skelly |
| 10 | 2nd again ole ole ole |
| 11 | firmala firmala |
| 12 | briantrovato (no team name set; this is the Sleeper username) |
| 13 | HORMIGOOOOOL |
| 14–31 | the same order again |
| 32–38 | playoffs: no card (owner, 2026-10-25: "leave those blank") |

**Config (my call):** a new `schedule` block in `config.json`:
`{"start_gw": 9, "end_gw": 31, "opponents": ["Machine Gun Skelly", "2nd again ole ole ole",
"firmala firmala", "briantrovato", "HORMIGOOOOOL"]}`. Game week `g` (where
`start_gw <= g <= end_gw`) faces `opponents[(g - start_gw) % len(opponents)]`. Weeks
outside that range have no opponent and no card (no notice either: it's expected). The block is optional; without it there is
no matchup card. Write the owner's values into the dev `config.json` and into
`config.example.json` as the example.

**Matching a name to a team:** the name matches a league member's Sleeper team
name (`metadata.team_name`) or, failing that, their Sleeper username
(`display_name`). It ignores case and leading or trailing spaces. The
member's `owner_id` then finds their roster. No match, or a match to the owner
themself, gives a notice on the matchup card. Team names change, so matching
happens on every build.

### 2. Data the tool must start storing

- **Team names and records.** `fetch_sleeper` stores each user's
  `metadata.team_name`, and each roster's wins, losses and ties
  (`settings.wins`, `settings.losses`, `settings.ties`). Add columns, migrated
  the way `db.py` already migrates.
- **Badge codes.** `fetch_fpl` stores each club's FPL `code` (from the
  bootstrap `teams` list; Arsenal is 3, Hull 88) in `fpl_teams`. The badge
  image is `https://resources.premierleague.com/premierleague/badges/70/t<code>.png`.
  All 20 current clubs returned HTTP 200 on 2026-10-25. Before the next
  bootstrap fetch the code is missing, and the circle fallback shows.

### 3. Projecting the opponent

Today `build_report` projects only the owner's roster and unrostered players.
It skips everyone on another team. It now also projects the week's
opponent's players, using exactly the same steps (history, market, minutes
model, flags). It keeps them apart from the owner's roster and from free
agents, so nothing in the owner's decisions changes (see the anti-goals).
Their XI comes from `best_xi` over their roster minus their reserve (IR), with
the league's slots and eligibility.

### 3b. Which week the card shows (owner, 2026-10-25)

The card follows the **week being played** until it finishes, then the next
week ("it should show the current match week ... and tomorrow match week 9
would appear"). Concretely:

- **Between weeks** (no game week in progress): the card shows the target
  week (`target_gameweek`, the same week as the XI and swap tips) with both
  strongest XIs and projections, as described above.
- **During a live week** (the same test that shows today's live card,
  `live_gameweek`): the card shows **that** week's opponent in **live mode**
  (section 6), even though the XI and swap tips below have already moved on
  to the next week. The card's heading names its game week, so the two don't
  get confused (for example "GW9 · live" above, "Best XI · GW10" below).

### 4. What `report.json` gains

A `matchup` key (null when there's no opponent this week) holding:

- both teams' names, records and projected XI totals;
- both XIs, each starter as the existing player dict plus its slot;
- the opponent's bench;
- the owner's projected total after all suggested swaps, computed by running
  `best_xi` on the post-swap roster (not by adding up the gains);
- a `notice` string when the schedule couldn't be resolved;
- `gw` and `mode` (`"projected"` or `"live"`); in live mode, the live fields
  from section 6 in place of the projections.

Each fixture in `matches` gains its badge codes and the count of each side's
starters playing in it.

### 5. The page

**Order (my call):**
1. banners (as today);
2. the live card (only while a game week is being played, as today);
3. **matchup**;
4. **starting XIs**;
5. **swap tips**;
6. **fixtures**;
7. Claude's second opinion;
8. top free agents;
9. report card;
10. unmatched.

**Matchup card.** As in the mockup: both names (yours in blue, theirs in
orange), each with its record and Sleeper username in small type (my call: the
mockup showed a record for you and a username for them, so show both for
both), both totals large, the comparison bar, a "You lead / trail by N" line,
and the swap line. Where the mockup and this text differ, this text wins,
keeping the mockup's extra pieces (my call). The mockup's "UK time" was a
stand-in for the viewer's time zone. Today's header lines ("Best XI
projected", points source, "Data as of") and the per-tip "Waiver claim:"
label stay.
With no opponent this week, the card is absent; with a schedule problem, it
shows the notice.

**Starting XIs card.** It has a Pitch / List switch. The page remembers the
last choice per phone in `localStorage`, guarded with try/catch, and defaults
to Pitch.
- **Pitch:** two pitches side by side on a wide screen, stacked on a phone.
  Rows run from forwards at the top down to the goalkeeper. A flex-slot
  player goes in the row of their **first listed position** (my call). A
  formation label (D-M-F counts, for example 3-5-2) sits above each pitch.
- **List:** for you, today's full Best XI table with the bench. For them, a
  simpler table: slot, player, club and projection, plus their bench.

**Fixtures card.** It replaces today's Matches card. Each fixture shows:
- day and time, converted to the viewer's time zone by a small script, with
  the UTC time in the HTML as the fallback;
- both badges and club names, with each side's win chance;
- "You n" and "Them n" counts.

Draw chance, expected goals, clean-sheet chances, sources and the stale tag
stay available (my call: a second line or a tap-to-expand row, builder's
choice). They must not be dropped.

**Visual refresh (my call).** Every card takes the mockup's look:
- 14 px corner radius, more padding, 17 px card headings;
- blue `#1d4ed8` for you and orange `#c2410c` for them;
- the green pitch;
- touch targets of at least 44 px.

Dark mode keeps working through the existing CSS variables, with dark-mode
versions of the new colors that keep text contrast at 4.5:1. Sorting, tables
and all existing content stay.

### 6. Live mode (my recommendation, owner agreed 2026-10-25)

During the week being played, the card shows the real situation instead of a
forecast. Everything comes from data the tool already stores:

- **Lineups:** each side's starters as actually set in Sleeper
  (`sleeper_rosters.starters_json`, newest snapshot), not the strongest XI.
- **Points so far:** each starter's league points from that week's Sleeper
  stats (`sleeper_stats`, scored with `sleeper_points`), summed per side. A
  starter whose match hasn't been played yet counts 0 and is marked "to play"
  with the kickoff time. The same freshness rule as the live card applies
  (stats fetched after the match ended).
- **Header:** "GW9 · live", both sides' points so far, and how many starters
  each side still has to play.
- The pitch / list views and fixture counts work the same way, using the set
  lineups and points so far in place of projections.
- No swap line in live mode.

When the week finishes (the live card disappears), the card switches to the
next week's opponent in projected mode.

## Left to the builder

- Function and module names, and where the matchup code lives.
- Exact CSS, as long as it matches the mockup and the rules above.
- How the fixtures card shows the market details.
- The short name used on the pitch (surname or Sleeper's last name), with
  ellipsis for long names.
- README updates: config (`schedule`), the new page sections, `report.json`
  keys, and the stored columns.

## Testing

- **Schedule:** the cycle maps GW9→first, GW13→fifth and GW14→first again.
  A week before `start_gw` or after `end_gw` (GW32) has no opponent and no card.
- **Week choice:** with a week in progress, the card shows that week's
  opponent in live mode; otherwise the target week in projected mode.
- **Live mode:** points so far add up each side's set starters' scored stats;
  unplayed starters count 0 and are marked to play; stats fetched before a
  match ended don't count. Matching ignores case and spaces,
  falls back to the username, gives a notice for no match or for the owner
  themself, and gives no card without a schedule.
- **Opponent XI:** comes only from the opponent's roster and leaves out their
  IR.
- **No leaks:** the opponent's players are absent from free agents, swap
  tips, top free agents, the second-opinion packet and projection logs.
  Free-agent lists and swaps are unchanged by adding a schedule.
- **Post-swap total:** equals `best_xi` on the post-swap roster.
- **Fetchers:** team name, record and badge code are stored. Old databases
  are migrated.
- **Page:** renders with and without a matchup and with a notice. It has
  both XI views, the badge `<img>` with its fallback, fixture counts, and
  the market details still present. The existing page tests still pass.
- **Manual:** build on dev and look at it at phone width, in light and dark
  mode.
