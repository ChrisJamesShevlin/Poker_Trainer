"""Bots: they must always make legal moves, be repeatable, and the styles must actually differ."""
from pokercalc.bots import STYLES, Bot
from pokercalc.game import Hand


def play_bots(styles, seed, sims=30):
    bots = [Bot(st, seed=seed * 10 + i, sims=sims) for i, st in enumerate(styles)]
    h = Hand([200] * len(styles), button=seed % len(styles), seed=seed)
    guard = 0
    while not h.over:
        guard += 1
        assert guard < 300
        s = h.to_act
        h.act(s, bots[s].decide(h, s))          # raises IllegalAction if a bot cheats
    return h


def test_bots_only_make_legal_moves_and_chips_are_conserved():
    mix = ["tag", "nit", "station", "maniac", "tag", "maniac"]
    for seed in range(60):
        h = play_bots(mix, seed)
        assert sum(h.result["net"]) == 0
        assert all(x >= 0 for x in h.stack)


def test_bots_are_repeatable_with_the_same_seed():
    a = play_bots(["tag"] * 6, 5)
    b = play_bots(["tag"] * 6, 5)
    assert a.log == b.log and a.result["net"] == b.result["net"]


def vpip(style, hands=250):
    """Share of hands in which seat 0 voluntarily put chips in preflop (call or raise)."""
    n = 0
    for seed in range(hands):
        bots = [Bot(style if i == 0 else "tag", seed=seed * 10 + i, sims=20) for i in range(6)]
        h = Hand([200] * 6, button=seed % 6, seed=seed)
        while not h.over and h.street == "preflop":
            s = h.to_act
            h.act(s, bots[s].decide(h, s))
        if any(st == "preflop" and seat == 0 and act in ("call", "raise") for st, seat, act, _ in h.log):
            n += 1
    return n / hands


def test_styles_differ_in_how_loose_they_are():
    nit, tag, station = vpip("nit"), vpip("tag"), vpip("station")
    assert nit < tag < station, (nit, tag, station)


def test_every_style_exists_and_has_the_same_knobs():
    keys = {frozenset(v) for v in STYLES.values()}
    assert len(keys) == 1


def test_every_style_bluffs_and_only_the_shark_picks_its_bluffs():
    from pokercalc.bots import STYLES
    assert all(p["bluff"] > 0 for p in STYLES.values())              # at every level, every kind of player bluffs sometimes
    assert STYLES["reg"]["bluff_eq"] < 1.0 and STYLES["reg"]["semi_lo"] > 0
    assert all(STYLES[s]["bluff_eq"] == 1.0 and STYLES[s]["semi_lo"] == 0.0 for s in STYLES if s != "reg")


def test_a_shark_reads_who_folds_but_never_stops_bluffing():
    from pokercalc.bots import Bot, record_reads, make_traits
    from pokercalc.game import Hand
    import random
    keys = {0: "hero", 1: "villain", 2: "x", 3: "y", 4: "z", 5: "w"}
    h = Hand([10000] * 6, button=0, sb=50, bb=100, seed=1)
    reads = {"villain": [100, 99], "x": [100, 99], "y": [100, 99], "z": [100, 99], "w": [100, 99]}
    folder = Bot("reg", seed=1, sims=10, traits=make_traits("reg", random.Random(1), 1.0, 0.5), reads=reads, keys=keys)
    callers = Bot("reg", seed=1, sims=10, traits=make_traits("reg", random.Random(1), 1.0, 0.5),
                  reads={k: [20, 1] for k in keys.values()}, keys=keys)
    assert folder._read_mult(h, 0) > 1.0 > callers._read_mult(h, 0)
    assert callers._read_mult(h, 0) >= 0.2 * (1 - callers.p["adapt"]) + 0.2 * callers.p["adapt"] - 1e-9
    rec = {}
    h2 = Hand([10000] * 6, button=0, sb=50, bb=100, seed=2)
    record_reads(rec, keys, h2, 0)
    assert isinstance(rec, dict)
