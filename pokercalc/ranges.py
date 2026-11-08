"""Starting-hand classes, a strength ranking, and 'top X%' ranges."""
import itertools
import json
import random
from pathlib import Path

import eval7

from .parser import RANKS, SUITS

_CACHE = Path(__file__).with_name("ranking.json")


def hand_classes():
    out = []
    for i, a in enumerate(reversed(RANKS)):
        for b in list(reversed(RANKS))[i:]:
            if a == b:
                out.append(a + b)
            else:
                out += [a + b + "s", a + b + "o"]
    return out  # 169


def combos(cls):
    """Concrete two-card combos (as strings) for a class like 'AKs'."""
    a, b = cls[0], cls[1]
    if a == b:
        return [(a + x, b + y) for x, y in itertools.combinations(SUITS, 2)]
    if cls.endswith("s"):
        return [(a + s, b + s) for s in SUITS]
    return [(a + x, b + y) for x in SUITS for y in SUITS if x != y]


def hole_class(c1, c2):
    r1, r2 = sorted((c1[0], c2[0]), key=RANKS.index, reverse=True)
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if c1[1] == c2[1] else "o")


def _build_ranking(sims=4000):
    rng = random.Random(12345)
    scores = {}
    for cls in hand_classes():
        h = [eval7.Card(c) for c in combos(cls)[0]]
        deck = [eval7.Card(r + s) for r in RANKS for s in SUITS if eval7.Card(r + s) not in h]
        tot = 0.0
        for _ in range(sims):
            d = rng.sample(deck, 7)
            hv, vv = eval7.evaluate(h + d[2:7]), eval7.evaluate(d[:2] + d[2:7])
            tot += 1 if hv > vv else 0.5 if hv == vv else 0
        scores[cls] = tot / sims
    return sorted(scores, key=scores.get, reverse=True), scores


def ranking():
    """Classes ordered strongest->weakest by equity vs a random hand (cached)."""
    if not _CACHE.exists():
        order, scores = _build_ranking()
        _CACHE.write_text(json.dumps({"order": order, "equity": scores}))
    return json.loads(_CACHE.read_text())["order"]


def rank_of(cls):
    return ranking().index(cls) + 1


def top_percent(pct):
    """Classes making up the top pct% of all 1326 combos."""
    limit, got, out = 1326 * pct / 100, 0, []
    for cls in ranking():
        n = len(combos(cls))
        if got + n / 2 > limit and out:
            break
        out.append(cls)
        got += n
    return out
