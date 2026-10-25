"""Head-to-head records and recent form from stored results.

Past seasons come from match_results (fetch-results); this season from the
finished FPL fixtures. Teams are FPL names (fpl_teams.name).
"""

from collections import namedtuple

from .db import latest

Result = namedtuple("Result", "date home away hg ag")


def season_results(conn):
    """This season's finished FPL fixtures with scores, oldest first."""
    names = {t["team_id"]: t["name"] for t in latest(conn, "fpl_teams")}
    out = []
    for f in latest(conn, "fpl_fixtures"):
        if (f["finished"] and f["team_h_score"] is not None and f["team_a_score"] is not None
                and f["kickoff_time"] and f["team_h"] in names and f["team_a"] in names):
            out.append(Result(f["kickoff_time"][:10], names[f["team_h"]], names[f["team_a"]],
                              f["team_h_score"], f["team_a_score"]))
    return sorted(out)


def all_results(conn):
    """Stored past results plus this season's, one per (date, home, away), oldest first."""
    seen = {}
    for r in conn.execute("SELECT date, home, away, home_goals, away_goals FROM match_results"):
        seen[(r[0], r[1], r[2])] = Result(*r)
    for r in season_results(conn):
        seen[(r.date, r.home, r.away)] = r
    return sorted(seen.values())


def _winner(r):
    return r.home if r.hg > r.ag else r.away if r.ag > r.hg else None


def _winless_run(meetings, team):
    run = 0
    for r in meetings:  # newest first
        if _winner(r) == team:
            break
        run += 1
    return run


def head_to_head(results, a, b, n=10):
    """The last `n` league meetings of a and b (newest first) with wins/draws over them, and how
    many meetings in a row each side has gone without a win (over every stored meeting)."""
    meetings = sorted((r for r in results if {r.home, r.away} == {a, b}), reverse=True)
    recent = meetings[:n]
    return {"meetings": [{"date": r.date, "home": r.home, "away": r.away, "score": f"{r.hg}-{r.ag}"}
                         for r in recent],
            "a_wins": sum(1 for r in recent if _winner(r) == a),
            "draws": sum(1 for r in recent if _winner(r) is None),
            "b_wins": sum(1 for r in recent if _winner(r) == b),
            "a_winless_run": _winless_run(meetings, a),
            "b_winless_run": _winless_run(meetings, b)}


def form(results, team, n=5):
    """`team`'s last `n` results, newest first, like "W 2-1 v Chelsea (H)"."""
    out = []
    for r in sorted((r for r in results if team in (r.home, r.away)), reverse=True)[:n]:
        home = r.home == team
        mine, theirs = (r.hg, r.ag) if home else (r.ag, r.hg)
        letter = "W" if mine > theirs else "L" if mine < theirs else "D"
        out.append(f"{letter} {mine}-{theirs} v {r.away if home else r.home} ({'H' if home else 'A'})")
    return out
