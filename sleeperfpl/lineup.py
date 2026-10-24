"""Best legal starting XI, swap tips, and free-agent ranking."""

from dataclasses import dataclass

from .config import ConfigError

NON_STARTING = {"BN", "IR", "TAXI"}
# Filling a slot always beats any projection difference; avg only breaks ties.
_FILL = 1e6
_TIE = 1e-6


@dataclass
class Lineup:
    starters: list          # [(slot, PlayerCtx | None)], in roster_positions order
    bench: list             # PlayerCtx, best projection first
    total: float
    empty_slots: list       # slot names nobody could fill


def starting_slots(roster_positions, eligibility):
    slots = [s for s in roster_positions if s not in NON_STARTING]
    unknown = sorted({s for s in slots if s not in eligibility})
    if unknown:
        raise ConfigError(f"Unknown lineup slot(s) {', '.join(unknown)} in the league's roster_positions; "
                          f"add them to slot_eligibility in config.json, e.g. "
                          f'"{unknown[0]}": ["DEF", "MID", "FWD"]')
    return slots


def _hungarian(cost):
    """Min-cost assignment for an n x m matrix, n <= m. Returns row -> column."""
    n, m = len(cost), len(cost[0])
    inf = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    assignment = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment


def _eligible(player, slot, eligibility):
    return bool(set(player.positions) & set(eligibility[slot]))


def best_xi(players, slots, eligibility):
    pool = sorted((pl for pl in players if not pl.in_reserve), key=lambda pl: pl.name)
    if not slots:
        return Lineup([], sorted(pool, key=lambda pl: -pl.proj), 0.0, [])
    width = max(len(pool), len(slots))
    cost = []
    for slot in slots:
        row = []
        for j in range(width):
            if j < len(pool) and _eligible(pool[j], slot, eligibility):
                pl = pool[j]
                row.append(-(_FILL + pl.proj + _TIE * (pl.avg or 0.0)))
            else:
                row.append(0.0)  # leaving the slot empty
        cost.append(row)
    assignment = _hungarian(cost)

    starters, empty, used = [], [], set()
    for slot, j in zip(slots, assignment):
        if j < len(pool) and _eligible(pool[j], slot, eligibility):
            starters.append((slot, pool[j]))
            used.add(j)
        else:
            starters.append((slot, None))
            empty.append(slot)
    bench = sorted((pl for j, pl in enumerate(pool) if j not in used),
                   key=lambda pl: (-pl.proj, -(pl.avg or 0.0), pl.name))
    total = sum(pl.proj for _, pl in starters if pl)
    return Lineup(starters, bench, total, empty)
