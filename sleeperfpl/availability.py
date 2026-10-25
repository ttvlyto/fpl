"""Injury labels: FPL's status and chance, and Sleeper's injury_status.

FPL leads. Only FPL's hard "out" (suspended, unavailable, left the club, or a
0% chance) zeroes a player; Sleeper's label never does.

In the minutes model, labels are relative risks: they multiply the model's
P(start) and P(appear). The starting values are guesses from the research
(nobody has published how often flagged players play); they learn from this
league's own outcomes as weeks finish.
"""

from datetime import timedelta

from .gameweek import parse_time
from .metrics import wilson
from .minutes import regular_weeks, season_rows

HARD_OUT = {"s", "u", "n", "i0", "d0"}
# (relative risk of starting, of appearing) against an unflagged player.
FPL_PRIOR = {"a100": (1.0, 1.0), "d75": (0.63, 0.82), "i75": (0.57, 0.76), "d50": (0.29, 0.43),
             "i50": (0.23, 0.38), "d25": (0.09, 0.16), "i25": (0.06, 0.11)}
# Sleeper's label on top of FPL's: keyed by (FPL side, Sleeper bucket); FPL side is a100 or "flagged".
SLP_PRIOR = {("a100", "out"): (0.80, 0.95), ("a100", "gtd"): (0.90, 1.00), ("flagged", "out"): (0.90, 0.95),
             ("a100", "sus"): (0.50, 0.50)}
SHORT_DOUBTS = {"d75", "d50", "i75", "i50"}   # labels that describe one match; a long injury stays flagged
STALE_SLEEPER_CAP = 0.60
POOL_BELOW = 30          # an FPL bucket with fewer rows borrows its d/i twin's counts
RR_CAP = 1.2
MAX_LABEL_AGE = timedelta(hours=72)


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
    """Relative risks per label. Each learns from logged outcomes as observed over expected:
    RR = (O + k·prior) / (E + k), where E sums the probabilities the layer before gave those players."""

    def __init__(self, counts=None, k=20):
        self.counts = counts or {}
        self.k = k

    def prior_fpl(self, bucket):
        return FPL_PRIOR.get(bucket) or _nearest(bucket)

    def _learn(self, prior, c):
        if not c:
            return prior
        rs = (c["starts"] + self.k * prior[0]) / (c["e_start"] + self.k)
        ra = (c["apps"] + self.k * prior[1]) / (c["e_app"] + self.k)
        return (min(RR_CAP, max(0.0, rs)), min(RR_CAP, max(0.0, ra)))

    def rr_fpl(self, bucket):
        c = self.counts.get(("fpl", bucket))
        if bucket != "a100" and c and c["n"] < POOL_BELOW:
            twin = self.counts.get(("fpl", ("i" if bucket[0] == "d" else "d") + bucket[1:]))
            if twin:
                c = {k: c[k] + twin[k] for k in c}
        return self._learn(self.prior_fpl(bucket), c)

    def rr_slp(self, fpl_bucket_, slp):
        key = (fpl_side(fpl_bucket_), slp)
        if key not in SLP_PRIOR:
            return (1.0, 1.0)     # no reason to adjust, and never learned
        return self._learn(SLP_PRIOR[key], self.counts.get(("sleeper", f"{key[0]}+{slp}")))


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


def _last_logged(conn, season, max_gw=None):
    """{(gw, sleeper id): row} for each player's last projection_log row before his kickoff that carries labels."""
    out = {}
    for row in conn.execute(
            "SELECT gw, sleeper_id, kickoff, fpl_bucket, slp_bucket, p_start_model, p_app_model, p_start_fpl, "
            "p_app_fpl, label_at FROM projection_log WHERE season = ? AND fpl_bucket IS NOT NULL "
            "AND p_start_model IS NOT NULL AND kickoff IS NOT NULL AND made_at <= kickoff "
            "AND (? IS NULL OR gw <= ?) ORDER BY made_at", (season, max_gw, max_gw)):
        out[(row[0], row[1])] = row
    return out


def hard_out_weeks(conn, season):
    """{sleeper id: weeks FPL had him hard out before kickoff}: left out of his minutes history."""
    out = {}
    for (gw, sid), row in _last_logged(conn, season).items():
        if is_hard_out(row[3]):
            out.setdefault(sid, set()).add(gw)
    return out


def rebuild_label_counts(conn, season, last_finished, built_at):
    """Count, per label, how often logged players started and played against what was expected."""
    if last_finished is None:
        return 0
    rows = season_rows(conn, season, last_finished)
    regular = regular_weeks(rows)
    cells = {}
    for (gw, sid), (_, _, kickoff, fb, sb, psm, pam, psf, paf, label_at) in _last_logged(conn, season,
                                                                                        last_finished).items():
        k, at = parse_time(kickoff), parse_time(label_at)
        if gw not in rows or gw not in regular or not k or not at or k - at > MAX_LABEL_AGE:
            continue
        st = rows[gw].get(sid) or {}
        minutes = st.get("min") or 0
        outcome = ((st.get("gs") or 0) >= 1, minutes > 0, minutes >= 60)
        for key, e in ((("fpl", fb), (psm, pam)), (("sleeper", f"{fpl_side(fb)}+{sb}"), (psf or 0.0, paf or 0.0))):
            c = cells.setdefault(key, {"n": 0, "starts": 0, "apps": 0, "sixty": 0, "e_start": 0.0, "e_app": 0.0})
            c["n"] += 1
            c["starts"] += outcome[0]
            c["apps"] += outcome[1]
            c["sixty"] += outcome[2]
            c["e_start"] += e[0]
            c["e_app"] += e[1]
    with conn:
        conn.execute("DELETE FROM label_counts WHERE season = ?", (season,))
        conn.executemany("INSERT INTO label_counts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(season, kind, cell, c["n"], c["starts"], c["apps"], c["sixty"], c["e_start"], c["e_app"],
                           built_at) for (kind, cell), c in cells.items()])
    return len(cells)


def load_counts(conn, season):
    return {(kind, cell): {"n": n, "starts": st, "apps": ap, "sixty": sx, "e_start": es, "e_app": ea}
            for kind, cell, n, st, ap, sx, es, ea in conn.execute(
                "SELECT kind, cell, n, starts, apps, sixty, e_start, e_app FROM label_counts WHERE season = ?",
                (season,))}


_LEAD = ("sleeper", "a100+out")


def audit_rows(conn, season):
    """Per label: how often players started, played (with a 90% range) and reached 60 minutes."""
    out = []
    for (kind, cell), c in load_counts(conn, season).items():
        lo, hi = wilson(c["apps"], c["n"])
        out.append({"kind": kind, "cell": cell, "n": c["n"], "start_rate": c["starts"] / c["n"],
                    "app_rate": c["apps"] / c["n"], "sixty_rate": c["sixty"] / c["n"], "app_lo": lo, "app_hi": hi})
    return sorted(out, key=lambda r: ((r["kind"], r["cell"]) != _LEAD, r["kind"], r["cell"]))


def _line(r):
    return (f"  {r['cell']:<16} players {r['n']:>4}  started {r['start_rate']:4.0%}  played {r['app_rate']:4.0%} "
            f"({r['app_lo']:.0%}–{r['app_hi']:.0%})  60+ {r['sixty_rate']:4.0%}")


def format_audit(rows):
    lead = [r for r in rows if (r["kind"], r["cell"]) == _LEAD]
    lines = ["Sleeper says Out, FPL says available"] + ([_line(r) for r in lead] or ["  no players yet"])
    for kind, title in (("fpl", "FPL labels"), ("sleeper", "Sleeper labels, on top of FPL's")):
        group = [r for r in rows if r["kind"] == kind and (kind, r["cell"]) != _LEAD]
        if group:
            lines += [title] + [_line(r) for r in group]
    lines.append("Ranges are 90% intervals; few players means a wide range.")
    return "\n".join(lines)
