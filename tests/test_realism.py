"""Realistic bots: the stat meter counts correctly, table strength shifts who sits there, players are individuals."""
import collections
import random

import pytest

from pokercalc import stakes, stats
from pokercalc.bots import STYLES, make_traits
from pokercalc.game import Hand
from pokercalc.table import Table


def count(h):
    c = collections.defaultdict(collections.Counter)
    stats.hand_stats(h, c)
    return c


def test_stat_meter_counts_a_scripted_hand():
    # button 0: seat 3 UTG raises, seat 4 3-bets, seat 3 folds, everyone else folds or calls
    h = Hand([200] * 6, button=0, seed=5)
    h.act(3, ("raise", 7))        # open (PFR, VPIP)
    h.act(4, ("raise", 24))       # 3-bet
    h.act(5, "fold")
    h.act(0, "fold")
    h.act(1, "fold")
    h.act(2, "fold")
    h.act(3, "fold")              # folds to the 3-bet
    c = count(h)
    assert c[3]["hands"] == 1 and c[3]["pfr"] == 1 and c[3]["vpip"] == 1
    assert c[4]["3bet_opp"] == 1 and c[4]["3bet"] == 1
    assert c[3]["f3_opp"] == 1 and c[3]["f3"] == 1
    assert c[5]["vpip"] == 0 and c[5]["3bet_opp"] == 0           # folded facing a 3-bet: not a 3-bet chance
    s = stats.summarise(c[3])
    assert s["vpip"] == 100 and s["pfr"] == 100 and s["fold_to_3bet"] == 100


def test_limp_is_counted_only_when_no_one_raised():
    h = Hand([200] * 6, button=0, seed=5)
    h.act(3, "call")              # limp
    h.act(4, ("raise", 12))       # isolation raise
    h.act(5, "call")              # a flat call of a raise, not a limp
    c = count(h)
    assert c[3]["limp"] == 1 and c[5]["limp"] == 0 and c[5]["vpip"] == 1 and c[4]["pfr"] == 1


def test_strength_shifts_how_many_regulars_sit_at_a_stake():
    rng = random.Random(4)

    def regs(stake, strength, n=300):
        return sum(sum(b["style"] == "reg" for b in stakes.population(stake, rng, strength=strength)) for _ in range(n)) / n
    for stake in ("0.05/0.10", "0.50/1"):
        assert regs(stake, "very_loose") < regs(stake, "loose") < regs(stake, "balanced") < regs(stake, "tough") < regs(stake, "hard")
    assert regs("0.02/0.05", "very_loose") == pytest.approx(stakes.MIN_SHARK, abs=0.12)          # micro + very loose: just the odd regular
    assert regs("5/10", "hard") == pytest.approx(3.5 + 1.0, abs=0.15)                                      # a balanced NL1,000 table has 3.5 sharks, hard adds one
    loose = stakes.population("0.05/0.10", rng, strength="very_loose")
    tough = stakes.population("0.05/0.10", rng, strength="hard")
    assert loose[0]["loose_mult"] > tough[0]["loose_mult"]
    assert [o["key"] for o in stakes.strength_options()] == ["very_loose", "loose", "balanced", "tough", "hard"]


def test_every_player_is_an_individual_and_stays_that_way():
    t = Table("0.02/0.05", seed=3, bot_sims=10, strength="loose")
    traits = [tuple(sorted(b["traits"].items())) for b in t.bots]
    assert len(set(traits)) == len(traits)                       # no two players have identical numbers
    for b in t.bots:
        base = STYLES[b["style"]]
        assert abs(b["traits"]["cbet"] - base["cbet"]) <= 0.26 * max(base["cbet"], 0.01) + 1e-9
    back = Table.from_dict(t.to_dict(), seed=9, bot_sims=10)
    assert [b["traits"] for b in back.bots] == [b["traits"] for b in t.bots]
    assert back.strength == "loose"


def test_changing_strength_between_hands_gives_new_players():
    t = Table("0.50/1", seed=2, bot_sims=10)
    before = [b["style"] for b in t.bots]
    t.set_strength("hard")
    assert t.strength == "hard" and all(b["stack"] == t.buy_in for b in t.bots)
    assert sum(b["style"] == "reg" for b in t.bots) >= 3
    with pytest.raises(ValueError):
        t.set_strength("nonsense")
    assert t.strength == "hard"                                  # a bad value changes nothing
    t.start_hand()
    if not t.hand.over:
        with pytest.raises(Exception):
            t.set_strength("loose")                              # not mid-hand


def test_loose_players_play_more_hands_than_regulars():
    """Cheap check (a few hundred hands) that the styles are at least ordered the way real tables are."""
    reg = stats.measure("reg", hands=150, sims=15)
    station = stats.measure("station", hands=150, sims=15)
    assert station["vpip"] > reg["vpip"] + 10
    assert station["pfr"] < reg["pfr"]                           # loose-passive players call more than they raise
    assert station["limp"] > reg["limp"]


def test_whales_raise_and_bet_big_unlike_stations():
    """A whale is a splashy big spender: far more raising than a calling station, and some bets bigger than the pot."""
    whale = stats.measure("whale", hands=200, sims=15)
    station = stats.measure("station", hands=200, sims=15)
    assert whale["pfr"] > 2 * station["pfr"] and whale["three_bet"] > station["three_bet"] + 5
    assert whale["vpip"] > 50                                    # and still plays most hands
    from pokercalc.bots import Bot
    sizes = lambda style: [Bot(style, seed=i, sims=5)._size(0.5, 0.5) for i in range(300)]
    assert max(sizes("whale")) >= 1.25 and max(sizes("reg")) <= 0.75     # overbets: whales only (and a few maniacs)
