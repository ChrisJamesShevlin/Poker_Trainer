"""Trainer table: stakes, who sits there, your stack, rebuys, the shot clock, and saving between sessions."""
import random

import pytest

from pokercalc import stakes, store
from pokercalc.game import IllegalAction
from pokercalc.table import NeedsRebuy, Table


def table(stake="0.02/0.05", **kw):
    kw.setdefault("seed", 1)
    kw.setdefault("bot_sims", 10)
    kw.setdefault("speed", "instant")          # these tests are about money and rules, not pacing
    kw.setdefault("account", 10 ** 7)
    return Table(stake, **kw)


def hero_passes(t):
    """Play the rest of the hand by checking or folding."""
    while not t.hand.over:
        L = t.hand.legal(0)
        t.hero_act("check" if L["can_check"] else "fold")


def start_until_hero_to_act(t, tries=40):
    for _ in range(tries):
        st = t.start_hand()
        if "legal" in st:
            return st
        # hand ended before we had to act (everyone folded to us)
    raise AssertionError("hero never got to act")


def test_stakes_ladder_and_money():
    bbs = [v["bb"] for v in stakes.STAKES.values()]
    assert bbs == sorted(bbs) and len(set(bbs)) == len(bbs)
    assert len(stakes.STAKES) == 15                 # NL2 up to $500/$1,000: four micro, three medium, eight high
    assert [v["tier"] for v in stakes.STAKES.values()] == ["micro"] * 4 + ["mid"] * 3 + ["high"] * 8
    assert stakes.STAKES["0.01/0.02"] == dict(sb=1, bb=2, tier="micro", tough=0.0)
    assert (stakes.money(500), stakes.money(5), stakes.money(2000), stakes.money(100000)) == ("$5", "$0.05", "$20", "$1,000")
    row = stakes.ladder()[1]                       # reads like the lobby: "$0.02/$0.05", buy-in "$2 - $5"
    assert (row["blinds"], row["buy_in"], row["buy_in_min"], row["buy_in_max"]) == ("$0.02/$0.05", "$2 - $5", 200, 500)
    assert stakes.ladder()[5]["blinds"] == "$0.50/$1" and stakes.ladder()[5]["buy_in"] == "$40 - $100"
    assert stakes.ladder()[-1]["blinds"] == "$500/$1,000" and stakes.ladder()[-1]["buy_in"] == "$40,000 - $100,000"      # NL100,000
    assert stakes.check_buy_in("0.02/0.05", 500) == 500 and stakes.check_buy_in("0.02/0.05", 200) == 200
    for bad in (150, 600):                         # 30bb and 120bb are outside the 40-100bb range
        with pytest.raises(ValueError):
            stakes.check_buy_in("0.02/0.05", bad)


def test_tables_get_tougher_as_stakes_rise():
    rng = random.Random(1)
    def strong_share(stake):
        n = sum(sum(b["style"] == "reg" for b in stakes.population(stake, rng)) for _ in range(400))
        return n / (400 * 5)
    micro, mid, high = (strong_share(s) for s in ("0.05/0.10", "0.50/1", "5/10"))
    assert micro < mid < high
    for _ in range(100):
        regs = sum(b["style"] == "reg" for b in stakes.population("0.02/0.05", rng))
        assert 1 <= regs <= 2                      # micro: one or two strong players
    assert max(b["loose_mult"] for b in stakes.population("0.02/0.05", rng)) > max(b["loose_mult"] for b in stakes.population("5/10", rng))


def test_blinds_and_buy_in_follow_the_stake():
    t = table("0.50/1")
    st = t.start_hand()
    assert (st["sb"], st["bb"], st["buy_in"]) == (50, 100, 10000)
    assert t.hand.sb == 50 and t.hand.bb == 100 and sum(t.hand.start_stack) == 6 * 10000


def test_your_stack_carries_between_hands_and_can_be_set():
    t = table("0.02/0.05")
    t.set_hero_stack(1000)
    start_until_hero_to_act(t)
    hero_passes(t)
    after = t.hero_stack
    assert after == t.hand.stack[0] and after <= 1000 and t.last["net"] == after - 1000
    t.start_hand()
    assert t.hand.start_stack[0] == after           # the same stack carries into the next hand
    if not t.hand.over:
        hero_passes(t)
    for bad in (10 ** 6, 1):                         # far above the maximum buy-in, and below one big blind
        with pytest.raises(ValueError):
            t.set_hero_stack(bad)
    t.set_hero_stack(777)
    assert t.hero_stack == 777


def test_cannot_change_things_mid_hand():
    t = table()
    start_until_hero_to_act(t)
    for call in (lambda: t.set_stake("0.50/1"), lambda: t.set_hero_stack(500), lambda: t.rebuy(100), lambda: t.start_hand()):
        with pytest.raises(IllegalAction):
            call()


def test_rebuy_when_broke():
    t = table()
    t.hero_stack = 0
    with pytest.raises(NeedsRebuy):
        t.start_hand()
    t.rebuy()
    assert t.hero_stack == t.buy_in
    t.start_hand()                                   # works again
    if not t.hand.over:
        hero_passes(t)
    with pytest.raises(ValueError):                  # nothing to add
        t.rebuy(0)


def test_changing_stake_resets_bots_and_stack():
    t = table("0.02/0.05")
    t.set_stake("0.25/0.50", buy_in=2000)          # the minimum buy-in, 40bb
    assert (t.bb, t.buy_in, t.hero_stack, t.tier) == (50, 2000, 2000, "mid")
    assert len(t.bots) == 5 and all(b["stack"] == 2000 for b in t.bots)
    with pytest.raises(ValueError):
        t.set_stake("0.25/0.50", buy_in=100)


def test_shot_clock_makes_you_check_or_fold():
    now = [1000.0]
    t = table(timer_s=10, clock=lambda: now[0])
    st = start_until_hero_to_act(t)
    assert 9.9 <= st["seconds_left"] <= 10.0 and t.check_clock() is False
    now[0] += 5
    assert 4.9 <= t.state()["seconds_left"] <= 5.1 and t.check_clock() is False
    now[0] += 6                                      # 11s: out of time
    before = len(t.hand.log)
    assert t.check_clock() is True
    first = t.hand.log[before]
    assert first[1] == 0 and first[2] in ("check", "fold")    # the clock played check/fold for seat 0


def test_acting_after_the_clock_runs_out_is_turned_into_check_or_fold():
    now = [0.0]
    t = table(timer_s=10, clock=lambda: now[0])
    start_until_hero_to_act(t)
    now[0] += 11
    st = t.hero_act(("raise", t.hand.legal(0)["max_raise_to"]) if t.hand.legal(0)["can_raise"] else "call")
    assert st["timed_out"] is True
    assert t.hand.log[-1][2] != "raise" or t.hand.log[-1][1] != 0


def test_no_clock_when_timer_is_off():
    t = table(timer_s=0)
    st = start_until_hero_to_act(t)
    assert st["seconds_left"] is None and t.check_clock() is False
    with pytest.raises(ValueError):
        t.set_timer(500)


def test_settings_and_stacks_are_saved_between_sessions():
    t = table("0.05/0.10", buy_in=1000, timer_s=20)
    t.set_hero_stack(2222)
    t.hero_net, t.hand_no, t.button = 1234, 17, 3
    con = store.connect(":memory:")
    store.trainer_save(con, t.to_dict())
    back = Table.from_dict(store.trainer_load(con), seed=2, bot_sims=10)
    assert (back.stake, back.bb, back.buy_in, back.timer_s) == ("0.05/0.10", 10, 1000, 20)
    assert (back.hero_stack, back.hero_net, back.hand_no, back.button) == (2222, 1234, 17, 3)
    assert [b["style"] for b in back.bots] == [b["style"] for b in t.bots]
    back.start_hand()
    assert back.hand.start_stack[0] == 2222


def test_hidden_cards_stay_hidden_until_showdown():
    t = table()
    st = start_until_hero_to_act(t)
    assert st["seats"][0]["hole"] is not None and all(s["hole"] is None for s in st["seats"][1:])
    assert all(s["style"] is None for s in st["seats"])      # you don't get told who is who mid-hand


def test_you_can_see_who_did_what_and_what_to_type_into_the_calculator():
    t = table()
    for _ in range(60):
        st = t.start_hand()
        if "legal" in st and any(e[2] == "raise" for e in t.hand.log):
            break
        if not t.hand.over:
            hero_passes(t)
    assert "legal" in st and any(e[2] == "raise" for e in t.hand.log)
    acted = {e[1]: e[2] for e in t.hand.log}
    for seat, kind in acted.items():
        assert st["seats"][seat]["action"]["kind"] == kind or (kind == "raise" and st["seats"][seat]["action"]["kind"] in ("raise", "bet"))
    tool = st["for_tool"]
    # the calculator's pot box excludes the bet you face: pot + to_call is the whole pot
    assert tool["pot"] + tool["to_call"] == st["pot"]
    last_raiser = [e for e in t.hand.log if e[0] == "preflop" and e[2] == "raise"][-1][1]
    assert tool["bettor"] == t.hand.position(last_raiser) and tool["preflop_raiser"] == tool["bettor"]
    assert tool["position"] == t.hand.position(0) and tool["opponents"] == len(t.hand.active()) - 1


def test_first_raise_after_the_flop_is_called_a_bet():
    t = table()
    for _ in range(80):
        st = t.start_hand()
        while "legal" in st and st["street"] == "preflop":
            st = t.hero_act("check" if st["legal"]["can_check"] else "call")
        if "legal" in st and st["street"] != "preflop" and st["legal"]["can_raise"] and st["legal"]["current_bet"] == 0:
            street = st["street"]
            st = t.hero_act(("raise", st["legal"]["min_raise_to"]))
            if st["street"] == street:                  # still the same street (everyone folded, or someone re-raised)
                assert st["seats"][0]["action"]["kind"] == "bet"
                return
        if not t.hand.over:
            hero_passes(t)
    pytest.skip("no suitable spot in 80 hands")


def test_a_short_stack_tops_up_or_leaves_and_the_page_is_told(monkeypatch):
    monkeypatch.setattr("pokercalc.table.REBUY_P", 1.0)           # everyone who is short tops up
    t = table()
    t.bots[2]["stack"] = 0                       # seat 3 went broke
    t.bots[0]["stack"] = 3 * t.bb                 # seat 1 is down to 3 big blinds
    st = t.start_hand()
    assert st["rebought"] == [1, 3] and [n["kind"] for n in st["news"] if n["kind"] == "rebuy"] == ["rebuy", "rebuy"]
    assert t.hand.start_stack[3] == t.buy_in and t.hand.start_stack[1] == t.buy_in
    if not t.hand.over:
        hero_passes(t)
    for b in t.bots:                              # everyone is topped up, so nobody rebuys in the next hand
        b["stack"] = t.buy_in
    monkeypatch.setattr("pokercalc.table.LEAVE_RANDOM_P", 0.0)
    monkeypatch.setattr("pokercalc.table.LEAVE_AHEAD_P", 0.0)
    t.start_hand()
    assert t.state()["rebought"] == [] and t.state()["news"] == []     # only reported for the hand where it happened


def test_showdown_names_each_hand_and_what_the_winner_collected():
    t = table()
    for _ in range(120):
        t.start_hand()
        if not t.hand.over:
            hero_passes(t)
        if t.last["showdown"]:
            break
    else:
        pytest.skip("no showdown in 120 hands")
    L, h = t.last, t.hand
    assert set(L["hands"]) == set(h.result["hands"]) == set(L["five"])
    assert all(isinstance(n, str) and len(n) > 3 for n in L["hands"].values())          # e.g. "Two pair, Tens and Eights"
    for s, five in L["five"].items():
        assert len(five) == 5 and set(five) <= set(h.hole[s]) | set(h.board)
    assert L["won"] and all(v > 0 for v in L["won"].values()) and sum(L["won"].values()) <= L["pot"]
    assert set(L["won"]) == set(L["winners"])


def test_a_pot_won_without_a_showdown_says_so():
    t = table()
    for _ in range(120):
        t.start_hand()
        if not t.hand.over:
            hero_passes(t)
        if not t.last["showdown"]:
            break
    else:
        pytest.skip("every hand went to showdown")
    L = t.last
    assert "hands" not in L and len(L["winners"]) == 1 and L["won"] == {L["winners"][0]: L["pot"]}
