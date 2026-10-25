"""Injury labels: FPL's status and chance, and Sleeper's injury_status.

FPL leads. Only FPL's hard "out" (suspended, unavailable, left the club, or a
0% chance) zeroes a player; Sleeper's label never does.

In the minutes model, labels are relative risks: they multiply the model's
P(start) and P(appear). The starting values are guesses from the research
(nobody has published how often flagged players play); they learn from this
league's own outcomes as weeks finish.
"""

from .gameweek import parse_time

HARD_OUT = {"s", "u", "n", "i0", "d0"}
# (relative risk of starting, of appearing) against an unflagged player.
FPL_PRIOR = {"a100": (1.0, 1.0), "d75": (0.63, 0.82), "i75": (0.57, 0.76), "d50": (0.29, 0.43),
             "i50": (0.23, 0.38), "d25": (0.09, 0.16), "i25": (0.06, 0.11)}
# Sleeper's label on top of FPL's: keyed by (FPL side, Sleeper bucket); FPL side is a100 or "flagged".
SLP_PRIOR = {("a100", "out"): (0.80, 0.95), ("a100", "gtd"): (0.90, 1.00), ("flagged", "out"): (0.90, 0.95),
             ("a100", "sus"): (0.50, 0.50)}
SHORT_DOUBTS = {"d75", "d50", "i75", "i50"}   # labels that describe one match; a long injury stays flagged
STALE_SLEEPER_CAP = 0.60


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


def fpl_side(bucket):
    return "a100" if bucket == "a100" else "flagged"


def _nearest(bucket):
    """A chance FPL doesn't usually use (e.g. d10) borrows the nearest level's prior."""
    kind, chance = bucket[0], int(bucket[1:])
    level = min((25, 50, 75), key=lambda c: abs(c - chance))
    return FPL_PRIOR.get(f"{kind}{level}") or FPL_PRIOR[f"d{level}"]


class LabelTable:
    """Relative risks per label; `counts` (learned outcomes) are folded in by later code."""

    def __init__(self, counts=None, k=20):
        self.counts = counts or {}
        self.k = k

    def prior_fpl(self, bucket):
        return FPL_PRIOR.get(bucket) or _nearest(bucket)

    def rr_fpl(self, bucket):
        return self.prior_fpl(bucket)

    def rr_slp(self, fpl_bucket_, slp):
        return SLP_PRIOR.get((fpl_side(fpl_bucket_), slp), (1.0, 1.0))


def fpl_stale(news_added, club_last_kickoff):
    """FPL's news predates his club's last match, so it may describe that match, not the next."""
    news, last = parse_time(news_added), parse_time(club_last_kickoff)
    return bool(news and last and news < last)


def apply_labels(p_start, p_app, fpl_b, slp_b, stale, table, weight):
    """(p_start after FPL, p_app after FPL, final p_start, final p_app)."""
    if is_hard_out(fpl_b):
        return (0.0, 0.0, 0.0, 0.0)
    rs, ra = table.rr_fpl(fpl_b)
    if stale and fpl_b in SHORT_DOUBTS:
        rs, ra = 1 - 0.5 * (1 - rs), 1 - 0.5 * (1 - ra)
    ps_fpl = min(1.0, p_start * rs)
    pa_fpl = max(ps_fpl, min(1.0, p_app * ra))
    ss, sa = table.rr_slp(fpl_b, slp_b)
    if fpl_b == "a100" and stale and slp_b == "out":
        ss = min(ss, STALE_SLEEPER_CAP)   # Sleeper may know something newer
    ss, sa = 1 - weight * (1 - ss), 1 - weight * (1 - sa)
    ps = min(1.0, ps_fpl * ss)
    return (ps_fpl, pa_fpl, ps, max(ps, min(1.0, pa_fpl * sa)))
