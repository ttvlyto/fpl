"""Dataclasses shared across sleeperfpl modules."""

from dataclasses import dataclass, field


@dataclass
class Fixture:
    opponent: str          # FPL short_name, e.g. "LIV"
    home: bool
    difficulty: int        # 1-5, from the player's side
    kickoff: str | None    # ISO UTC
    fixture_id: int | None = None


@dataclass
class PlayerCtx:
    sleeper_id: str
    name: str
    positions: list[str]           # Sleeper fantasy_positions, primary first
    club: str                      # display club code
    fpl_id: int | None             # None = unmatched
    on_roster: bool = False        # on the owner's roster
    in_reserve: bool = False       # owner's IR
    flag: str | None = None        # "doubtful" | "out" | "no data" | None
    fixtures: list[Fixture] = field(default_factory=list)
    history: list[float] = field(default_factory=list)   # league points, most recent first, minutes > 0 only
    avg: float | None = None
    n: int = 0
    proj: float = 0.0
    trending_adds: int | None = None
    undroppable: bool = False      # owner said never suggest dropping this player
    proj_v1: float = 0.0           # recent average x fixture difficulty, kept for comparison
    proj_source: str = "difficulty"  # "market" when betting odds priced at least one fixture
    market_flags: list = field(default_factory=list)  # "cs" / "goal": history and market disagree
    p_goal_market: float | None = None
    p_goal_hist: float | None = None
    ep_next: float | None = None   # FPL's own expected points (FPL scoring), a free baseline
    # Minutes model (shadow unless minutes_model is "on"); None when no model or no Sleeper stats.
    p_start: float | None = None
    p_app: float | None = None
    p60: float | None = None       # first fixture's
    e_min: float | None = None
    fpl_bucket: str | None = None
    slp_bucket: str | None = None
    proj_v2: float | None = None   # market projection, kept when minutes_model "on" replaces proj
    proj_v1m: float | None = None
    proj_v2m: float | None = None
    p_start_model: float | None = None   # before labels
    p_app_model: float | None = None
    p_start_fpl: float | None = None     # after FPL's label, before Sleeper's
    p_app_fpl: float | None = None

    def value(self) -> float:
        """Blend of recent output and projection, used for bench comparisons."""
        return ((self.avg or 0.0) + self.proj) / 2
