"""The game week being played: who might not play, and 0-minute replacements.

Each player locks when he steps onto the pitch, so a starter whose match has
finished without him playing can still be swapped for a bench player whose
match hasn't started. Free agents are waiver claims by then, so bench only.
"""

from dataclasses import dataclass

from .matching import normalize_name

NO_CANDIDATE = "no bench player whose match hasn't started"


@dataclass
class Replacement:
    starter: object         # PlayerCtx
    slot: str
    bench: object           # PlayerCtx | None
    reason: str


def _eligible(player, slot, eligibility):
    return bool(set(player.positions) & set(eligibility.get(slot, [])))


def zero_minute_replacements(starters, bench, finished_teams, started_teams, minutes, eligibility):
    """`finished_teams`: clubs whose match is over (and no other match of theirs in progress).
    `started_teams`: clubs whose match has kicked off. Bench players are ranked by recent average
    (their projection is for the next game week)."""
    used, out = set(), []
    for slot, pl in starters:
        if pl is None or pl.club not in finished_teams or (minutes.get(pl.sleeper_id) or 0) > 0:
            continue
        options = [b for b in bench if b.sleeper_id not in used and not b.in_reserve
                   and b.club not in started_teams and _eligible(b, slot, eligibility)]
        best = max(options, key=lambda b: (b.avg or 0.0, b.name), default=None)
        if best:
            used.add(best.sleeper_id)
            out.append(Replacement(pl, slot, best, f"{pl.name} got 0 minutes"))
        else:
            out.append(Replacement(pl, slot, None, NO_CANDIDATE))
    return out


def unlikely_to_play(starters, player_quotes_by_team, injury):
    """{sleeper_id: reason}. A hint only: market listings usually follow announced lineups."""
    out = {}
    for _, pl in starters:
        if pl is None:
            continue
        status = injury.get(pl.sleeper_id)
        names = player_quotes_by_team.get(pl.club)
        if status:
            out[pl.sleeper_id] = f"injury: {status}"
        elif names and normalize_name(pl.name) not in names:
            out[pl.sleeper_id] = "not in market lineup"
    return out
