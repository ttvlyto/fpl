"""Link Sleeper player ids to FPL element ids.

Matching is conservative: if a name step finds more than one candidate even
after narrowing by position, the player stays unmatched rather than guessed.
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field

from .config import ConfigError

FPL_POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
# Letters NFKD does not decompose into an ASCII base.
_SPECIAL = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ß": "ss", "đ": "d",
                          "Đ": "d", "ł": "l", "Ł": "l", "ı": "i", "œ": "oe", "Œ": "oe"})


@dataclass
class MatchResult:
    mapping: dict = field(default_factory=dict)    # sleeper_id -> fpl_id
    method: dict = field(default_factory=dict)     # sleeper_id -> how it matched
    unmatched: list = field(default_factory=list)  # sleeper_ids


def normalize_name(s):
    s = unicodedata.normalize("NFKD", (s or "").translate(_SPECIAL))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z]+", " ", s)
    return " ".join(s.split())


def load_overrides(path):
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {"players": {}, "clubs": {}}
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}")
    try:
        if not isinstance(raw, dict):
            raise TypeError("top level must be an object")
        return {"players": {str(k): int(v) for k, v in (raw.get("players") or {}).items()},
                "clubs": {str(k): str(v) for k, v in (raw.get("clubs") or {}).items()}}
    except (TypeError, ValueError, AttributeError) as e:
        raise ConfigError(f"Bad overrides in {path}: {e} "
                          '(expected {"players": {"<sleeper id>": <fpl id>}, "clubs": {"<code>": "<FPL code>"}})')


def _full(e):
    return normalize_name(f"{e['first_name']} {e['second_name']}")


def _steps(name):
    """(method, predicate on an FPL element) in priority order."""
    tokens = name.split()
    last = tokens[-1] if tokens else ""
    first_initial = name[:1]

    def last_initial(e):
        second = normalize_name(e["second_name"]).split()
        first = normalize_name(e["first_name"])
        return len(tokens) > 1 and bool(second) and second[-1] == last and first[:1] == first_initial

    def web(e):
        w = normalize_name(e["web_name"])
        return w in (name, last)

    return [("exact", lambda e: _full(e) == name), ("last+initial", last_initial), ("web_name", web)]


def match_players(sleeper_players, elements, teams, overrides):
    overrides = overrides or {}
    player_over = overrides.get("players") or {}
    club_alias = overrides.get("clubs") or {}
    team_code = {t["team_id"]: t["short_name"] for t in teams}
    by_club = {}
    for e in elements:
        by_club.setdefault(team_code.get(e["team_id"]), []).append(e)

    claims = {}  # sleeper_id -> (fpl_id, method)
    for p in sleeper_players:
        sid = p["player_id"]
        if sid in player_over:
            claims[sid] = (player_over[sid], "override")
            continue
        club = club_alias.get(p.get("team"), p.get("team"))
        candidates = by_club.get(club, [])
        name = normalize_name(p.get("name"))
        wanted = set(p.get("positions") or [])
        for method, pred in _steps(name):
            hits = [e for e in candidates if pred(e)]
            if len(hits) > 1 and wanted:
                hits = [e for e in hits if FPL_POSITIONS.get(e["element_type"]) in wanted]
            if len(hits) == 1:
                claims[sid] = (hits[0]["fpl_id"], method)
                break
            if len(hits) > 1:
                break  # ambiguous: never guess

    # An FPL id may belong to one Sleeper player; overrides win contested ids.
    owners = {}
    for sid, (fid, method) in claims.items():
        owners.setdefault(fid, []).append((sid, method))

    result = MatchResult()
    for fid, holders in owners.items():
        forced = [h for h in holders if h[1] == "override"]
        winners = forced if forced else holders
        if len(winners) == 1:
            sid, method = winners[0]
            result.mapping[sid] = fid
            result.method[sid] = method
    result.unmatched = [p["player_id"] for p in sleeper_players if p["player_id"] not in result.mapping]
    return result
