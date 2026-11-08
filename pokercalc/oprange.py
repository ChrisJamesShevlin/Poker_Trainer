"""Pick a sensible opponent range automatically (a rough guess, not a read)."""
from .ranges import combos, ranking
from .preflop import OPEN

LADDER = [60, 40, 25, 15, 8]            # "top X% of hands" steps, wide -> tight
FRACTIONS = [1.0, 0.65, 0.4, 0.25, 0.15]  # share of a raiser's open chart, wide -> tight
TABLE_SHIFT = {"loose": -1, "normal": 0, "tight": 1}


def _approx_pct(classes):
    return round(100 * sum(len(combos(c)) for c in classes) / 1326)


def _subset(classes, frac):
    order = [c for c in ranking() if c in classes]
    total = sum(len(combos(c)) for c in order)
    out, got = [], 0
    for c in order:
        if out and got >= frac * total:
            break
        out.append(c)
        got += len(combos(c))
    return out


def bet_size_note(pot, call):
    before = pot - call
    ratio = call / before if before > 0 else 2.0
    word = ("small bet" if ratio < 0.4 else "medium bet" if ratio < 0.8 else "big bet")
    return ratio, word


def resolve(pot, call, board_len, raiser=None, table="normal", multi_street=False, extra_steps=0, wet=False):
    """Returns (classes, label, approx_pct_of_hands)."""
    if call > 0 and board_len >= 3:
        ratio, word = bet_size_note(pot, call)
        base = 0 if ratio < 0.4 else 1 if ratio < 0.8 else 2
        why = f"{word} ({ratio:.0%} of pot)"
    elif call > 0 and board_len == 0:
        base, why = (0 if raiser else 1), "facing a raise"
    else:
        base, why = 0, "no bet to face"
    street_steps = (board_len - 3) if multi_street and board_len >= 4 else 0
    wet_step = 1 if wet and call > 0 and board_len >= 3 else 0   # a bet on a connected board is more often a real hand
    extra = street_steps + extra_steps + wet_step
    shift = TABLE_SHIFT.get(table, 0)
    notes = [why]
    if extra_steps:
        notes.append("big raise, so tighter")
    if street_steps:
        notes.append("betting every street")
    if wet_step:
        notes.append("straight/flush possible, so tighter")
    if shift:
        notes.append(f"{table} table")
    if raiser and raiser in OPEN:
        idx = max(0, min(4, base + extra + shift))
        classes = _subset(OPEN[raiser], FRACTIONS[idx])
        label = f"{raiser} raiser's range, narrowed to {FRACTIONS[idx]:.0%} of it ({', '.join(notes)})"
        return classes, label, _approx_pct(classes)
    # no known raiser: ladder of top-X% (start at Top 40 for no/small bet)
    idx = max(0, min(4, base + 1 + extra + shift))
    pct = LADDER[idx]
    from .ranges import top_percent
    classes = top_percent(pct)
    return classes, f"Top {pct}% ({', '.join(notes)})", _approx_pct(classes)
