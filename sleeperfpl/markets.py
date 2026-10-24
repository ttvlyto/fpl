"""Betting-market helpers: team codes, quote usability, fixture matching."""

from .gameweek import parse_time

KALSHI_ALIASES = {"BRI": "BHA", "CFC": "CHE", "LFC": "LIV"}
POLYMARKET_ALIASES = {"AST": "AVL", "BRI": "BHA", "MAC": "MCI", "NOT": "NFO"}
BUILTIN_ALIASES = {"kalshi": KALSHI_ALIASES, "polymarket": POLYMARKET_ALIASES}
MATCH_WINDOW_HOURS = 36


def team_code(source, raw, fpl_codes, overrides):
    """Map a market's team code to an FPL short_name, or None if unknown."""
    code = (raw or "").upper()
    forced = (overrides or {}).get("market_teams", {}).get(f"{source}:{code}")
    if forced:
        return forced
    alias = BUILTIN_ALIASES.get(source, {}).get(code)
    if alias:
        return alias
    return code if code in fpl_codes else None


def usable(q, cfg):
    """A quote is trusted only if it's two-sided, tight and deep enough."""
    bid, ask, depth = q.get("bid"), q.get("ask"), q.get("depth")
    if bid is None or ask is None or depth is None:
        return False
    if not (0 < bid <= ask < 1):
        return False
    return ask - bid <= cfg.market_max_spread and depth >= cfg.market_min_depth.get(q["source"], float("inf"))


def mid(q):
    return (q["bid"] + q["ask"]) / 2


def find_fixture(home, away, kickoff, fixtures, teams):
    """The FPL fixture with these home/away codes kicking off within ±36 h."""
    when = parse_time(kickoff)
    for f in fixtures:
        if teams.get(f["team_h"]) != home or teams.get(f["team_a"]) != away:
            continue
        k = parse_time(f.get("kickoff_time"))
        if when and k and abs((k - when).total_seconds()) <= MATCH_WINDOW_HOURS * 3600:
            return f
    return None
