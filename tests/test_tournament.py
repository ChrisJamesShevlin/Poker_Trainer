"""Players who top up or leave and are replaced at random (cash tables), and the tournament mode (sit and go)."""
import json

import pytest

from pokercalc import stakes
from pokercalc.game import Hand
from pokercalc.table import Table


def cash(**kw):
    return Table("0.02/0.05", seed=kw.pop("seed", 3), bot_sims=5, speed="instant", **kw)


def pass_hand(t):
    while not t.hand.over:
        t.hero_act("fold" if t.hand.legal(0)["to_call"] else "check")


# ---------------------------------------------------------------- players coming and going
def test_a_short_stack_who_does_not_top_up_leaves_and_a_new_player_sits_down(monkeypatch):
    monkeypatch.setattr("pokercalc.table.REBUY_P", 0.0)                       # nobody tops up
    t = cash()
    before = [b["persona"]["name"] for b in t.bots]
    t.bots[1]["stack"] = 0
    st = t.start_hand()
    swap = [n for n in st["news"] if n["kind"] == "swap"]
    assert len(swap) == 1 and swap[0]["seat"] == 2 and swap[0]["name"] == before[1] and swap[0]["new"] != before[1]
    assert t.bots[1]["persona"]["name"] == swap[0]["new"] and t.hand.start_stack[2] == t.buy_in      # a fresh buy-in
    assert len({b["persona"]["name"] for b in t.bots}) == 5                                          # still five different people
    assert st["rebought"] == []


def test_a_new_player_comes_with_their_own_level_and_it_stays_hidden():
    t = cash(seed=5)
    t.bots[0]["stack"] = 0
    import pokercalc.table as tm
    tm.REBUY_P, old = 0.0, tm.REBUY_P
    try:
        st = t.start_hand()
    finally:
        tm.REBUY_P = old
    seat = st["seats"][1]
    assert seat["name"] and seat["type"] is None                                                     # the newcomer is a question mark too
    from pokercalc import personas
    assert personas.STYLE_OF[seat["name"]] == t.bots[0]["style"]                                     # and is the kind of player their name says


def test_players_who_have_won_a_lot_sometimes_cash_out_and_others_get_up_now_and_then():
    leavers = 0
    for seed in range(80):
        t = cash(seed=seed)
        for b in t.bots:
            b["stack"] = int(2.5 * t.buy_in)                               # everyone is well ahead
        before = [b["persona"]["name"] for b in t.bots]
        st = t.start_hand()
        leavers += sum(1 for n in st["news"] if n["kind"] == "swap")
    assert 5 <= leavers <= 60                                              # about 4% of 5 seats over 80 hands: some, but not most
    quiet = 0
    for seed in range(80):
        t = cash(seed=seed)
        quiet += sum(1 for n in t.start_hand()["news"] if n["kind"] == "swap")
    assert quiet <= 8                                                      # normal stacks: only the odd random departure


def test_churn_does_not_happen_mid_hand_and_chips_are_conserved():
    t = cash(seed=2)
    for _ in range(60):
        t.start_hand()
        names = [b["persona"]["name"] for b in t.bots]
        pass_hand(t)
        assert [b["persona"]["name"] for b in t.bots] == names             # nobody swapped during the hand
        assert sum(t.hand.result["net"]) == 0


# ---------------------------------------------------------------- the engine: seats that are out, and heads-up
def test_hand_with_players_out_deals_them_nothing_and_names_positions_among_those_left():
    h = Hand([1000] * 6, button=1, sb=10, bb=20, seed=4, out={0, 3})
    assert h.folded == [True, False, False, True, False, False] and h.active() == [1, 2, 4, 5]
    assert (h.sb_seat, h.bb_seat) == (2, 4) and h.to_act == 5
    assert [h.position(s) for s in range(6)] == ["OUT", "BTN", "SB", "OUT", "BB", "CO"]
    while not h.over:
        h.act(h.to_act, "call" if h.legal(h.to_act)["to_call"] else "check")
    assert sum(h.result["net"]) == 0 and h.result["net"][0] == h.result["net"][3] == 0


def test_heads_up_the_button_is_the_small_blind_and_acts_first_before_the_flop_only():
    h = Hand([1000] * 6, button=4, sb=10, bb=20, seed=3, out={0, 2, 3, 5})
    assert (h.sb_seat, h.bb_seat, h.to_act) == (4, 1, 4)
    assert [h.position(s) for s in (4, 1)] == ["SB", "BB"]
    h.act(4, "call")
    h.act(1, "check")
    assert h.street == "flop" and h.to_act == 1                            # after the flop the big blind acts first
    with pytest.raises(ValueError):
        Hand([1000] * 6, out={0, 1, 2, 3, 4})                              # one player is not a game


# ---------------------------------------------------------------- a multi-table tournament
import random

from pokercalc import mtt


def tourney(seed=1, stake="0.02/0.05", size=mtt.FIELD_SIZE):
    t = Table(stake, seed=seed, bot_sims=5, speed="instant")
    t.start_tournament(stake, size=size)
    return t


def safe_play(t, limit=600):
    """A patient player: checks, calls small bets and folds the rest. Returns the hands played."""
    n = 0
    while not t.tourney["finished"] and n < limit:
        t.start_hand()
        while not t.hand.over:
            L = t.hand.legal(0)
            t.hero_act("check" if L["can_check"] else "call" if L["to_call"] <= 3 * t.bb else "fold")
        n += 1
    return n


def test_a_tournament_is_a_field_of_36_on_six_tables_with_equal_chips_and_rising_blinds():
    t = tourney()
    f = t.tourney["field"]
    assert f["size"] == 36 and len(f["tables"]) == 6 and all(len(tb) == 6 for tb in f["tables"]) and f["hero_table"] == 0
    sc = t.tourney["start_chips"]
    assert all(p["stack"] == sc for p in f["players"].values())
    assert len({p["persona"]["name"] for p in f["players"].values() if not p.get("hero")}) == 35          # nobody shares a name
    st = t.state()["tourney"]
    assert st["blinds"] == t.tourney["levels"][0][:2] and st["left"] == 36 and st["paid"] == 5 and st["tables"] == 6 and st["table_no"] == 1
    assert sum(st["prizes"]) == st["prize_pool"] == 36 * st["buy_in"] and st["prizes"] == sorted(st["prizes"], reverse=True)
    assert all(b["stack"] == sc and not b.get("empty") for b in t.bots) and len(t.bots) == 5


def test_every_table_plays_each_round_and_the_chips_always_add_up():
    t = tourney(seed=2)
    f = t.tourney["field"]
    for _ in range(25):
        if t.tourney["finished"]:
            break
        t.start_hand()
        while not t.hand.over:
            L = t.hand.legal(0)
            t.hero_act("check" if L["can_check"] else "call" if L["to_call"] <= 3 * t.bb else "fold")
        assert sum(p["stack"] for p in f["players"].values()) == 36 * t.tourney["start_chips"]       # nothing is ever created or lost (antes included)
        assert all(p["stack"] == 0 for p in f["players"].values() if p["place"] is not None)
    assert f["left"] < 36                                                                                  # the other tables really are playing


def test_players_go_out_in_order_with_unique_places_and_tables_are_broken_and_balanced():
    for seed in (1, 4):
        t = tourney(seed=seed)
        f = t.tourney["field"]
        seen_tables = {len(f["tables"])}
        while not t.tourney["finished"]:
            t.start_hand()
            while not t.hand.over:
                L = t.hand.legal(0)
                t.hero_act("check" if L["can_check"] else "call" if L["to_call"] <= 3 * t.bb else "fold")
            seen_tables.add(len(f["tables"]))
            sizes = [len(tb) for tb in f["tables"]]
            if t.tourney["pending"] is False:
                continue
        # (the tables are evened out at the start of the next round, so check after a fresh round when we can)
        places = sorted(p["place"] for p in f["players"].values() if p["place"] is not None)
        assert places == list(range(36 - len(places) + 1, 37))                                              # no gaps, no ties
        assert len(seen_tables) > 1 and f["left"] <= 36


def test_tables_are_kept_within_one_player_and_broken_when_not_needed_and_the_final_table_is_one_table():
    rng = random.Random(1)
    f = mtt.new_field("0.02/0.05", "balanced", rng, 36, 3000)
    order = list(range(1, 36))
    rng.shuffle(order)
    for left in (30, 25, 19, 13, 7, 6, 3):
        while f["left"] > left:
            pid = order.pop()
            f["players"][str(pid)]["place"] = f["left"]
            f["players"][str(pid)]["stack"] = 0
            f["left"] -= 1
        mtt.rebalance(f, rng)
        sizes = [len(tb) for tb in f["tables"]]
        assert max(sizes) - min(sizes) <= 1 and sum(sizes) == left and max(sizes) <= 6
        assert len(f["tables"]) == -(-left // 6)                                                           # as few tables as needed
        assert 0 in f["tables"][f["hero_table"]]
    assert len(f["tables"]) == 1 and f["left"] == 3


def test_breaking_your_table_moves_you_and_your_opponents_change():
    t = tourney(seed=5)
    f = t.tourney["field"]
    before = set(mtt.hero_table_ids(f))
    keep = {0, 6} | {1, 7, 13, 19, 25} | {2, 8, 14, 20, 26}                  # 12 left: two at your table, five at each of two others
    for pid, p in f["players"].items():
        if int(pid) not in keep:
            p["place"], p["stack"] = f["left"], 0
            f["left"] -= 1
    t.tourney["pending"] = True
    t.start_hand()                                                          # the tables are evened out as the next round starts
    kinds = [n["kind"] for n in t.state()["news"]]
    assert "moved" in kinds and t.tourney["table_moves"] == 1
    assert len(f["tables"]) == 2 and f["left"] == 12
    mine = set(mtt.hero_table_ids(f))
    assert len(mine) == 5 and not (mine & before) or mine != before         # you sit with different people now
    assert sorted(pid for pid in t.tourney["hero_seats"] if pid is not None) == sorted(mine)
    assert [b["persona"]["name"] for b in t.bots if not b.get("empty")] == [f["players"][str(pid)]["persona"]["name"] for pid in t.tourney["hero_seats"] if pid is not None]
    assert t.state()["tourney"]["table_no"] == f["hero_table"] + 1


def test_busted_players_are_out_with_their_place_and_empty_seats_have_no_name():
    t = tourney(seed=3, size=12)
    safe_play(t, limit=200)
    st = t.state()
    assert t.tourney["finished"] and st["tourney"]["finished"] and st["revealed"] is True
    seats = st["seats"]
    assert all(s["place"] is None or s["place"] >= 1 for s in seats)
    empty = [s for s in seats if s["empty"]]
    assert all(s["name"] is None and s["type"] is None for s in empty)
    assert all(s["out"] for s in empty)


def test_the_result_pays_the_top_places_and_counts_what_the_badges_need():
    won = None
    for seed in range(1, 60):
        t = tourney(seed=seed, size=12)
        while not t.tourney["finished"]:
            t.start_hand()
            while not t.hand.over:
                L = t.hand.legal(0)
                t.hero_act(("raise", L["max_raise_to"]) if L["can_raise"] else "call" if L["to_call"] else "check")     # shove every hand
        r = t.tourney_result()
        paid = t.tourney["paid"]
        assert r["prize"] == (t.tourney["prizes"][r["place"] - 1] if r["place"] <= paid else 0)
        assert set(r) >= {"place", "size", "buy_in", "prize", "hands", "knockouts", "level", "final_table", "heads_up", "min_bb", "table_moves", "sharks"}
        assert 1 <= r["place"] <= 12 and r["size"] == 12
        if r["place"] == 1:
            won = r
            assert r["heads_up"] and r["final_table"] and 0 <= r["knockouts"] <= 11               # (you can win without knocking anyone out yourself)
            break
    assert won is not None                                                                                  # a shove-everything player does win sometimes


def test_a_tournament_is_saved_and_restored_with_its_field_and_seats():
    t = tourney(seed=5)
    t.start_hand()
    while not t.hand.over:
        t.hero_act("check" if t.hand.legal(0)["can_check"] else "fold")
    data = json.loads(json.dumps(t.to_dict()))                                                              # plain JSON both ways
    back = Table.from_dict(data, seed=1, bot_sims=5)
    assert back.tourney["field"]["tables"] == t.tourney["field"]["tables"] and back.tourney["hero_seats"] == t.tourney["hero_seats"]
    assert [b["persona"]["name"] for b in back.bots] == [b["persona"]["name"] for b in t.bots]
    assert (back.sb, back.bb) == (t.sb, t.bb)
    back.start_hand()                                                                                       # and it plays on
    plain = cash()
    assert plain.tourney is None and plain.state().get("chip_mode") is False


def test_antes_are_dead_money_nobody_has_to_call():
    from pokercalc.game import Hand
    h = Hand([1000] * 6, button=0, sb=5, bb=10, ante=2, seed=1)
    assert h.pot() == 6 * 2 + 15 and h.current_bet == 10 and h.legal(h.to_act)["to_call"] == 10      # antes are in the pot, not in the bet
    assert sum(h.stack) + h.pot() == 6000
    short = Hand([1000, 1000, 1, 1000, 1000, 1000], button=0, sb=5, bb=10, ante=2, seed=1)
    assert short.allin[2] and short.stack[2] == 0 and short.total[2] == 1                             # a 1-chip stack posts what it has
    while not short.over:
        short.act(short.to_act, "check" if short.legal(short.to_act)["can_check"] else "call")
    assert sum(short.result["net"]) == 0


def test_every_stake_has_its_own_structure_that_gets_deeper_and_gains_antes_earlier():
    keys = list(stakes.STAKES)
    structs = [mtt.structure(k) for k in keys]
    assert structs[0]["start_chips"] == 3000 and structs[1]["start_chips"] == 3000                    # the two lowest entries: 3,000 chips
    chips = [s["start_chips"] for s in structs]
    assert chips == sorted(chips) and chips[-1] > chips[0]                                           # more chips as the stakes rise
    depth = [s["start_chips"] / s["levels"][0][1] for s in structs]
    assert depth[0] == 100 and depth[-1] >= 150 and all(d >= 100 for d in depth)                      # deeper stacks at the top
    assert structs[0]["ante_from"] > structs[-1]["ante_from"]                                         # antes come in earlier at the top
    for s in structs:
        bbs = [lv[1] for lv in s["levels"]]
        assert bbs == sorted(set(bbs)) and all(lv[0] * 2 == lv[1] for lv in s["levels"])             # every level is bigger, small blind = half
        assert [lv[2] > 0 for lv in s["levels"]] == [i + 1 >= s["ante_from"] for i in range(len(bbs))]
    assert [tuple(x) for x in structs[0]["levels"][:2]] == [(15, 30, 0), (25, 50, 0)]


def test_a_tournament_uses_the_stakes_structure_and_antes_start_on_time():
    t = Table("0.50/1", seed=3, bot_sims=8)
    t.start_tournament("0.50/1")
    s = mtt.structure("0.50/1")
    assert t.hero_stack == s["start_chips"] == 10000 and t.state()["tourney"]["blinds"] == [50, 100] and t.state()["tourney"]["ante"] == 0
    for _ in range(s["ante_from"] * s["hands_per_level"] + 2):
        if t.tourney["finished"]:
            break
        t.start_hand()
        while not t.hand.over:
            L = t.hand.legal(0)
            t.hero_act("check" if L["can_check"] else "fold")
    if not t.tourney["finished"]:
        assert t.state()["tourney"]["ante"] > 0 and t.hand.ante == t.ante > 0                         # the ante is in play from its level


def test_a_tournament_saved_before_structures_varied_still_loads():
    t = Table("0.50/1", seed=3, bot_sims=8)
    t.start_tournament("0.50/1")
    d = t.to_dict()
    for k in ("levels", "hands_per_level", "start_chips", "ante_from"):
        d["tourney"].pop(k)
    back = Table.from_dict(d, seed=1, bot_sims=8)
    assert back.tourney["hands_per_level"] == 12 and back.tourney["levels"][0] == [15, 30, 0]
    back.start_hand()


def test_chips_still_add_up_with_antes_at_a_high_stake():
    t = Table(seed=5, bot_sims=8)                                      # (sits at the first level; the tournament picks its own stake)
    t.start_tournament("5/10")
    f = t.tourney["field"]
    total = 36 * t.tourney["start_chips"]
    for _ in range(80):
        if t.tourney["finished"]:
            break
        t.start_hand()
        while not t.hand.over:
            L = t.hand.legal(0)
            t.hero_act("check" if L["can_check"] else "call" if L["to_call"] <= 3 * t.bb else "fold")
        assert sum(p["stack"] for p in f["players"].values()) == total
    assert t.tourney["level"] >= 3 and any(lv[2] for lv in t.tourney["levels"][:t.tourney["level"] + 1])        # it got to the antes
