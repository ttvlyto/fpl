"""Weekly report card: were the projections (and the market) right?

Every build logs its projections. After a game week finishes, each player's
last projection made before his kickoff is compared with his real league points.
"""

import json

from .db import latest
from .lineup import best_xi
from .metrics import Scored, brier, category, error_summary, log_loss, mean, position_group, rank_by_position, rps
from .models import PlayerCtx
from .sleeper_stats import sleeper_points, stored_weeks


def log_projections(conn, season, gw, players, made_at):
    rows = [(made_at, season, gw, pl.sleeper_id, pl.fixtures[0].kickoff if pl.fixtures else None,
             pl.proj_v1, pl.proj if pl.proj_source == "market" else None, pl.p_goal_market,
             position_group(pl.positions), pl.ep_next)
            for pl in players if pl.fpl_id is not None]
    with conn:
        conn.executemany("INSERT INTO projection_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def log_recommendation(conn, season, gw, first_kickoff, roster_id, lineup, swaps, made_at):
    """The XI and swaps this build suggested, so they can be graded after the week."""
    xi = [pl.sleeper_id for _, pl in lineup.starters if pl]
    pairs = [[s.drop.sleeper_id, s.add.sleeper_id] for s in swaps]
    with conn:
        conn.execute("INSERT INTO recommendation_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (made_at, season, gw, first_kickoff, roster_id, json.dumps(xi), json.dumps(pairs)))


def log_matches(conn, season, gw, fodds, fixtures, made_at, method="market"):
    """Fitted odds for the target week's fixtures, for the match report card."""
    rows = [(made_at, season, gw, f["fixture_id"], f.get("kickoff_time"), method, o.lam_home, o.lam_away,
             o.p_home, o.p_draw, o.p_away, o.cs_home, o.cs_away, o.n_quotes, o.avg_spread)
            for f in fixtures if f.get("event") == gw and (o := fodds.get(f["fixture_id"]))]
    with conn:
        conn.executemany("INSERT INTO match_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def log_goal_odds(conn, season, gw, rows, made_at):
    """Match-day player goal odds (no projection): rows of (sleeper_id, kickoff, p_goal)."""
    with conn:
        conn.executemany("INSERT INTO projection_log VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL)",
                         [(made_at, season, gw, sid, kickoff, p) for sid, kickoff, p in rows])
    return len(rows)


def score_weeks(conn, season, scoring, last_finished, low=3, haul=10):
    """Per finished week: how far off and how well ordered the last pre-kickoff projections were.

    Every logged player counts; one who didn't play (no stats row, or 0 minutes)
    scores whatever his stats give, normally 0, in the "dnp" group.
    """
    if last_finished is None:
        return []
    latest, goal = {}, {}
    for made_at, gw, sid, kickoff, v1, v2, pg, pos, ep in conn.execute(
            "SELECT made_at, gw, sleeper_id, kickoff, v1, v2, p_goal_market, position, ep_next FROM projection_log "
            "WHERE season = ? AND gw <= ? AND (kickoff IS NULL OR made_at <= kickoff) ORDER BY made_at",
            (season, last_finished)):
        if v1 is not None:
            latest[(gw, sid)] = (v1, v2, pos, ep)
        if pg is not None:
            goal[(gw, sid)] = pg
    actual = {(week, pid): json.loads(raw) for week, pid, raw in conn.execute(
        "SELECT week, player_id, stats_json FROM sleeper_stats WHERE season = ? AND week <= ?",
        (season, last_finished))}
    fetched = stored_weeks(conn, season)
    weeks = {}
    for key in set(latest) | set(goal):
        gw, sid = key
        if gw not in fetched:
            continue  # stats not fetched yet: scoring now would count everyone as 0
        stats = actual.get(key)
        pts = sleeper_points(stats, scoring) if stats else 0.0
        w = weeks.setdefault(gw, {"v1": [], "v2": [], "v1p": [], "ep": [], "v1e": [], "b": []})
        if key in latest:
            v1, v2, pos, ep = latest[key]
            cat = category(stats, pts, low, haul)
            w["v1"].append(Scored(pos, v1, pts, cat))
            if v2 is not None:   # compare v1 and v2 on the same players
                w["v2"].append(Scored(pos, v2, pts, cat))
                w["v1p"].append(Scored(pos, v1, pts, cat))
            if ep is not None:   # ep_next is in FPL points: only its ordering is comparable
                w["ep"].append(Scored(pos, ep, pts, cat))
                w["v1e"].append(Scored(pos, v1, pts, cat))
        if key in goal and stats and (stats.get("min") or 0) > 0:
            w["b"].append(brier(goal[key], (stats.get("g") or 0) >= 1))

    out = []
    for gw in sorted(weeks):
        w = weeks[gw]
        e1 = error_summary(w["v1"])
        out.append({"gw": gw, "n": e1["n"], "mae_v1": e1["mae"], "n_played": e1["n_played"],
                    "mae_v1_played": e1["mae_played"], "by_cat_v1": e1["by_cat"],
                    "rank_v1": rank_by_position(w["v1"]),
                    "n_v2": len(w["v2"]), "mae_v2": error_summary(w["v2"])["mae"],
                    "mae_v1_paired": error_summary(w["v1p"])["mae"],
                    "rank_v2": rank_by_position(w["v2"]), "rank_v1_paired": rank_by_position(w["v1p"]),
                    "n_ep": len(w["ep"]), "rank_ep": rank_by_position(w["ep"]),
                    "rank_v1_ep": rank_by_position(w["v1e"]),
                    "brier_goal": mean(w["b"]), "n_goal": len(w["b"])})
    return out


def score_decisions(conn, season, scoring, last_finished, slots, eligibility):
    """Per finished week: real points of the lineup you set, the XI this page suggested,
    and the best XI possible from the same roster, plus how each swap tip turned out.

    Uses the last recommendation before the week's first kickoff and the newest
    roster snapshot before it; weeks missing either are skipped."""
    if last_finished is None:
        return []
    recs = {}
    for made_at, gw, first, roster_id, xi_json, swaps_json in conn.execute(
            "SELECT made_at, gw, first_kickoff, roster_id, xi_json, swaps_json FROM recommendation_log "
            "WHERE season = ? AND gw <= ? AND first_kickoff IS NOT NULL AND made_at < first_kickoff "
            "ORDER BY made_at", (season, last_finished)):
        recs[gw] = (first, roster_id, json.loads(xi_json or "[]"), json.loads(swaps_json or "[]"))
    if not recs:
        return []
    names, positions = {}, {}
    for r in latest(conn, "sleeper_players"):
        names[r["player_id"]] = r["name"] or r["player_id"]
        positions[r["player_id"]] = json.loads(r["positions_json"] or "[]")
    fetched = stored_weeks(conn, season)
    out = []
    for gw in sorted(recs):
        if gw not in fetched:
            continue  # stats not fetched yet
        first, roster_id, xi, swaps = recs[gw]
        row = conn.execute("SELECT players_json, starters_json, reserve_json FROM sleeper_rosters "
                           "WHERE roster_id = ? AND fetched_at < ? ORDER BY fetched_at DESC LIMIT 1",
                           (roster_id, first)).fetchone()
        if row is None:
            continue
        players = json.loads(row[0] or "[]")
        starters = json.loads(row[1] or "[]")
        reserve = set(json.loads(row[2] or "[]"))
        stats = {pid: json.loads(raw) for pid, raw in conn.execute(
            "SELECT player_id, stats_json FROM sleeper_stats WHERE season = ? AND week = ?", (season, gw))}

        def pts(pid):
            return sleeper_points(stats[pid], scoring) if pid in stats else 0.0
        pool = [PlayerCtx(sleeper_id=pid, name=names.get(pid, pid), positions=positions.get(pid, []), club="",
                          fpl_id=0, proj=pts(pid)) for pid in players if pid not in reserve]
        out.append({"gw": gw,
                    "set_pts": sum(pts(pid) for pid in starters if pid in players),
                    "tool_pts": sum(pts(pid) for pid in xi),
                    "best_pts": best_xi(pool, slots, eligibility).total,
                    "swaps": [{"drop": names.get(d, d), "add": names.get(a, a), "drop_pts": pts(d),
                               "add_pts": pts(a), "diff": pts(a) - pts(d)} for d, a in swaps]})
    return out


def score_matches(conn, season, last_finished):
    """Per finished week: log loss and RPS of the win/draw/loss odds, Brier of the
    clean-sheet chances, using each fixture's last odds logged before kickoff."""
    if last_finished is None:
        return []
    logged = {}
    for fid, made_at, kickoff, ph, pd, pa, csh, csa in conn.execute(
            "SELECT fixture_id, made_at, kickoff, p_home, p_draw, p_away, cs_home, cs_away FROM match_log "
            "WHERE season = ? AND (kickoff IS NULL OR made_at <= kickoff) ORDER BY made_at", (season,)):
        logged[fid] = (ph, pd, pa, csh, csa)
    weeks = {}
    for f in latest(conn, "fpl_fixtures"):
        h, a = f["team_h_score"], f["team_a_score"]
        if (not f["finished"] or h is None or a is None or f["fixture_id"] not in logged
                or f["event"] is None or f["event"] > last_finished):
            continue  # postponed, abandoned, unplayed or never priced
        ph, pd, pa, csh, csa = logged[f["fixture_id"]]
        outcome = "home" if h > a else "away" if a > h else "draw"
        w = weeks.setdefault(f["event"], {"ll": [], "rps": [], "cs": []})
        w["ll"].append(log_loss(ph, pd, pa, outcome))
        w["rps"].append(rps(ph, pd, pa, outcome))
        w["cs"].append((brier(csh, a == 0) + brier(csa, h == 0)) / 2)
    return [{"gw": gw, "n": len(w["ll"]), "log_loss": mean(w["ll"]), "rps": mean(w["rps"]),
             "brier_cs": mean(w["cs"])} for gw, w in sorted(weeks.items())]


def summary(weeks, decisions=None):
    if decisions and len(decisions) >= 2:
        wins = sum(1 for d in decisions if d["tool_pts"] > d["set_pts"])
        return f"the tool's XI beat your lineup in {wins} of {len(decisions)} weeks"
    compared = [w for w in weeks if w.get("n_v2") and w.get("mae_v2") is not None]
    if len(compared) < 2:
        return "not enough weeks yet"
    wins = sum(1 for w in compared if w["mae_v2"] < w.get("mae_v1_paired", w["mae_v1"]))
    return f"v2 (market) beat v1 (difficulty) in {wins} of {len(compared)} weeks"
