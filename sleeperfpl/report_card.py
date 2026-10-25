"""Weekly report card: were the projections (and the market) right?

Every build logs its projections. After a game week finishes, each player's
last projection made before his kickoff is compared with his real league points.
"""

import json

from .sleeper_stats import sleeper_points


def log_projections(conn, season, gw, players, made_at):
    rows = [(made_at, season, gw, pl.sleeper_id, pl.fixtures[0].kickoff if pl.fixtures else None,
             pl.proj_v1, pl.proj if pl.proj_source == "market" else None, pl.p_goal_market)
            for pl in players if pl.fpl_id is not None]
    with conn:
        conn.executemany("INSERT INTO projection_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)", rows)
    return len(rows)


def log_goal_odds(conn, season, gw, rows, made_at):
    """Match-day player goal odds (no projection): rows of (sleeper_id, kickoff, p_goal)."""
    with conn:
        conn.executemany("INSERT INTO projection_log VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL)",
                         [(made_at, season, gw, sid, kickoff, p) for sid, kickoff, p in rows])
    return len(rows)


def score_weeks(conn, season, scoring, last_finished):
    if last_finished is None:
        return []
    latest, goal = {}, {}
    for made_at, gw, sid, kickoff, v1, v2, pg in conn.execute(
            "SELECT made_at, gw, sleeper_id, kickoff, v1, v2, p_goal_market FROM projection_log "
            "WHERE season = ? AND gw <= ? AND (kickoff IS NULL OR made_at <= kickoff) ORDER BY made_at",
            (season, last_finished)):
        if v1 is not None:
            latest[(gw, sid)] = (v1, v2)
        if pg is not None:
            goal[(gw, sid)] = pg
    actual = {(week, pid): json.loads(raw) for week, pid, raw in conn.execute(
        "SELECT week, player_id, stats_json FROM sleeper_stats WHERE season = ? AND week <= ?",
        (season, last_finished))}
    weeks = {}
    for key in set(latest) | set(goal):
        gw, sid = key
        stats = actual.get(key)
        if not stats or (stats.get("min") or 0) <= 0:
            continue  # didn't play: not a projection miss
        pts = sleeper_points(stats, scoring)
        w = weeks.setdefault(gw, {"gw": gw, "e1": [], "e1p": [], "e2": [], "b": []})
        if key in latest:
            v1, v2 = latest[key]
            w["e1"].append(abs(v1 - pts))
            if v2 is not None:   # compare v1 and v2 on the same players
                w["e2"].append(abs(v2 - pts))
                w["e1p"].append(abs(v1 - pts))
        if key in goal:
            w["b"].append((goal[key] - (1.0 if (stats.get("g") or 0) >= 1 else 0.0)) ** 2)

    def mean(xs):
        return sum(xs) / len(xs) if xs else None
    return [{"gw": w["gw"], "n": len(w["e1"]), "mae_v1": mean(w["e1"]), "mae_v1_paired": mean(w["e1p"]),
             "mae_v2": mean(w["e2"]),
             "n_v2": len(w["e2"]), "brier_goal": mean(w["b"]), "n_goal": len(w["b"])}
            for w in sorted(weeks.values(), key=lambda w: w["gw"])]


def summary(weeks):
    compared = [w for w in weeks if w.get("n_v2") and w.get("mae_v2") is not None]
    if len(compared) < 2:
        return "not enough weeks yet"
    wins = sum(1 for w in compared if w["mae_v2"] < w.get("mae_v1_paired", w["mae_v1"]))
    return f"v2 (market) beat v1 (difficulty) in {wins} of {len(compared)} weeks"
