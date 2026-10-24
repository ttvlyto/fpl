"""Load config.json: league, owner, paths, and every tunable."""

import json
import os
from dataclasses import dataclass
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
    "slot_eligibility": {"GK": ["GK"], "DEF": ["DEF"], "MID": ["MID"], "FWD": ["FWD"]},
    "fpl_request_interval": 0.5,
}
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

    base = cfg_path.resolve().parent
    paths = {k: base / merged[k] for k in PATH_KEYS}
    try:
        return Config(
            league_id=str(merged["league_id"]),
            owner=str(merged["owner"]),
            difficulty_multipliers={int(k): float(v) for k, v in multipliers.items()},
            avg_window=int(merged["avg_window"]),
            free_agents_per_position=int(merged["free_agents_per_position"]),
            swap_min_gain=float(merged["swap_min_gain"]),
            max_swaps=int(merged["max_swaps"]),
            slot_eligibility={str(k): list(v) for k, v in merged["slot_eligibility"].items()},
            fpl_request_interval=float(merged["fpl_request_interval"]),
            **paths,
        )
    except (TypeError, ValueError, AttributeError) as e:
        raise ConfigError(f"Bad value in {cfg_path}: {e}")
