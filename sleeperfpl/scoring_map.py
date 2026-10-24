"""Editable mapping from the league's Sleeper scoring keys to FPL stats.

scoring_map.json shape:
    {"rules": {"<sleeper key>": {"fpl_field": "goals_scored" | null, "points": 6,
                                 "min": 60,          # optional: award once if stat >= min
                                 "per": 2,           # optional: points per `per` units
                                 "positions": ["DEF"]}}}  # optional: only these positions
"""

import json
import math
from dataclasses import dataclass

from .config import ConfigError
from .http import FetchError, get_json

FPL_FIELDS = ["minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded", "own_goals",
              "penalties_saved", "penalties_missed", "yellow_cards", "red_cards", "saves", "bonus",
              "bps", "tackles", "recoveries", "clearances_blocks_interceptions", "defensive_contribution"]

# (patterns, field), first match wins. Patterns of 3 letters or fewer must equal
# a whole "_"-separated token; longer ones match as substrings.
SUGGESTIONS = [
    (("own_goal",), "own_goals"),
    (("pen_save", "penalty_save", "ps"), "penalties_saved"),
    (("pen_miss", "penalty_miss", "pm"), "penalties_missed"),
    (("clean_sheet", "cs"), "clean_sheets"),
    (("conced",), "goals_conceded"),
    (("goal",), "goals_scored"),
    (("assist", "ast"), "assists"),
    (("yellow", "yc"), "yellow_cards"),
    (("red", "rc"), "red_cards"),
    (("save",), "saves"),
    (("bonus",), "bonus"),
    (("tackle", "tkl"), "tackles"),
    (("recover",), "recoveries"),
    (("clearance", "block", "intercept"), "clearances_blocks_interceptions"),
    (("minute", "min_", "appear"), "minutes"),
]
POSITION_SUFFIXES = {"gk": "GK", "def": "DEF", "mid": "MID", "fwd": "FWD"}


@dataclass
class ScoringRule:
    key: str
    fpl_field: str | None
    points: float
    min: float | None = None
    per: float | None = None
    positions: list[str] | None = None


def _suggest(key):
    k = key.lower()
    tokens = k.split("_")
    for patterns, fpl_field in SUGGESTIONS:
        for pat in patterns:
            if (pat in tokens) if len(pat) <= 3 else (pat in k):
                return fpl_field
    return None


def generate_scoring_map(scoring_settings):
    rules = {}
    for key in sorted(scoring_settings):
        rule = {"fpl_field": _suggest(key), "points": scoring_settings[key]}
        suffix = key.lower().rsplit("_", 1)[-1]
        if "_" in key and suffix in POSITION_SUFFIXES:
            rule["positions"] = [POSITION_SUFFIXES[suffix]]
        rules[key] = rule
    return {"rules": rules}


def ensure_scoring_map(path, scoring_settings):
    """Write a generated map only if none exists. Returns True if it wrote one."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(generate_scoring_map(scoring_settings), indent=2) + "\n")
    return True


def load_scoring_map(path):
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path} not found; run fetch-daily to generate it")
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}")
    rules = []
    for key, r in (raw.get("rules") or {}).items():
        field = r.get("fpl_field")
        if field is not None and field not in FPL_FIELDS:
            raise ConfigError(f"{path}: rule '{key}' has unknown fpl_field '{field}' "
                              f"(valid: {', '.join(FPL_FIELDS)}, or null)")
        try:
            rules.append(ScoringRule(key=key, fpl_field=field, points=float(r.get("points", 0)),
                                     min=r.get("min"), per=r.get("per"), positions=r.get("positions")))
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{path}: rule '{key}' has a bad value: {e}")
    return rules


def score_match(stats, position, rules):
    total = 0.0
    for r in rules:
        if r.fpl_field is None or (r.positions and position not in r.positions):
            continue
        value = stats.get(r.fpl_field)
        if value is None:
            continue
        value = float(value)
        if r.min is not None:
            total += r.points if value >= r.min else 0.0
        elif r.per:
            total += r.points * math.floor(value / r.per)
        else:
            total += r.points * value
    return total


def coverage(rules):
    """(covered keys, missing keys). Zero-point rules don't affect scores, so count as covered."""
    covered = [r.key for r in rules if r.fpl_field is not None or r.points == 0]
    missing = [r.key for r in rules if r.fpl_field is None and r.points != 0]
    return covered, missing


def probe_sleeper_stats(sport, season, week, get=get_json):
    """Check whether Sleeper's undocumented stats endpoints answer for this sport."""
    urls = [f"https://api.sleeper.app/v1/stats/{sport}/regular/{season}/{week}",
            f"https://api.sleeper.com/stats/{sport}/{season}/{week}?season_type=regular"]
    lines = []
    for url in urls:
        try:
            data = get(url, retries=0, timeout=30)
            count = len(data) if isinstance(data, (list, dict)) else 0
            lines.append(f"{url}: OK, {count} items")
        except FetchError as e:
            lines.append(f"{url}: failed ({e})")
        except Exception as e:  # probe must never break the command
            lines.append(f"{url}: failed ({e})")
    return lines
