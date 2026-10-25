"""Injury labels: FPL's status and chance, and Sleeper's injury_status.

FPL leads. Only FPL's hard "out" (suspended, unavailable, left the club, or a
0% chance) zeroes a player; Sleeper's label never does.
"""

HARD_OUT = {"s", "u", "n", "i0", "d0"}


def chance_for(el, deadline_passed):
    """FPL's chance for the target week: "next round" means the target week only until its deadline."""
    return el.get("chance_this") if deadline_passed else el.get("chance_next")


def fpl_bucket(status, chance):
    if status in ("s", "u", "n"):
        return status
    if chance is None:
        # FPL sends no chance for unflagged players. An "i" or "d" without one hasn't been seen.
        return {"i": "i0", "d": "d50"}.get(status, "a100")
    if chance >= 100:
        return "a100"
    return ("i" if status == "i" else "d") + str(int(chance))


def slp_bucket(injury_status):
    return (injury_status or "none").lower()


def is_hard_out(bucket):
    return bucket in HARD_OUT
