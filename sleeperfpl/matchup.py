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
