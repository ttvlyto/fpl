"""Assemble the game-week report from the DB and render it as a static page.

report.json is the structured result (a later Claude review can read it);
index.html is the page the owner opens.
"""

import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .db import latest, latest_fetched_at, meta_get
from .fetch_fpl import HISTORY_META
from .gameweek import season_of, target_gameweek, team_fixtures
from .lineup import best_xi, rank_free_agents, starting_slots, swap_tips
from .matching import FPL_POSITIONS, load_overrides, match_players
from .models import PlayerCtx
from .projection import project
from .scoring_map import coverage, league_drift, load_scoring_map, score_match

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sleeper import rostered_ids  # noqa: E402

OUT_STATUSES = {"i", "s", "u", "n"}


class BuildError(Exception):
    pass


def _rows(conn, table):
    return [dict(r) for r in latest(conn, table)]


def _owner_roster(users, rosters, owner):
    user = next((u for u in users if owner in (u["user_id"], u["display_name"])), None)
    roster = next((r for r in rosters if user and r["owner_id"] == user["user_id"]), None)
    if roster is None:
        names = ", ".join(sorted(u["display_name"] or u["user_id"] for u in users))
        raise BuildError(f"No roster for owner '{owner}' in this league. League members: {names}")
    return roster


def _flag(sp, el):
    if el is None:
        return "no data"
    if el["status"] in OUT_STATUSES or el["chance_next"] == 0 or (sp.get("injury_status") or "") == "Out":
        return "out"
    if el["status"] == "d":
        return "doubtful"
    return None


def _history(conn, season, fpl_id, position, rules):
    rows = conn.execute("SELECT stats_json FROM fpl_history WHERE season = ? AND fpl_id = ? AND minutes > 0 "
                        "ORDER BY kickoff_time DESC", (season, fpl_id)).fetchall()
    return [score_match(json.loads(r[0]), position, rules) for r in rows]


def _player_dict(pl):
    return {
        "sleeper_id": pl.sleeper_id, "name": pl.name, "positions": pl.positions, "club": pl.club,
        "flag": pl.flag, "avg": None if pl.avg is None else round(pl.avg, 1), "n": pl.n,
        "proj": round(pl.proj, 1), "trending_adds": pl.trending_adds,
        "fixtures": [{"opponent": f.opponent, "home": f.home, "difficulty": f.difficulty, "kickoff": f.kickoff}
                     for f in pl.fixtures],
    }


def build_report(conn, cfg, now):
    league_rows = _rows(conn, "sleeper_league")
    players = _rows(conn, "sleeper_players")
    rosters = _rows(conn, "sleeper_rosters")
    elements = _rows(conn, "fpl_elements")
    if not (league_rows and players and rosters and elements):
        raise BuildError("no data yet; run fetch-daily and fetch-rosters first")

    league = json.loads(league_rows[0]["data_json"])
    slots = starting_slots(league.get("roster_positions") or [], cfg.slot_eligibility)
    rules = load_scoring_map(cfg.scoring_map_path)
    covered, missing = coverage(rules)
    not_in_map, changed = league_drift(rules, league.get("scoring_settings") or {})
    missing = sorted(set(missing) | set(not_in_map))

    for r in rosters:
        for key in ("players_json", "starters_json", "reserve_json"):
            r[key[:-5]] = json.loads(r[key] or "[]")
    mine = _owner_roster(_rows(conn, "sleeper_users"), rosters, cfg.owner)
    taken = rostered_ids(rosters)

    teams = {t["team_id"]: t["short_name"] for t in _rows(conn, "fpl_teams")}
    fixtures = _rows(conn, "fpl_fixtures")
    events = _rows(conn, "fpl_events")
    season = season_of(events) or "unknown"
    gw = target_gameweek(events, fixtures, now)
    by_team = team_fixtures(fixtures, teams, gw.gw) if gw.gw else {}

    for p in players:
        p["positions"] = json.loads(p["positions_json"] or "[]")
    match = match_players(players, elements, [{"team_id": k, "short_name": v} for k, v in teams.items()],
                          load_overrides(cfg.overrides_path))
    el_by_id = {e["fpl_id"]: e for e in elements}
    trending = {t["player_id"]: t["count"] for t in _rows(conn, "sleeper_trending")}

    my_ids, reserve = set(mine["players"]), set(mine["reserve"])
    ctx = {}
    for p in players:
        sid = p["player_id"]
        if sid not in my_ids and (sid in taken or not p["team"] or not p["active"]):
            continue  # someone else's player, or not a usable free agent
        el = el_by_id.get(match.mapping.get(sid))
        fpl_id = el["fpl_id"] if el else None  # an id missing from FPL counts as unmatched
        club = teams.get(el["team_id"], p["team"]) if el else p["team"]
        pl = PlayerCtx(sleeper_id=sid, name=p["name"] or sid, positions=p["positions"], club=club,
                       fpl_id=fpl_id, on_roster=sid in my_ids, in_reserve=sid in reserve,
                       flag=_flag(p, el), fixtures=by_team.get(club, []) if el else [],
                       trending_adds=trending.get(sid))
        if el:
            pl.history = _history(conn, season, fpl_id, FPL_POSITIONS.get(el["element_type"], ""), rules)
            pr = project(pl.history, [f.difficulty for f in pl.fixtures], pl.flag == "out",
                         cfg.difficulty_multipliers, cfg.avg_window)
            pl.avg, pl.n, pl.proj = pr.avg, pr.n, pr.proj
        ctx[sid] = pl
    for sid in my_ids - set(ctx):
        # On the roster but absent from Sleeper's player map: show it, never hide it.
        ctx[sid] = PlayerCtx(sleeper_id=sid, name=f"Sleeper player {sid}", positions=[], club="",
                             fpl_id=None, on_roster=True, in_reserve=sid in reserve, flag="no data")

    roster = [pl for pl in ctx.values() if pl.on_roster]
    free_agents = [pl for pl in ctx.values() if not pl.on_roster]
    lineup = best_xi(roster, slots, cfg.slot_eligibility)
    swaps = swap_tips(roster, free_agents, slots, cfg.slot_eligibility, cfg.swap_min_gain, cfg.max_swaps)
    ranked = rank_free_agents(free_agents, cfg.free_agents_per_position)

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gameweek": gw.gw,
        "mode": gw.mode,
        "first_kickoff": gw.first_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ") if gw.first_kickoff else None,
        "sources": {
            "sleeper_rosters": latest_fetched_at(conn, "sleeper_rosters"),
            "sleeper_players": latest_fetched_at(conn, "sleeper_players"),
            "sleeper_trending": latest_fetched_at(conn, "sleeper_trending"),
            "fpl_bootstrap": latest_fetched_at(conn, "fpl_elements"),
            "fpl_fixtures": latest_fetched_at(conn, "fpl_fixtures"),
            "fpl_history_gw": meta_get(conn, HISTORY_META),
        },
        "coverage": {"covered": covered, "missing": missing, "changed": changed},
        "xi": {
            "total": round(lineup.total, 1),
            "starters": [{"slot": s, "player": _player_dict(pl) if pl else None} for s, pl in lineup.starters],
            "bench": [_player_dict(pl) for pl in lineup.bench],
            "empty_slots": lineup.empty_slots,
        },
        "swaps": [{"drop": _player_dict(s.drop), "add": _player_dict(s.add), "gain": round(s.gain, 1),
                   "kind": s.kind} for s in swaps],
        "free_agents": {pos: [_player_dict(pl) for pl in group] for pos, group in sorted(ranked.items())},
        "unmatched_rostered": [_player_dict(pl) for pl in roster if pl.fpl_id is None],
        "unmatched_count": len(match.unmatched),
    }


# ---------------------------------------------------------------- html

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d1f23;--muted:#667;--line:#e3e5e8;--good:#2e9e5b;--mid:#888;--bad:#c0392b;--warn:#b35c00;--warnbg:#fff4e5}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1f2226;--fg:#e8e9eb;--muted:#9aa0a8;--line:#30343a;--warnbg:#3a2a12;--warn:#ffb35c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 8px}
.muted{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:600;cursor:pointer;white-space:nowrap;padding:4px 6px;border-bottom:1px solid var(--line)}
td{padding:6px;border-bottom:1px solid var(--line);white-space:nowrap}td.num,th.num{text-align:right}
small{color:var(--muted)}
.fx{display:inline-block;color:#fff;padding:1px 6px;border-radius:4px;font-size:12px;margin-right:3px}
.d1,.d2{background:var(--good)}.d3{background:var(--mid)}.d4,.d5{background:var(--bad)}
.flag{font-size:12px;color:var(--warn)}
.tip{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 10px;margin:6px 0}
.banner{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 10px}
"""

SORT_JS = """
document.querySelectorAll('table.sortable th').forEach(function(th,i){th.addEventListener('click',function(){
var tb=th.closest('table').tBodies[0],rows=Array.from(tb.rows),asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
rows.sort(function(a,b){var x=a.cells[i].dataset.v||a.cells[i].textContent,y=b.cells[i].dataset.v||b.cells[i].textContent;
var nx=parseFloat(x),ny=parseFloat(y);var c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y);return asc?c:-c;});
rows.forEach(function(r){tb.appendChild(r);});});});
"""


def _e(s):
    return html.escape("" if s is None else str(s))


def _fixtures_html(p):
    if not p["fixtures"]:
        return '<small>no match</small>'
    parts = []
    for f in p["fixtures"]:
        code = f["opponent"].upper() if f["home"] else f["opponent"].lower()
        parts.append(f'<span class="fx d{int(f["difficulty"])}">{_e(code)}</span>')
    return "".join(parts)


def _num(v):
    return "–" if v is None else f"{v:.1f}"


def _player_cells(p):
    flag = f' <span class="flag">{_e(p["flag"])}</span>' if p["flag"] else ""
    pos = "/".join(p["positions"])
    return (f'<td>{_e(p["name"])} <small>{_e(pos)} {_e(p["club"])}</small>{flag}</td>'
            f'<td>{_fixtures_html(p)}</td>'
            f'<td class="num" data-v="{p["avg"] if p["avg"] is not None else -1}">{_num(p["avg"])} <small>({p["n"]})</small></td>'
            f'<td class="num" data-v="{p["proj"]}"><b>{_num(p["proj"])}</b></td>')


HEAD = '<tr><th>Player</th><th>vs</th><th class="num">Avg5</th><th class="num">Proj</th>'


def _table(rows_html, extra_head=""):
    return (f'<div class="scroll"><table class="sortable"><thead>{HEAD}{extra_head}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table></div>')


def render_html(report):
    r = report
    src = r["sources"]
    asof = " · ".join(f"{_e(k.replace('_', ' '))}: {_e(v or 'never')}" for k, v in src.items())
    parts = [f'<h1>GW{_e(r["gameweek"] or "–")} · Best XI <span class="muted">projected {_num(r["xi"]["total"])}</span></h1>',
             f'<div class="muted">Generated {_e(r["generated_at"])} · first kickoff {_e(r["first_kickoff"] or "–")} · '
             f'pickups: {"waiver claims" if r["mode"] == "waiver" else "instant free agents"}</div>',
             f'<div class="muted">Data as of — {asof}</div>']

    if r["coverage"]["missing"]:
        parts.append(f'<div class="card banner">Approximate: {len(r["coverage"]["missing"])} scoring rule(s) have no FPL '
                     f'stat and are left out: {_e(", ".join(r["coverage"]["missing"]))}. Edit scoring_map.json to map them.</div>')

    if r["coverage"].get("changed"):
        parts.append(f'<div class="card banner">League points changed for: {_e(", ".join(r["coverage"]["changed"]))}. '
                     f'Update those rules in scoring_map.json.</div>')

    rows = []
    for s in r["xi"]["starters"]:
        if s["player"]:
            rows.append(f'<tr>{_player_cells(s["player"])}<td>{_e(s["slot"])}</td></tr>')
        else:
            rows.append(f'<tr><td colspan="4" class="flag">No eligible player</td><td>{_e(s["slot"])}</td></tr>')
    xi = [f'<div class="card"><h2>Best XI</h2>{_table(rows, "<th>Slot</th>")}']
    if r["xi"]["bench"]:
        xi.append(f'<h2 style="margin-top:12px">Bench</h2>{_table([f"<tr>{_player_cells(p)}</tr>" for p in r["xi"]["bench"]])}')
    xi.append('<div class="muted" style="margin-top:6px">Opponent in CAPS = home, lower case = away. '
              'Green = easy, grey = average, red = hard. Avg5 (n) = average league points over the last n matches played.</div></div>')
    parts.append("".join(xi))

    tips = []
    for s in r["swaps"]:
        prefix = "Waiver claim: " if r["mode"] == "waiver" else ""
        what = "projected" if s["kind"] == "xi" else "bench upgrade"
        tips.append(f'<div class="tip">{prefix}Drop {_e(s["drop"]["name"])}, add {_e(s["add"]["name"])}: '
                    f'+{_num(s["gain"])} {what}</div>')
    parts.append(f'<div class="card"><h2>Swap tips</h2>{"".join(tips) or "<div class=muted>No swap clears the threshold this week.</div>"}</div>')

    fa = ['<div class="card"><h2>Top free agents</h2>']
    for pos, group in r["free_agents"].items():
        rows = [f'<tr>{_player_cells(p)}<td class="num" data-v="{p["trending_adds"] or 0}">'
                f'{"▲ " + str(p["trending_adds"]) if p["trending_adds"] else "–"}</td></tr>' for p in group]
        fa.append(f'<h2 style="margin-top:10px">{_e(pos)}</h2>{_table(rows, "<th class=num>Trend</th>")}')
    fa.append("</div>")
    parts.append("".join(fa))

    um = r["unmatched_rostered"]
    um_list = "".join(f"<li>{_e(p['name'])} <small>{_e(p['club'])}</small></li>" for p in um)
    parts.append(f'<div class="card"><h2>Unmatched</h2><div class="muted">{r["unmatched_count"]} Sleeper players have no FPL match '
                 f'and no projection. Fix them in overrides.json.</div>'
                 + (f'<p>On your roster:</p><ul>{um_list}</ul>' if um else "") + "</div>")

    body = "\n".join(parts)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>GW{_e(r["gameweek"] or "")} lineup</title><style>{CSS}</style></head>'
            f'<body><main>{body}</main><script>{SORT_JS}</script></body></html>')


def _atomic_write(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_outputs(report, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_dir / "report.json", json.dumps(report, indent=2))
    _atomic_write(output_dir / "index.html", render_html(report))
