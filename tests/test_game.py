"""No-limit hold'em game engine: rules, side pots, and chip conservation."""
import random

import pytest

from pokercalc.game import Hand, IllegalAction


def hand(stacks=None, button=0, holes=None, board=None, **kw):
    return Hand(stacks or [200] * 6, button=button, holes=holes, board=board, **kw)


# seat order with button 0: 1=SB, 2=BB, 3=UTG, 4=HJ, 5=CO, 0=BTN
def test_blinds_and_first_to_act():
    h = hand()
    assert h.committed[1] == 1 and h.committed[2] == 2 and h.pot() == 3
    assert h.to_act == 3 and h.position(3) == "UTG" and h.position(0) == "BTN"
    assert all(len(c) == 2 for c in h.hole)


def test_no_limit_limits_preflop():
    L = hand().legal(3)
    assert L["to_call"] == 2 and L["min_raise_to"] == 4
    assert L["max_raise_to"] == 200 == L["all_in_to"]          # no limit: you may put in everything


def test_min_raise_follows_the_last_raise():
    h = hand()
    h.act(3, ("raise", 7))
    L = h.legal(4)
    assert L["min_raise_to"] == 12 and L["max_raise_to"] == 200      # a raise of 5 on top of 7
    with pytest.raises(IllegalAction):
        h.act(4, ("raise", 9))                                       # below the minimum raise
    with pytest.raises(IllegalAction):
        h.act(4, ("raise", 201))                                     # more than the stack
    with pytest.raises(IllegalAction):
        h.act(4, "check")


def test_everyone_folds_to_the_big_blind():
    h = hand()
    for s in (3, 4, 5, 0, 1):
        h.act(s, "fold")
    assert h.over and h.result["winners"] == [2] and not h.result["showdown"]
    assert h.result["net"][2] == 1 and h.result["net"][1] == -1 and sum(h.result["net"]) == 0


def test_big_blind_gets_the_option():
    h = hand()
    for s in (3, 4, 5, 0):
        h.act(s, "call")
    h.act(1, "call")
    assert h.to_act == 2 and h.legal(2)["can_check"] and h.street == "preflop"
    h.act(2, "check")
    assert h.street == "flop" and h.to_act == 1 and len(h.board) == 3


def test_cannot_fold_when_you_can_check():
    h = hand()
    for s in (3, 4, 5, 0):
        h.act(s, "call")
    h.act(1, "call")
    with pytest.raises(IllegalAction):
        h.act(2, "fold")


def test_best_five_of_seven_wins_at_showdown():
    # board has four spades; seat 4 holds a spade (flush), seat 3 holds none (just a pair)
    holes = [["2c", "3d"], ["2d", "3c"], ["8c", "8d"], ["Ah", "Ac"], ["Ts", "Jc"], ["5c", "6d"]]
    board = ["2s", "5s", "9s", "Ks", "7d"]
    h = Hand([200] * 6, button=0, holes=holes, board=board)
    h.act(3, "call")
    h.act(4, "call")
    for s_ in (5, 0, 1):
        h.act(s_, "fold")
    h.act(2, "check")
    while not h.over:
        h.act(h.to_act, "check")
    assert h.result["winners"] == [4]
    assert h.result["net"] == [0, -1, -2, -2, 5, 0]                  # pot 7 (SB folded after posting 1)


def test_all_in_runout_and_side_pots_exact():
    holes = [["3c", "5d"], ["3d", "5c"], ["3h", "5h"], ["Ks", "Kh"], ["9s", "9h"], ["Jc", "Jd"]]
    board = ["2s", "7h", "Kd", "9c", "4d"]
    h = Hand([200, 200, 200, 6, 15, 200], button=0, holes=holes, board=board)
    h.act(3, ("raise", 6))        # UTG all-in for 6
    h.act(4, ("raise", 15))       # HJ all-in for 15
    h.act(5, ("raise", 40))       # CO raises to 40; nobody left to call
    for s_ in (0, 1, 2):
        h.act(s_, "fold")
    assert h.over and h.result["showdown"]
    # main pot 21 -> seat 3 (trip kings); side pot 18 -> seat 4 (trip nines); seat 5's extra 25 comes back
    assert h.result["net"] == [0, -1, -2, 15, 3, -15]
    assert h.result["won"] == [0, 0, 0, 21, 18, 25] and sum(h.result["won"]) == h.result["pot"]    # 21 + 18 + the 25 handed back
    assert sum(h.result["net"]) == 0 and h.stack[3] == 21 and h.stack[4] == 18 and h.stack[5] == 185


def test_chips_always_conserved_with_random_play():
    rng = random.Random(7)
    for g in range(300):
        stacks = [rng.choice([20, 60, 200, 400]) for _ in range(6)]
        h = Hand(stacks, button=g % 6, seed=g)
        guard = 0
        while not h.over:
            guard += 1
            assert guard < 200
            s = h.to_act
            L = h.legal(s)
            r = rng.random()
            if L["can_raise"] and r < 0.25:
                h.act(s, ("raise", rng.randint(L["min_raise_to"], L["max_raise_to"])))
            elif L["can_check"]:
                h.act(s, "check")
            elif r < 0.75:
                h.act(s, "call")
            else:
                h.act(s, "fold")
        assert sum(h.result["net"]) == 0, g
        assert sum(h.stack) == sum(stacks), g
        assert all(x >= 0 for x in h.stack), g


def test_short_all_in_does_not_reopen_raising():
    stacks = [200] * 6
    stacks[4] = 9                                   # HJ has only 9: a raise to 9 is less than a full raise over 7
    h = Hand(stacks, button=0, seed=3)
    h.act(3, ("raise", 7))
    h.act(4, ("raise", 9))                          # short all-in (increment 2 < 5)
    for s_ in (5, 0, 1, 2):
        h.act(s_, "fold")
    L = h.legal(3)                                  # UTG faces 2 more but may NOT re-raise
    assert L["to_call"] == 2 and not L["can_raise"]


def test_odd_chip_goes_left_of_the_button():
    # the board is a broadway straight, so seats 2, 3 and 4 all play it and split the 7-chip pot three ways (2, 2, 3):
    # the odd chip goes to the first winner left of the button, the big blind (seat 2)
    holes = [["2c", "3d"], ["2d", "3h"], ["2h", "3s"], ["4s", "4h"], ["4c", "4d"], ["2s", "3c"]]
    board = ["Ad", "Kd", "Qs", "Jh", "Tc"]
    h = Hand([200] * 6, button=0, holes=holes, board=board)
    h.act(3, "call")
    h.act(4, "call")
    for s_ in (5, 0, 1):
        h.act(s_, "fold")
    h.act(2, "check")
    while not h.over:
        h.act(h.to_act, "check")
    assert h.result["winners"] == [2, 3, 4]
    assert h.result["net"] == [0, -1, 1, 0, 0, 0]
