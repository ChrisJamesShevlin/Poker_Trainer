"""Monte Carlo equity vs N random hands or a top-X% range."""
import random
from typing import List, Optional

import eval7

from .parser import RANKS, SUITS
from .ranges import combos, top_percent

FULL_DECK = [r + s for r in RANKS for s in SUITS]


def equity(hole: List[str], board: List[str], opponents: int,
           range_pct: Optional[int] = None, sims: int = 20000, seed: Optional[int] = None,
           range_classes=None):
    """Returns (win, tie, equity) as fractions. Ties count half in equity."""
    rng = random.Random(seed)
    cards = {c: eval7.Card(c) for c in FULL_DECK}
    dead = set(hole + board)
    hero = [cards[c] for c in hole]
    brd = [cards[c] for c in board]
    deck = [c for c in FULL_DECK if c not in dead]
    pool = None
    if range_pct or range_classes:
        classes = range_classes if range_classes else top_percent(range_pct)
        pool = [(a, b) for cls in classes for a, b in combos(cls)
                if a not in dead and b not in dead]
        if not pool:
            raise ValueError("Range is empty after removing known cards")
    need = 5 - len(board)
    wins = ties = 0.0
    for _ in range(sims):
        used, opp_hands = set(), []
        for _o in range(opponents):
            if pool:
                for _try in range(50):
                    a, b = rng.choice(pool)
                    if a not in used and b not in used:
                        break
                else:
                    a, b = rng.sample([c for c in deck if c not in used], 2)
            else:
                a, b = rng.sample([c for c in deck if c not in used], 2)
            used.update((a, b))
            opp_hands.append([cards[a], cards[b]])
        rest = rng.sample([c for c in deck if c not in used], need)
        full = brd + [cards[c] for c in rest]
        hv = eval7.evaluate(hero + full)
        best = max(eval7.evaluate(h + full) for h in opp_hands)
        if hv > best:
            wins += 1
        elif hv == best:
            ties += 1 / (1 + sum(eval7.evaluate(h + full) == hv for h in opp_hands))
    return wins / sims, ties / sims, (wins + ties) / sims
