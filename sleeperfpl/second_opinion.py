"""Claude's second opinion: its own XI, swaps and matchup notes for the coming game week.

Claude gets raw data only (recent league points and minutes, news, fixtures,
odds, head-to-heads), never this tool's projections or picks, so its view is
independent. Runs once per game week; `build` only reads what it saved.
"""

import json
import os
from collections import namedtuple
from datetime import timedelta

from .availability import chance_for
from .db import latest, meta_get, meta_set
from .gameweek import target_gameweek
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
            "home_name": home, "away_name": away,
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


SYSTEM_PROMPT = """You are advising the manager of a Sleeper fantasy Premier League team for one game week.
The user message is a JSON packet with the league's starting slots and scoring, the manager's roster,
a list of free agents, and this game week's fixtures with betting odds, head-to-head records and form.

Each fixture's head_to_head counts the two clubs' recent league meetings at either venue:
home_wins and home_winless_run are for the fixture's home club (home_name), away_wins and
away_winless_run for its away club; a winless run of null means they haven't met.

Pick the starting XI and up to max_swaps drop/add swaps that will score the most league points this
game week. Apply your swaps first: pick the XI from the roster after them, so a player you add may
start and a player you drop may not. Players on IR can't start or be dropped; undroppable players
can't be dropped. Adds are free agent pickups or waiver claims, as the packet's adds_are says.

Judge on evidence: minutes, recent league points, form, fixtures, odds and injury news. Do not favour
players for their club's reputation or their name; a squad player at a big club who isn't getting
minutes is worth less than a regular starter at a small one. Cite only numbers that appear in the
packet, and never invent a statistic. Keep every reason to one sentence. Write matchup notes only
for fixtures involving the roster or your adds. Return no swaps if none clearly helps."""

Reply = namedtuple("Reply", "text stop_reason input_tokens output_tokens")


def _default_call(request):
    """Send `request` to the Anthropic API (streamed) and return a Reply."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise BuildError("ANTHROPIC_API_KEY isn't set")
    try:
        import anthropic
    except ImportError:
        raise BuildError("the anthropic package isn't installed: pip install -r requirements.txt")
    try:
        with anthropic.Anthropic().beta.messages.stream(**request) as stream:
            m = stream.get_final_message()
    except anthropic.APIError as e:
        raise BuildError(f"Anthropic API: {e}")
    text = "".join(b.text for b in m.content if b.type == "text")
    return Reply(text, m.stop_reason, m.usage.input_tokens, m.usage.output_tokens)


def make_request(packet, cfg, problems=None, previous=None):
    so = cfg.second_opinion
    messages = [{"role": "user", "content": json.dumps(packet)}]
    if problems:
        messages += [{"role": "assistant", "content": previous},
                     {"role": "user", "content": "That answer breaks the league's rules:\n- " + "\n- ".join(problems)
                      + "\nSend a corrected full answer."}]
    return {"model": so["model"], "max_tokens": 64000,
            "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default",
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": so["effort"], "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
            "system": SYSTEM_PROMPT, "messages": messages}


MAX_ATTEMPTS = 2   # failed runs per game week before the hourly job gives up (each one is billed)


def _attempts_key(season, gw):
    return f"second_opinion_attempts_{season}_{gw}"


def is_due(conn, season, gw, first_kickoff, now, lead_hours):
    """A game week to plan, its first kickoff within lead_hours, no opinion saved for it yet,
    and fewer than MAX_ATTEMPTS tries so far."""
    if not gw or first_kickoff is None or first_kickoff - now > timedelta(hours=lead_hours):
        return False
    if int(meta_get(conn, _attempts_key(season, gw)) or 0) >= MAX_ATTEMPTS:
        return False
    return conn.execute("SELECT 1 FROM second_opinion_log WHERE season = ? AND gw = ?",
                        (season, gw)).fetchone() is None


def _ask(call, request):
    reply = call(request)
    if reply.stop_reason != "end_turn":
        raise BuildError(f"Claude stopped early ({reply.stop_reason}); nothing saved")
    try:
        return reply, json.loads(reply.text)
    except json.JSONDecodeError:
        raise BuildError("Claude's answer wasn't valid JSON; nothing saved")


def run_second_opinion(conn, cfg, now, *, force=False, call=None, refresh=None):
    """Ask Claude for this game week's opinion if one is due (or `force`), and save it. Returns a status line."""
    so = cfg.second_opinion
    if not so["enabled"]:
        return "second opinion: disabled in config.json"
    league = latest(conn, "sleeper_league")
    season = str(json.loads(league[0]["data_json"]).get("season") or "") if league else ""
    gw = target_gameweek([dict(r) for r in latest(conn, "fpl_events")],
                         [dict(r) for r in latest(conn, "fpl_fixtures")], now)
    if not force and not is_due(conn, season, gw.gw, gw.first_kickoff, now, so["lead_hours"]):
        return f"second opinion: not due (GW{gw.gw or '-'})"
    if gw.gw:
        key = _attempts_key(season, gw.gw)
        meta_set(conn, key, str(int(meta_get(conn, key) or 0) + 1))
    if refresh:
        refresh()
    packet, context = build_packet(conn, cfg, now)
    call = call or _default_call
    reply, answer = _ask(call, make_request(packet, cfg))
    problems = validate_answer(answer, packet, context, cfg.slot_eligibility)
    if problems:
        reply2, answer = _ask(call, make_request(packet, cfg, problems, reply.text))
        reply = Reply(reply2.text, reply2.stop_reason, reply.input_tokens + reply2.input_tokens,
                      reply.output_tokens + reply2.output_tokens)
        problems = validate_answer(answer, packet, context, cfg.slot_eligibility)
        if problems:
            raise BuildError("Claude's answer broke the league's rules twice; nothing saved:\n  "
                             + "\n  ".join(problems))
    with conn:
        conn.execute("INSERT INTO second_opinion_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (now.strftime("%Y-%m-%dT%H:%M:%SZ"), context["season"], packet["gameweek"],
                      packet["first_kickoff"], so["model"], json.dumps(answer["xi"]), json.dumps(answer["swaps"]),
                      json.dumps(answer["matchup_notes"]), answer["summary"], reply.input_tokens,
                      reply.output_tokens))
    return f"second opinion: GW{packet['gameweek']} saved ({reply.input_tokens}/{reply.output_tokens} tokens)"
