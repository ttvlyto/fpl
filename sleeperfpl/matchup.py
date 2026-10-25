"""Head-to-head matchup: this week's opponent from the schedule, and helpers for showing both sides.

Sleeper's API has no soccer matchups, so the owner enters the schedule in config.json.
"""


def opponent_for(schedule, gw):
    """The opponent's name for game week `gw`, or None outside the schedule (or without one)."""
    if not schedule or gw is None or not schedule["start_gw"] <= gw <= schedule["end_gw"]:
        return None
    opponents = schedule["opponents"]
    return opponents[(gw - schedule["start_gw"]) % len(opponents)]


def team_label(user):
    """The Sleeper team name, else the username."""
    return user.get("team_name") or user.get("display_name") or user.get("user_id")


def _key(s):
    return (s or "").strip().casefold()


def find_opponent(name, users, rosters, my_roster_id):
    """(roster, None) for the league member called `name` (team name first, then username),
    or (None, notice) when no one matches or the name is the owner's own team."""
    want = _key(name)
    user = (next((u for u in users if _key(u.get("team_name")) == want), None)
            or next((u for u in users if _key(u.get("display_name")) == want), None))
    if user is None:
        teams = ", ".join(sorted((team_label(u) for u in users), key=str.casefold))
        return None, f'Schedule opponent "{name}" matches no team in the league. Teams: {teams}'
    roster = next((r for r in rosters if r["owner_id"] == user["user_id"]), None)
    if roster is None:
        return None, f'Schedule opponent "{name}" has no roster in the league'
    if roster["roster_id"] == my_roster_id:
        return None, f'Schedule opponent "{name}" is you; check the schedule in config.json'
    return roster, None


_ROWS = {"F": 0, "FWD": 0, "M": 1, "MID": 1, "D": 2, "DEF": 2, "GK": 3}


def pitch_row(positions):
    """Pitch row for a player, by his first listed position: 0 forwards (top) … 3 goalkeeper."""
    return _ROWS.get(positions[0] if positions else "", 1)


def formation(position_lists):
    """Defenders-midfielders-forwards, e.g. "3-5-2" (goalkeepers not counted)."""
    rows = [pitch_row(p) for p in position_lists]
    return "-".join(str(rows.count(r)) for r in (2, 1, 0))


def record_text(roster):
    """ "4–3", "4–3–1" with ties, or "–" before the record is stored."""
    wins = roster.get("wins")
    if wins is None:
        return "–"
    ties = roster.get("ties") or 0
    return f"{wins}–{roster.get('losses') or 0}" + (f"–{ties}" if ties else "")


def live_side(starters, week_stats, scoring, states):
    """Points so far for a side's set lineup during the week being played.

    `starters` is [(slot, player or None)]; `states` maps club -> "done" | "playing" | "to_play".
    A starter whose match hasn't started counts 0 and is "to_play".
    Returns ([{"slot", "pl", "points", "state"}], total, number still to play)."""
    rows, total, to_play = [], 0.0, 0
    for slot, pl in starters:
        if pl is None:
            rows.append({"slot": slot, "pl": None, "points": 0.0, "state": None})
            continue
        state = states.get(pl.club, "to_play")
        stats = week_stats.get(pl.sleeper_id)
        points = (sum(float(stats.get(k, 0) or 0) * float(v) for k, v in scoring.items())
                  if stats and state in ("done", "playing") else 0.0)
        rows.append({"slot": slot, "pl": pl, "points": points, "state": state})
        total += points
        to_play += state == "to_play"
    return rows, total, to_play
