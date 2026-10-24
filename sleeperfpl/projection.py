"""v1 projection: recent average scaled by fixture difficulty.

This is the one swappable piece. A prediction model can replace `project`
(or run beside it) without touching fetchers, lineup or report code.
"""

from dataclasses import dataclass


@dataclass
class Projection:
    avg: float | None
    n: int
    proj: float


def project(history, difficulties, ruled_out, multipliers, window=5):
    """`history`: league points for matches with minutes > 0, most recent first.
    `difficulties`: one entry per fixture this game week (empty = blank, two = double)."""
    recent = list(history[:window])
    avg = sum(recent) / len(recent) if recent else None
    if ruled_out or avg is None or not difficulties:
        return Projection(avg, len(recent), 0.0)
    return Projection(avg, len(recent), sum(avg * multipliers[d] for d in difficulties))
