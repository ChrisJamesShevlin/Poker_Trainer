"""Big-blind defence, derived from pot odds - NOT a published chart (none is freely available).

For each raiser's seat we measure every starting hand's equity against that seat's open range
(PokerSkill chart, see preflop.OPEN), add a small bonus for suited hands (they realise their equity
better out of position), and defend the best-scoring hands up to a target share of all hands.
Candidates are limited to the published button opening range (hands that are playable at all - this keeps
dominated junk like K4o out). Targets follow the common guidance that the big blind defends roughly 40-50%
against a button open and less against earlier seats. Equities are computed once, cached in bb_equity.json.
"""
import json
import random
from pathlib import Path

import eval7

from .parser import RANKS, SUITS
from .ranges import combos, hand_classes

_CACHE = Path(__file__).with_name("bb_equity.json")
# share of ALL hands the big blind continues with (calls + re-raises) vs a single open
TARGET = {"UTG": 28, "HJ": 31, "CO": 36, "BTN": 43, "SB": 43}
SUITED_BONUS = 0.025


def _equity(cls, pool, rng, sims):
    hero = combos(cls)[0]
    h = [eval7.Card(c) for c in hero]
    dead = set(hero)
    cards = {r + s: eval7.Card(r + s) for r in RANKS for s in SUITS}
    deck = [c for c in cards if c not in dead]
    pool = [(a, b) for a, b in pool if a not in dead and b not in dead]
    tot = 0.0
    for _ in range(sims):
        a, b = rng.choice(pool)
        rest = rng.sample([c for c in deck if c != a and c != b], 5)
        board = [cards[c] for c in rest]
        hv, vv = eval7.evaluate(h + board), eval7.evaluate([cards[a], cards[b]] + board)
        tot += 1 if hv > vv else 0.5 if hv == vv else 0
    return tot / sims


def build(sims=2500):
    from .preflop import OPEN
    rng = random.Random(2024)
    out = {}
    for raiser, chart in OPEN.items():
        pool = [pair for cls in chart for pair in combos(cls)]
        out[raiser] = {cls: _equity(cls, pool, rng, sims) for cls in hand_classes()}
    _CACHE.write_text(json.dumps(out))
    return out


def _data():
    if not _CACHE.exists():
        return build()
    return json.loads(_CACHE.read_text())


def defend_set(raiser):
    """Hand classes the big blind continues with (call or re-raise) against `raiser`'s open."""
    raiser = raiser if raiser in TARGET else "CO"
    from .preflop import OPEN
    eq = _data()[raiser]
    score = {c: eq[c] + (SUITED_BONUS if c.endswith("s") else 0) for c in OPEN["BTN"]}
    limit, got, keep = 1326 * TARGET[raiser] / 100, 0, set()
    for c in sorted(score, key=score.get, reverse=True):
        n = len(combos(c))
        if got + n / 2 > limit and keep:
            break
        keep.add(c)
        got += n
    return keep
