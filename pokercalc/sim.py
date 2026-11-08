"""Headless simulation: the app's advice (seat 0) against bots, over many hands. No UI, no database.

    python3 -m pokercalc.sim --hands 200 --styles tag,nit,station,maniac,tag --workers 4
    python3 -m pokercalc.sim --hands 400 --stake 0.02/0.05            # a fresh, realistic table for that stake every hand

Seat 0 plays whatever service.evaluate() advises. Seats 1-5 are bots: in the given styles, or (with --stake) drawn
for that stake the way the trainer does it (stakes.population). The button moves each hand, every hand starts with
100bb stacks, and there is no rake.
"""
import argparse
import math
import re
import statistics
import time
from multiprocessing import Pool

import random

from . import service, stakes
from .bots import Bot, check_or_call, check_or_fold, clamp_raise
from .game import Hand, service_input


class Advisor:
    """Plays what the tool says. `sims` is the equity trials per decision (the page uses 10,000)."""

    def __init__(self, sims=400, opp_type="normal"):
        self.sims, self.opp_type = sims, opp_type

    def decide(self, hand, seat):
        info = hand.legal(seat)
        out = service.evaluate(service_input(hand, seat, self.opp_type), sims=self.sims)
        act = out["action"]
        if act == "FOLD":
            return check_or_fold(info)
        if act in ("CALL", "CHECK") or not info["can_raise"]:
            return check_or_call(info)
        sizing = out.get("sizing")
        chips = getattr(sizing, "chips", None)
        if chips is None and sizing:
            if "all-in" in sizing:
                chips = info["max_raise_to"]
            else:
                m = re.search(r"~([\d,]+)", sizing)
                chips = float(m.group(1).replace(",", "")) if m else None
        if chips is None:
            chips = info["min_raise_to"]
        return clamp_raise(info, chips)


def play(agents, button, seed, stack=200, sb=1, bb=2):
    h = Hand([stack] * len(agents), button=button, seed=seed, sb=sb, bb=bb)
    while not h.over:
        s = h.to_act
        h.act(s, agents[s].decide(h, s))
    return h


def _worker(args):
    styles, hands, seed0, bot_sims, hero_sims, opp_type, stake = args
    sb, bb = (stakes.STAKES[stake]["sb"], stakes.STAKES[stake]["bb"]) if stake else (1, 2)
    nets, by_style = [[] for _ in range(6)], {}
    for i in range(hands):
        seed = seed0 + i
        pop = stakes.population(stake, random.Random(seed)) if stake else [{"style": st, "loose_mult": 1.0} for st in styles]
        agents = [Advisor(hero_sims, opp_type)] + [Bot(b_["style"], seed=seed * 7 + k, sims=bot_sims, loose_mult=b_["loose_mult"])
                                                   for k, b_ in enumerate(pop)]
        h = play(agents, button=seed % 6, seed=seed, stack=100 * bb, sb=sb, bb=bb)
        for s_, n in enumerate(h.result["net"]):
            nets[s_].append(n / bb)                                       # in big blinds
            if s_:
                by_style.setdefault(pop[s_ - 1]["style"], []).append(n / bb)
    return nets, by_style


def run(styles, hands=200, workers=4, bot_sims=300, hero_sims=400, seed=1000, opp_type="normal", stake=None):
    chunk = math.ceil(hands / workers)
    jobs = [(styles, chunk, seed + w * 100000, bot_sims, hero_sims, opp_type, stake) for w in range(workers)]
    t = time.time()
    with Pool(workers) as p:
        parts = p.map(_worker, jobs)
    nets = [sum((part[0][s] for part in parts), []) for s in range(6)]
    by_style = {}
    for part in parts:
        for k, v in part[1].items():
            by_style.setdefault(k, []).extend(v)
    return nets, by_style, time.time() - t


def stat(values):
    """(bb/100, one standard error) from per-hand results measured in big blinds."""
    return statistics.mean(values) * 100, statistics.stdev(values) / math.sqrt(len(values)) * 100


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", type=int, default=200)
    ap.add_argument("--styles", default="tag,nit,station,maniac,tag")
    ap.add_argument("--stake", default=None, choices=[None] + list(stakes.STAKES))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--opp-type", default="normal", choices=["normal", "folds", "calls"])
    a = ap.parse_args()
    nets, by_style, secs = run(a.styles.split(","), a.hands, a.workers, opp_type=a.opp_type, stake=a.stake)
    n = len(nets[0])
    where = f"{a.stake} tables" if a.stake else a.styles
    print(f"{n} hands vs {where} in {secs:.0f}s ({secs / n:.2f}s per hand, {a.workers} workers)\n")
    m, se = stat(nets[0])
    print(f"advisor: {m:+.1f} bb/100  (+/- {se:.1f}, one standard error)")
    print("\nbots by style (all seats pooled):")
    for k, v in sorted(by_style.items()):
        m, se = stat(v)
        print(f"  {k:8s} {len(v):5d} hands  {m:+8.1f} bb/100  (+/- {se:.1f})")
