"""Load config.json: league, owner, paths, and every tunable."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = "config.json"
ENV_VAR = "SLEEPER_FPL_CONFIG"

DEFAULTS = {
    "db_path": "data/fpl.db",
    "output_dir": "site",
    "scoring_map_path": "scoring_map.json",
    "overrides_path": "overrides.json",
    "difficulty_multipliers": {"1": 1.2, "2": 1.1, "3": 1.0, "4": 0.9, "5": 0.8},
    "avg_window": 5,
    "free_agents_per_position": 10,
    "swap_min_gain": 1.0,
    "max_swaps": 3,
    # Sleeper soccer slots (F/M/D/GK and flex slots); a few players are listed as "DEF".
    "slot_eligibility": {"GK": ["GK"], "D": ["D", "DEF"], "M": ["M"], "F": ["F"],
                         "FM_FLEX": ["F", "M"], "MD_FLEX": ["M", "D", "DEF"], "FMD_FLEX": ["F", "M", "D", "DEF"],
                         "DEF": ["D", "DEF"], "MID": ["M", "MID"], "FWD": ["F", "FWD"]},
    "fpl_request_interval": 0.5,
    "stats_source": "sleeper",     # "sleeper" (exact league points) or "fpl" (approximate, via scoring_map.json)
    "undroppable": [],             # player names or Sleeper ids never suggested as drops
    # Betting markets: ignore quotes wider than this spread or thinner than this depth
    # (Kalshi: contracts at the best bid+ask; Polymarket: USD liquidity).
    "market_max_spread": 0.10,
    "market_min_depth": {"kalshi": 50, "polymarket": 1000},
    "market_stale_hours": 24,      # older prices are shown as stale and not used
    "matchday_window_hours": 3,    # match-day runs refresh fixtures kicking off this soon
    "league_avg_goals": 1.35,      # team expected-goals baseline when no market history exists
    "flag_cs_gap": 0.25,           # flag when history and market clean-sheet chances differ this much
    "flag_goal_gap": 0.20,         # same for a player's chance to score
    # Report card outcome groups, in league points: low < category_low <= mid < category_haul <= haul.
    "category_low": 3,
    "category_haul": 10,
    # Expected minutes: "shadow" computes and logs v1m/v2m; "on" also uses them for picks.
    "minutes_model": "shadow",
    "label_prior_k": 20,           # pseudo-observations behind each label's starting guess
    "sleeper_label_weight": 1.0,   # 0 ignores Sleeper's injury labels in the minutes model
    "minutes_shrink_k": 3,         # pseudo-90s pulling v1m/v2m per-90 rates toward the position average
    # Claude's second opinion: once per game week, lead_hours before its first kickoff.
    "second_opinion": {"enabled": False, "model": "claude-opus-5-5", "effort": "high", "lead_hours": 48},
}
MINUTES_MODES = ("shadow", "on")
STATS_SOURCES = ("sleeper", "fpl")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
REQUIRED = ("league_id", "owner")
PATH_KEYS = ("db_path", "output_dir", "scoring_map_path", "overrides_path")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    league_id: str
    owner: str
    db_path: Path
    output_dir: Path
    scoring_map_path: Path
    overrides_path: Path
    difficulty_multipliers: dict[int, float]
    avg_window: int
    free_agents_per_position: int
    swap_min_gain: float
    max_swaps: int
    slot_eligibility: dict[str, list[str]]
    fpl_request_interval: float
    stats_source: str = "sleeper"
    undroppable: list = None
    market_max_spread: float = 0.10
    market_min_depth: dict = field(default_factory=lambda: dict(DEFAULTS["market_min_depth"]))
    market_stale_hours: float = 24
    matchday_window_hours: float = 3
    league_avg_goals: float = 1.35
    flag_cs_gap: float = 0.25
    flag_goal_gap: float = 0.20
    category_low: float = 3
    category_haul: float = 10
    minutes_model: str = "shadow"
    label_prior_k: float = 20
    sleeper_label_weight: float = 1.0
    minutes_shrink_k: float = 3
    second_opinion: dict = field(default_factory=lambda: dict(DEFAULTS["second_opinion"]))


def _second_opinion(raw, cfg_path):
    so = {**DEFAULTS["second_opinion"], **raw}
    try:
        so["enabled"], so["model"], so["lead_hours"] = bool(so["enabled"]), str(so["model"]), float(so["lead_hours"])
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{cfg_path}: bad second_opinion value: {e}")
    if so["effort"] not in EFFORTS:
        raise ConfigError(f"{cfg_path}: second_opinion effort must be one of {', '.join(EFFORTS)}")
    if so["lead_hours"] <= 0:
        raise ConfigError(f"{cfg_path}: second_opinion lead_hours must be more than 0")
    return so


def load_config(path: str | None = None) -> Config:
    """Read config from `path`, else $SLEEPER_FPL_CONFIG, else ./config.json."""
    cfg_path = Path(path or os.environ.get(ENV_VAR) or DEFAULT_PATH)
    try:
        raw = json.loads(cfg_path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {cfg_path} (copy config.example.json)")
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {cfg_path}: {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{cfg_path} must contain a JSON object")

    for key in REQUIRED:
        if not raw.get(key):
            raise ConfigError(f"{cfg_path} is missing required key '{key}'")

    merged = {**DEFAULTS, **raw}
    # Partial multiplier overrides keep the defaults for the other levels.
    multipliers = {**DEFAULTS["difficulty_multipliers"], **raw.get("difficulty_multipliers", {})}

    if merged["stats_source"] not in STATS_SOURCES:
        raise ConfigError(f"{cfg_path}: stats_source must be one of {', '.join(STATS_SOURCES)}")
    if not isinstance(merged["undroppable"], list) or not all(isinstance(x, str) for x in merged["undroppable"]):
        raise ConfigError(f'{cfg_path}: undroppable must be a list of names or ids, e.g. ["Bukayo Saka"]')

    base = cfg_path.resolve().parent
    paths = {k: base / merged[k] for k in PATH_KEYS}
    try:
        cfg = Config(
            league_id=str(merged["league_id"]),
            owner=str(merged["owner"]),
            difficulty_multipliers={int(k): float(v) for k, v in multipliers.items()},
            avg_window=int(merged["avg_window"]),
            free_agents_per_position=int(merged["free_agents_per_position"]),
            swap_min_gain=float(merged["swap_min_gain"]),
            max_swaps=int(merged["max_swaps"]),
            slot_eligibility={str(k): list(v) for k, v in merged["slot_eligibility"].items()},
            fpl_request_interval=float(merged["fpl_request_interval"]),
            stats_source=merged["stats_source"],
            undroppable=list(merged["undroppable"]),
            market_max_spread=float(merged["market_max_spread"]),
            market_min_depth={**DEFAULTS["market_min_depth"],
                              **{str(k): float(v) for k, v in raw.get("market_min_depth", {}).items()}},
            market_stale_hours=float(merged["market_stale_hours"]),
            matchday_window_hours=float(merged["matchday_window_hours"]),
            league_avg_goals=float(merged["league_avg_goals"]),
            flag_cs_gap=float(merged["flag_cs_gap"]),
            flag_goal_gap=float(merged["flag_goal_gap"]),
            category_low=float(merged["category_low"]),
            category_haul=float(merged["category_haul"]),
            minutes_model=merged["minutes_model"],
            label_prior_k=float(merged["label_prior_k"]),
            sleeper_label_weight=float(merged["sleeper_label_weight"]),
            minutes_shrink_k=float(merged["minutes_shrink_k"]),
            second_opinion=_second_opinion(raw.get("second_opinion") or {}, cfg_path),
            **paths,
        )
    except (TypeError, ValueError, AttributeError) as e:
        raise ConfigError(f"Bad value in {cfg_path}: {e}")
    if cfg.minutes_model not in MINUTES_MODES:
        raise ConfigError(f"{cfg_path}: minutes_model must be one of {', '.join(MINUTES_MODES)}")
    if not 0 <= cfg.sleeper_label_weight <= 1:
        raise ConfigError(f"{cfg_path}: sleeper_label_weight must be between 0 and 1")
    if not cfg.category_low < cfg.category_haul:
        raise ConfigError(f"{cfg_path}: category_low must be less than category_haul")
    return cfg
