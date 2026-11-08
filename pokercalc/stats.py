"""HUD-style statistics for the bots, measured from finished hands.

    python3 -m pokercalc.stats --hands 1500 --styles reg,tag,nit,station,maniac

plays bot-only tables (six seats of each style in turn) and prints the numbers a HUD would show, so the bots can be
checked against what real players' stats look like (see TARGETS, which are rough ranges from forum posts and HUD guides, not data).
"""
import argparse
import collections
import random

from .bots import Bot
from .game import Hand

# (low, high) in percent. Sources: an old Two Plus Two micro-stakes thread (population about 31% VPIP / 15% PFR, 6%
# 3-bet, 41% fold to a 3-bet, 48% fold to a c-bet), HUD guides (winning 6-max players about 22-28% VPIP, aggression
# 2-3.5, showdown rate 20-32%) and commercial TAG benchmarks (15-22% VPIP, 13-18% PFR). Anecdotes and rough ranges,
# not a database study; the rest are our own guesses.
TARGETS = {
    "reg":     dict(vpip=(21, 26), pfr=(16, 22), limp=(0, 3),   three_bet=(5, 10), fold_to_3bet=(50, 70), cbet=(55, 75),
                    fold_to_cbet=(40, 55), wtsd=(24, 32), aggression=(2.3, 4.0)),
    "tag":     dict(vpip=(22, 30), pfr=(16, 23), limp=(0, 5),   three_bet=(4, 9),  fold_to_3bet=(45, 70), cbet=(50, 70),
                    fold_to_cbet=(40, 55), wtsd=(24, 32), aggression=(1.8, 3.2)),
    "nit":     dict(vpip=(13, 19), pfr=(10, 16), limp=(0, 5),   three_bet=(2, 6),  fold_to_3bet=(55, 85), cbet=(45, 65),
                    fold_to_cbet=(50, 70), wtsd=(20, 30), aggression=(1.5, 3.2)),
    "station": dict(vpip=(35, 55), pfr=(3, 12),  limp=(8, 30),  three_bet=(0, 4),  fold_to_3bet=(0, 25),  cbet=(35, 60),
                    fold_to_cbet=(30, 50), wtsd=(28, 40), aggression=(0.5, 1.5)),
    "whale":   dict(vpip=(50, 75), pfr=(18, 32), limp=(8, 30),  three_bet=(8, 22), fold_to_3bet=(0, 45),  cbet=(50, 75),
                    fold_to_cbet=(15, 40), wtsd=(45, 65), aggression=(1.0, 2.5)),
    "maniac":  dict(vpip=(40, 65), pfr=(22, 40), limp=(2, 15),  three_bet=(8, 18), fold_to_3bet=(5, 35),  cbet=(60, 90),
                    fold_to_cbet=(25, 45), wtsd=(26, 38), aggression=(2.5, 6.0)),
}


def hand_stats(h, c):
    """Add one finished hand's counts into c[seat] (a Counter)."""
    raises, first_raiser, saw_flop = 0, None, set()
    flop_bets, cbet_seat = 0, h.pf_aggressor
    pre = [e for e in h.log if e[0] == "preflop"]
    acted_pre = set()
    for _st, seat, kind, _amt in pre:
        cc = c[seat]
        if seat not in acted_pre:
            cc["hands"] += 1
        acted_pre.add(seat)
        if raises == 1:
            cc["3bet_opp"] += 1
            cc["3bet"] += kind == "raise"
        if raises >= 2 and seat == first_raiser:
            cc["f3_opp"] += 1
            cc["f3"] += kind == "fold"
        if kind in ("call", "raise"):
            cc["vpip"] += 1 if not cc["_v"] else 0
            cc["_v"] = 1
        if kind == "raise":
            cc["pfr"] += 1 if not cc["_p"] else 0
            cc["_p"] = 1
        if kind == "call" and raises == 0:
            cc["limp"] += 1 if not cc["_l"] else 0
            cc["_l"] = 1
        if kind == "raise":
            raises += 1
            first_raiser = first_raiser if first_raiser is not None else seat
    for seat in range(h.n):
        c[seat]["_v"] = c[seat]["_p"] = c[seat]["_l"] = 0
    # flop
    flop = [e for e in h.log if e[0] == "flop"]
    flop_seats = {e[1] for e in flop}
    first_bet_by = None
    for _st, seat, kind, _amt in flop:
        cc = c[seat]
        if first_bet_by is None:
            if seat == cbet_seat:
                cc["cbet_opp"] += 1
                if kind == "raise":
                    cc["cbet"] += 1
                    first_bet_by = seat
            elif kind == "raise":
                first_bet_by = seat
        elif first_bet_by == cbet_seat and seat != cbet_seat and kind in ("fold", "call", "raise"):
            cc["fcb_opp"] += 1
            cc["fcb"] += kind == "fold"
            first_bet_by = -1                                   # count each defender once
    for st, seat, kind, _amt in h.log:
        if st == "preflop":
            continue
        if kind == "raise":
            c[seat]["agg"] += 1
        elif kind == "call":
            c[seat]["calls"] += 1
    sawflop = {s for s in range(h.n) if any(e[0] == "flop" for e in h.log) and s in flop_seats} | \
              {s for s in range(h.n) if h.board and not any(e[1] == s and e[2] == "fold" and e[0] == "preflop" for e in h.log)}
    if h.board:
        for s in sawflop:
            c[s]["saw_flop"] += 1
            if h.result["showdown"] and not h.folded[s]:
                c[s]["wtsd"] += 1


def summarise(c):
    pct = lambda a, b: 100 * c[a] / c[b] if c[b] else float("nan")
    return dict(hands=c["hands"], vpip=pct("vpip", "hands"), pfr=pct("pfr", "hands"), limp=pct("limp", "hands"),
                three_bet=pct("3bet", "3bet_opp"), fold_to_3bet=pct("f3", "f3_opp"), cbet=pct("cbet", "cbet_opp"),
                fold_to_cbet=pct("fcb", "fcb_opp"), wtsd=pct("wtsd", "saw_flop"),
                aggression=c["agg"] / c["calls"] if c["calls"] else float("nan"))


def measure(style, hands=800, seed=1, sims=40, traits_fn=None):
    """Stats for a table of six bots of one style."""
    c = collections.defaultdict(collections.Counter)
    for i in range(hands):
        sd = seed * 100003 + i
        rng = random.Random(sd)
        bots = [Bot(style, seed=sd * 7 + k, sims=sims, traits=traits_fn(rng) if traits_fn else None) for k in range(6)]
        h = Hand([200] * 6, button=i % 6, seed=sd)
        while not h.over:
            s = h.to_act
            h.act(s, bots[s].decide(h, s))
        hand_stats(h, c)
    total = collections.Counter()
    for seat in c.values():
        total.update({k: v for k, v in seat.items() if not k.startswith("_")})
    return summarise(total)


def flag(style, key, value):
    lo, hi = TARGETS[style][key]
    return "ok" if lo <= value <= hi else ("LOW" if value < lo else "HIGH")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", type=int, default=800)
    ap.add_argument("--styles", default="reg,tag,nit,station,maniac")
    a = ap.parse_args()
    cols = ["vpip", "pfr", "limp", "three_bet", "fold_to_3bet", "cbet", "fold_to_cbet", "wtsd", "aggression"]
    print(f"{'style':8s} " + " ".join(f"{c:>13s}" for c in cols))
    for st in a.styles.split(","):
        r = measure(st, a.hands)
        cells = []
        for c in cols:
            v = r[c]
            cells.append(f"{v:6.1f} {flag(st, c, v) if v == v else '':>6s}" if v == v else f"{'n/a':>13s}")
        print(f"{st:8s} " + " ".join(cells))
