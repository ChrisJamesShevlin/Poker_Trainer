"""Simple 6-max preflop guide built from hand charts. A rough guide, NOT a solver.

Charts are approximate, commonly published ~100bb 6-max ranges. Medium stacks
(15-40bb) use the same chart trimmed to its stronger hands; short stacks (<15bb)
use a push/fold list.
"""
import math
import re

from .parser import RANKS
from .ranges import combos, hole_class, ranking

POSITIONS = ["UTG", "HJ", "CO", "BTN", "SB", "BB"]
_R = {r: i for i, r in enumerate(RANKS)}


def expand(spec):
    """'22+, A2s+, KTs+, JTs, AJo+, 66-TT' -> set of hand classes like {'AA','AKs','AJo'}."""
    out = set()
    for tok in (t.strip() for t in spec.split(",") if t.strip()):
        m = re.fullmatch(r"([2-9TJQKA])([2-9TJQKA])([so]?)(\+|-([2-9TJQKA])([2-9TJQKA])[so]?)?", tok)
        if not m:
            raise ValueError(f"bad chart token {tok!r}")
        a, b, kind, suffix, c, d = m.groups()
        if a != b and suffix and suffix.startswith("-"):   # e.g. A5s-A2s: same high card, kickers b..d
            hi_r, lo1, lo2 = a, b, d
            for i in range(min(_R[lo1], _R[lo2]), max(_R[lo1], _R[lo2]) + 1):
                out.add(hi_r + RANKS[i] + kind)
            continue
        if a == b:  # pairs
            lo, hi = _R[a], _R[a]
            if suffix == "+":
                hi = len(RANKS) - 1
            elif suffix:
                hi = max(_R[a], _R[c])
                lo = min(_R[a], _R[c])
            for i in range(lo, hi + 1):
                out.add(RANKS[i] * 2)
        else:
            hi_r, lo_r = (a, b) if _R[a] > _R[b] else (b, a)
            lows = [lo_r]
            if suffix == "+":
                lows = [RANKS[i] for i in range(_R[lo_r], _R[hi_r])]
            for l in lows:
                out.add(hi_r + l + kind)
    return out


def _n(cs):
    return sum(len(combos(c)) for c in cs)


# Raise-first-in (open) charts, ~100bb 6-max. Source: PokerSkill opening ranges
# (https://www.pokerskill.com/charts/opening-ranges/): UTG 14.3%, HJ 18.9%, CO 25.5%, BTN 43.3%, SB 33.0%.
OPEN = {
    "UTG": expand("22+, ATs+, A5s-A2s, KTs+, QTs+, JTs, T9s, 98s, AJo+, KQo"),
    "HJ": expand("22+, A2s+, K9s+, Q9s+, J9s+, T8s+, 98s, 87s, ATo+, KJo+"),
    "CO": expand("22+, A2s+, K5s+, Q8s+, J8s+, T8s+, 97s+, 87s, 76s, 65s, 54s, A8o+, KTo+, QJo"),
    "BTN": expand("22+, A2s+, K2s+, Q4s+, J6s+, T6s+, 95s+, 85s+, 75s+, 64s+, 54s, A2o+, K8o+, Q9o+, J9o+, T9o, 98o"),
    "SB": expand("22+, A2s+, K4s+, Q6s+, J7s+, T7s+, 96s+, 86s+, 76s, 65s, 54s, A5o+, K9o+, QTo+, JTo"),
}
# Facing a single raise: re-raise (3-bet) and call charts by your position
# Vs a CO/BTN/SB open: JJ+ and AK for value (PokerSkill's BTN-vs-CO chart also adds many suited bluffs; we keep A5s/A4s).
# Vs a UTG/HJ open the value range tightens to QQ+ and AK (same source's guidance).
THREE_BET = expand("JJ+, AKs, AKo, A5s, A4s")
THREE_BET_VS_EARLY = expand("QQ+, AKs, AKo")
# Small blind vs an early-position open: re-raise or fold, never flat (PokerSkill 3-bet chart, 4.8% of hands)
SB_VS_EARLY = expand("JJ+, AKo, AJs+, A5s-A2s")
CALL = {
    "UTG": expand("66-TT, ATs+, KQs, KJs, QJs, JTs, T9s, AQo"),
    "HJ": expand("66-TT, ATs+, KJs+, QJs, JTs, T9s, 98s, AQo"),
    "CO": expand("55-TT, A9s+, KTs+, QTs+, JTs, T9s, 98s, 87s, AQo, KQo"),
    # BTN: PokerSkill's "button vs cutoff open" calling chart (14.0% of hands)
    "BTN": expand("22-TT, A9s-A6s, AQs, AJs, ATs, KTs, K9s, QJs, QTs, Q9s, JTs, J9s, T9s, T8s, 98s, 87s, AQo, AJo, KQo, KJo, QJo"),
    "SB": expand("77-TT, ATs+, KQs, QJs, JTs, AQo"),
    "BB": expand("22-TT, A2s+, K5s+, Q7s+, J8s+, T8s+, 97s+, 86s+, 75s+, 65s, 54s, ATo+, KTo+, QTo+, JTo"),
}
# Facing a BIG raise (about 4-12bb, usually a 3-bet): re-raise only the top, call a tighter slice
FOURBET = expand("QQ+, AKs, AKo")
BIG_CONTINUE_PCT = {"UTG": 5, "HJ": 5, "CO": 6, "BTN": 7, "SB": 5, "BB": 8}   # top X% of hands
# Facing a HUGE raise or shove (12bb+, or 40%+ of your stack): only the very top continues
SHOVE_CALL = expand("QQ+, AKs, AKo")
# Short stack (<15bb) push list as a % of hands (by strength); BB = calling a shove
SHOVE = {"UTG": 10, "HJ": 13, "CO": 18, "BTN": 30, "SB": 40, "BB": 22}


def stack_band(bb):
    if bb is None:
        return "unknown"
    return "short" if bb < 15 else "medium" if bb < 40 else "deep"


def percentile(cls):
    """Cumulative % of all hands that are as strong or stronger."""
    got = 0
    for c in ranking():
        got += len(combos(c))
        if c == cls:
            return 100 * got / 1326


def tier(pct):
    return ("Premium" if pct <= 3 else "Strong" if pct <= 10 else "Playable" if pct <= 25
            else "Speculative" if pct <= 45 else "Weak")


def describe_class(cls):
    if len(cls) == 2:
        return f"pocket pair ({cls[0]}s)"
    hi, lo, kind = cls
    gap = _R[hi] - _R[lo]
    suited = kind == "s"
    if hi == "A" and suited:
        return "suited ace"
    if hi in "AKQJT" and lo in "AKQJT":
        return "suited broadway" if suited else "offsuit broadway"
    if gap == 1:
        return "suited connector" if suited else "offsuit connector"
    if gap == 2 and suited:
        return "suited one-gapper"
    return "suited hand" if suited else "offsuit hand"


def _trim(chart, bb):
    """15-25bb: keep only the stronger ~85% of the chart's hands."""
    if bb is None or bb >= 25:
        return chart
    order = [c for c in ranking() if c in chart]
    return set(order[: math.ceil(len(order) * 0.85)])


def raise_info(call, bb, stack, position):
    """Works out the raise size from only 'bet to call'. Returns dict(raise_bb, tier, facing).

    tier: 'none' (nothing to face), 'limp', 'normal' (<=4bb), 'big' (4-12bb), 'shove' (12bb+ or 40%+ of stack).
    Blinds already posted are added back, since 'bet to call' only counts what you still owe.
    """
    if not call or call <= 0:
        return {"raise_bb": None, "tier": "none", "facing": False}
    posted = (bb if position == "BB" else bb / 2 if position == "SB" else 0) if bb else 0
    raise_bb = (call + posted) / bb if bb else None
    stack_share = call / stack if stack else None
    if (raise_bb is not None and raise_bb >= 12) or (stack_share is not None and stack_share >= 0.4):
        tier = "shove"
    elif raise_bb is None:
        tier = "normal"
    elif raise_bb > 4:
        tier = "big"
    elif raise_bb > 1.05:
        tier = "normal"
    else:
        tier = "limp"
    return {"raise_bb": raise_bb, "tier": tier, "facing": tier in ("normal", "big", "shove")}


def advise(hole, position, stack_bb, facing_raise, size_tier="normal", raise_bb=None, raiser=None, limpers=False, opps=1):
    cls = hole_class(*hole)
    rank = ranking().index(cls) + 1
    pct = percentile(cls)
    band = stack_band(stack_bb)
    head = (f"{cls} - {tier(pct)} {describe_class(cls)}: rank {rank}/169 (top {pct:.0f}% of hands). "
            f"Stack: {band}" + (f" ({stack_bb:.0f}bb)" if stack_bb else "") + f". Position {position}.")
    if band == "short":
        lim = SHOVE[position]
        if pct <= lim:
            act, why = "RAISE", f"short stack: shove/call-shove range ~top {lim}% from {position} - this hand is in it"
        else:
            act, why = "FOLD", f"outside the ~top {lim}% short-stack range for {position}"
    elif not facing_raise and limpers:
        # limped pot: raise to isolate with strong hands, otherwise take a cheap flop with
        # pairs and suited hands (they win big when they hit), fold the rest
        iso = 10 if opps >= 4 else 18          # more players in = tighter raising range
        chart = _trim(OPEN[position], stack_bb) if position != "BB" else set()
        cheap = len(cls) == 2 or cls.endswith("s")
        if pct <= iso:
            act, why = "RAISE", "Limpers ahead, strong hand: raise"
        elif cheap and (position == "BB" or cls in chart):
            act, why = "CALL" if position != "BB" else "CHECK", \
                (f"Limpers ahead: {'small pair' if len(cls) == 2 else 'suited hand'}. See a cheap flop, don't raise")
        elif position == "BB":
            act, why = "CHECK", "Limpers ahead: free flop"
        else:
            act, why = "FOLD", "Limpers ahead, hand too weak: fold"
    elif not facing_raise:
        if position == "BB":
            act, why = "CHECK", "nobody raised; you see a free flop"
        else:
            chart = _trim(OPEN[position], stack_bb)
            share = 100 * _n(chart) / 1326
            if cls in chart:
                act, why = "RAISE", f"in the {position} open chart (~{share:.0f}% of hands)"
            else:
                act, why = "FOLD", f"not in the {position} open chart (~{share:.0f}% of hands)"
    elif size_tier == "shove":
        size = f"raise of ~{raise_bb:.0f}bb" if raise_bb else "huge raise / shove"
        if cls in SHOVE_CALL:
            act, why = "CALL", f"{size}: only QQ+ and AK continue - this hand is in that group"
        else:
            act, why = "FOLD", f"{size}: only QQ+ and AK continue"
    elif size_tier == "big":
        size = f"big raise (~{raise_bb:.1f}bb)" if raise_bb else "big raise"
        if cls in FOURBET:
            act, why = "RAISE", f"{size}: premium - re-raise"
        elif pct <= BIG_CONTINUE_PCT[position]:
            act, why = "CALL", f"{size}: continue with the top ~{BIG_CONTINUE_PCT[position]}% of hands - this hand qualifies"
        else:
            act, why = "FOLD", f"{size}: outside the top ~{BIG_CONTINUE_PCT[position]}% you can continue with"
    else:
        early = raiser in ("UTG", "HJ")      # earlier-position raisers have stronger ranges
        if position == "BB":
            from .bbdefense import defend_set   # derived from pot odds, see bbdefense.py
            call = _trim(defend_set(raiser or "CO"), stack_bb)
        else:
            call = _trim(CALL[position], stack_bb)
        if early and position != "BB":
            call = call | {"JJ"}             # JJ calls (not re-raises) an early-position open
            order = [c for c in ranking() if c in call]
            call = set(order[: math.ceil(len(order) * 0.7)])
        if position == "SB" and early:
            three_bet, call = SB_VS_EARLY, set()
        else:
            three_bet = THREE_BET_VS_EARLY if early else THREE_BET
        src = " (derived from pot odds)" if position == "BB" else ""
        if cls in three_bet:
            act, why = "RAISE", ("premium: re-raise" if early else "premium or good bluff-3-bet hand: re-raise")
        elif cls in call:
            act, why = "CALL", f"in the {position} calling chart vs a raise" + (f" from {raiser}" if raiser else "") + src
        else:
            act, why = "FOLD", f"not in the {position} calling chart vs a raise" + (f" from {raiser}" if raiser else "") + src
    return {"action": act, "text": head, "why": why, "class": cls, "rank": rank, "pct": pct}
