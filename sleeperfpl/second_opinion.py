"""Claude's second opinion: its own XI, swaps and matchup notes for the coming game week.

Claude gets raw data only (recent league points and minutes, news, fixtures,
odds, head-to-heads), never this tool's projections or picks, so its view is
independent. Runs once per game week; `build` only reads what it saved.
"""

import json

from .availability import chance_for
from .db import latest
from .headtohead import all_results, form, head_to_head, season_results
from .lineup import _eligible, best_xi
from .report import BuildError, build_report
from .sleeper_stats import sleeper_points

FA_PER_POSITION = 15
RECENT_WEEKS = 5
H2H_MEETINGS = 10
FORM_GAMES = 5


def _weeks(conn, season, last_finished):
    """{player_id: {week: stats}} for every stored week up to last_finished."""
    out = {}
    for pid, week, raw in conn.execute("SELECT player_id, week, stats_json FROM sleeper_stats "
                                       "WHERE season = ? AND week <= ?", (season, last_finished or 0)):
        out.setdefault(pid, {})[week] = json.loads(raw)
    return out


def _player(pl, el, sp, stats, recent_weeks, scoring, deadline_passed, on_roster):
    weeks = stats.get(pl.sleeper_id, {})

    def pts(w):
        return round(sleeper_points(weeks[w], scoring), 1) if w in weeks else 0

    played = [w for w in weeks if (weeks[w].get("min") or 0) > 0]
    out = {"id": pl.sleeper_id, "name": pl.name, "club": pl.club, "positions": pl.positions,
           "fpl_status": el["status"], "chance_of_playing": chance_for(el, deadline_passed), "news": el["news"] or "",
           "sleeper_injury": sp.get("injury_status"), "trending_adds": pl.trending_adds,
           "recent": [{"gw": w, "pts": pts(w), "min": int(weeks[w].get("min") or 0) if w in weeks else 0}
                      for w in recent_weeks],
           "season": {"apps": len(played), "minutes": int(sum(weeks[w].get("min") or 0 for w in weeks)),
                      "pts": round(sum(pts(w) for w in weeks), 1)},
           "fixtures": [{"opponent": f.opponent, "home": f.home, "kickoff": f.kickoff} for f in pl.fixtures]}
    if on_roster:
        out = {**out, "ir": pl.in_reserve, "undroppable": pl.undroppable}
    return out


def build_packet(conn, cfg, now):
    """(packet, context): everything Claude is given, and build_report's context behind it."""
    c = {}
    build_report(conn, cfg, now, log=False, context=c)
    if not c["gw"].gw:
        raise BuildError("no upcoming game week to plan for")
    if not c["use_sleeper"]:
        raise BuildError("the second opinion needs Sleeper stats; run fetch-history first")
    last = c["last_finished"] or 0
    recent = [w for w in range(last - RECENT_WEEKS + 1, last + 1) if w >= 1]
    stats = _weeks(conn, c["season"], last)

    def player(pl):
        return _player(pl, c["elements"][pl.fpl_id], c["sleeper_players"].get(pl.sleeper_id, {}), stats, recent,
                       c["scoring"], c["deadline_passed"], pl.on_roster)

    matched = [pl for pl in c["ctx"].values() if pl.fpl_id is not None and pl.fpl_id in c["elements"]]
    roster = [player(pl) for pl in matched if pl.on_roster]
    by_pos = {}
    for pl in matched:
        if not pl.on_roster:
            by_pos.setdefault((pl.positions or ["?"])[0], []).append(player(pl))
    chosen = {}
    for group in by_pos.values():
        group.sort(key=lambda p: (-sum(w["pts"] for w in p["recent"]), p["name"]))
        for p in group[:FA_PER_POSITION]:
            chosen[p["id"]] = p
        for p in group:
            if p["trending_adds"]:
                chosen[p["id"]] = p

    names = {t["team_id"]: t["name"] for t in latest(conn, "fpl_teams")}
    history, this_season = all_results(conn), season_results(conn)
    fixtures = []
    for f in sorted(c["fixtures"], key=lambda f: f.get("kickoff_time") or ""):
        odds = c["fodds"].get(f["fixture_id"])
        home, away = names.get(f["team_h"], ""), names.get(f["team_a"], "")
        fixtures.append({
            "fixture_id": f["fixture_id"], "home": c["teams"].get(f["team_h"]), "away": c["teams"].get(f["team_a"]),
            "kickoff": f.get("kickoff_time"),
            **{k: None if odds is None else round(getattr(odds, k), 2) for k in ("p_home", "p_draw", "p_away")},
            "xg_home": None if odds is None else round(odds.lam_home, 2),
            "xg_away": None if odds is None else round(odds.lam_away, 2),
            "difficulty_home": f.get("team_h_difficulty"), "difficulty_away": f.get("team_a_difficulty"),
            "head_to_head": head_to_head(history, home, away, H2H_MEETINGS),
            "home_form": form(this_season, home, FORM_GAMES), "away_form": form(this_season, away, FORM_GAMES)})

    gw = c["gw"]
    packet = {
        "gameweek": gw.gw,
        "first_kickoff": gw.first_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ") if gw.first_kickoff else None,
        "adds_are": "free agent pickups" if gw.mode == "instant" else "waiver claims",
        "max_swaps": cfg.max_swaps,
        "slots": c["slots"],
        "slot_positions": {s: cfg.slot_eligibility.get(s, []) for s in dict.fromkeys(c["slots"])},
        "scoring": c["scoring"],
        "roster": roster,
        "free_agents": sorted(chosen.values(), key=lambda p: ((p["positions"] or ["?"])[0], p["name"])),
        "fixtures": fixtures,
    }
    return packet, c


def _obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
ANSWER_SCHEMA = _obj({
    "summary": _STR,
    "xi": {"type": "array", "items": _obj({"player_id": _STR, "slot": _STR, "reason": _STR})},
    "swaps": {"type": "array", "items": _obj({"drop_id": _STR, "add_id": _STR, "reason": _STR})},
    "matchup_notes": {"type": "array", "items": _obj({"fixture": _STR, "note": _STR})},
})


def validate_answer(answer, packet, context, eligibility):
    """Everything wrong with Claude's answer under the league's rules, as readable lines ([] if it's legal).
    The XI is picked from the roster after Claude's own swaps."""
    ctx = context["ctx"]
    roster = {p["id"]: p for p in packet["roster"]}
    free = {p["id"] for p in packet["free_agents"]}
    problems = []
    swaps = answer.get("swaps") or []
    if len(swaps) > packet["max_swaps"]:
        problems.append(f"{len(swaps)} swaps; at most {packet['max_swaps']} are allowed")
    drops, adds = set(), set()
    for s in swaps:
        d, a = s.get("drop_id"), s.get("add_id")
        if d not in roster:
            problems.append(f"drop {d!r} is not on the roster")
        elif roster[d].get("ir") or roster[d].get("undroppable"):
            problems.append(f"drop {d!r} can't be dropped (on IR or marked undroppable)")
        if a not in free:
            problems.append(f"add {a!r} is not one of the listed free agents")
        if d in drops or a in adds:
            problems.append(f"swap {d!r} -> {a!r} repeats a player used in another swap")
        drops.add(d)
        adds.add(a)
    if drops & adds:
        problems.append(f"players both dropped and added: {', '.join(sorted(drops & adds))}")

    pool = {pid: ctx[pid] for pid in roster if pid in ctx and pid not in drops and not ctx[pid].in_reserve}
    pool.update({pid: ctx[pid] for pid in adds if pid in free and pid in ctx})
    left = list(packet["slots"])
    seen = set()
    for entry in answer.get("xi") or []:
        pid, slot = entry.get("player_id"), entry.get("slot")
        if pid in seen:
            problems.append(f"{pid!r} is in the XI twice")
            continue
        seen.add(pid)
        if slot not in left:
            problems.append(f"slot {slot!r} isn't one of the league's open starting slots {packet['slots']}")
        else:
            left.remove(slot)
        if pid in drops:
            problems.append(f"{pid!r} starts but is dropped in your swaps")
        elif pid in roster and ctx.get(pid) is not None and ctx[pid].in_reserve:
            problems.append(f"{pid!r} is on IR and can't start")
        elif pid not in pool:
            problems.append(f"{pid!r} is not on the roster after your swaps")
        elif slot in eligibility and not _eligible(pool[pid], slot, eligibility):
            problems.append(f"{pid!r} ({'/'.join(pool[pid].positions)}) can't play slot {slot!r}")
    can_leave = len(best_xi(list(pool.values()), packet["slots"], eligibility).empty_slots)
    if len(left) > can_leave:
        problems.append(f"slots left empty: {', '.join(left)}; your roster after swaps can fill all but {can_leave}")
    return problems
