"""The shared table: two humans and bots, each player seeing the table from their own seat."""
import pytest

from pokercalc.game import IllegalAction
from pokercalc.multiplayer import GUEST, HOST, SEATS, SharedTable


def table(seed=3, join=True):
    t = SharedTable("0.01/0.02", seed=seed, bot_sims=6)
    if join:
        t.join("Sam")
    return t


def fold_or_check(t, seat):
    legal = t.hand.legal(seat)
    t.act(seat, "check" if legal["can_check"] else "fold")


def play_hand(t, policy=fold_or_check):
    t.start_hand()
    guard = 0
    while not t.hand.over and guard < 200:
        guard += 1
        policy(t, t.hand.to_act)


def test_bots_play_until_a_human_has_to_act_and_only_that_human_may_act():
    t = table()
    t.start_hand()
    h = t.hand
    assert h.to_act in (HOST, GUEST) or h.over
    other = GUEST if h.to_act == HOST else HOST
    with pytest.raises(IllegalAction):
        t.act(other, "fold")                                     # not your turn
    with pytest.raises(IllegalAction):
        t.act(3, "fold")                                         # a bot's seat is not yours to play


def test_each_player_sees_only_their_own_cards_until_a_showdown():
    t = table()
    t.start_hand()
    h = t.hand
    host_view, guest_view = t.state(HOST), t.state(GUEST)
    assert host_view["seats"][0]["hole"] == h.hole[HOST] and guest_view["seats"][0]["hole"] == h.hole[GUEST]
    assert all(s["hole"] is None for s in host_view["seats"][1:]) and all(s["hole"] is None for s in guest_view["seats"][1:])


def test_the_table_is_turned_round_so_each_viewer_is_seat_zero():
    t = table()
    t.start_hand()
    h = t.hand
    for viewer in (HOST, GUEST):
        v = t.state(viewer)
        assert [s["seat"] for s in v["seats"]] == list(range(SEATS))
        assert v["seats"][0]["position"] == h.position(viewer)                          # the viewer is at the bottom, whatever their position
        assert v["seats"][1]["position"] == h.position((viewer + 1) % SEATS)
        assert v["button"] == (h.button - viewer) % SEATS
        if not h.over:
            assert (v["to_act"] == 0) == (h.to_act == viewer)
            assert ("legal" in v) == (h.to_act == viewer)
    assert t.state(GUEST)["seats"][1]["name"] == t._name_of(2) and t.state(HOST)["seats"][1]["name"] == "Sam"


def test_the_for_the_rta_numbers_are_the_viewers_own():
    t = table()
    t.start_hand()
    seat = t.hand.to_act
    if t.hand.over or seat not in (HOST, GUEST):
        pytest.skip("hand ended before a human acted")
    v = t.state(seat)
    assert v["for_tool"]["position"] == t.hand.position(seat) and v["for_tool"]["stack"] == t.hand.stack[seat]
    other = GUEST if seat == HOST else HOST
    assert "for_tool" not in t.state(other)


def test_the_result_and_showdown_are_shown_from_each_viewers_side():
    t = table(seed=5)
    for _ in range(40):
        t.start_hand()
        h = t.hand
        guard = 0
        while not h.over and guard < 100:
            guard += 1
            seat = h.to_act
            legal = h.legal(seat)
            t.act(seat, "call" if legal["to_call"] else "check")      # calling stations: showdowns happen
        if h.result["showdown"]:
            break
    assert h.result["showdown"]
    for viewer in (HOST, GUEST):
        v = t.state(viewer)
        assert v["last"]["net"] == h.result["net"][viewer]
        assert set(v["last"]["winners"]) == {(w - viewer) % SEATS for w in h.result["winners"]}
        shown = {s["seat"] for s in v["seats"] if s["hole"]}
        assert shown >= {0}                                                          # at a showdown the cards of those still in are turned up


def test_chips_are_conserved_over_many_hands_and_a_busted_human_is_topped_up():
    t = table(seed=7)
    total = lambda: sum(t._stack_of(s) for s in range(SEATS))
    start = total()
    for _ in range(30):
        t.start_hand()
        h = t.hand
        while not h.over:
            seat = h.to_act
            legal = h.legal(seat)
            t.act(seat, ("raise", legal["max_raise_to"]) if legal["can_raise"] and h.street == "preflop" else ("call" if legal["to_call"] else "check"))
        assert total() == sum(h.stack)
    t.host_stack = 1                                         # a human who is out of chips is topped up at the next deal
    t.start_hand()
    assert t.state(HOST)["news"] == [] or any(n["kind"] == "topup" for n in t.news)
    assert t._stack_of(HOST) >= 0 and t.hand.start_stack[HOST] == t.buy_in


def test_joining_and_leaving_only_between_hands_and_the_seat_goes_back_to_a_bot():
    t = table(join=False)
    assert not t.is_human(GUEST) and t.state(HOST)["guest"] is None
    t.start_hand()
    with pytest.raises(IllegalAction):
        t.join("Sam")
    while not t.hand.over:
        fold_or_check(t, t.hand.to_act)
    t.join("Sam")
    assert t.is_human(GUEST) and t.state(HOST)["guest"] == "Sam"
    t.leave()
    assert not t.is_human(GUEST) and t.state(HOST)["guest"] is None
    play_hand(t)                                              # the table still plays with the seat back to a bot


def test_the_host_changes_the_settings_between_hands_like_practice_mode():
    t = table()
    t.set_stake("0.50/1", buy_in=4000)
    assert t.bb == 100 and t.buy_in == 4000 and t._stack_of(HOST) == 4000 and t._stack_of(GUEST) == 4000
    t.set_strength("hard")
    t.set_buy_in(6000)
    assert t._stack_of(GUEST) == 6000 and t.strength == "hard"
    with pytest.raises(ValueError):
        t.set_buy_in(10)                                      # outside the 40-100 big blind range
    t.start_hand()
    with pytest.raises(IllegalAction):
        t.set_stake("0.01/0.02")                              # not in the middle of a hand


def test_the_version_changes_when_the_table_does():
    t = table()
    v0 = t.state(HOST)["version"]
    t.start_hand()
    assert t.state(HOST)["version"] > v0


def test_a_friend_who_leaves_mid_hand_is_folded_out_and_the_seat_goes_back_to_a_bot():
    t = table()
    for _ in range(30):
        t.start_hand()
        if t.hand.to_act == GUEST:
            break
        while not t.hand.over:
            fold_or_check(t, t.hand.to_act)
    assert t.hand.to_act == GUEST and not t.hand.over
    t.request_leave()                                    # (their browser closed on their turn)
    assert not t.hand.over or t.guest is None
    while not t.hand.over:
        fold_or_check(t, t.hand.to_act)                  # only the host is left to act
    assert t.guest is None and not t.is_human(GUEST)
    play_hand(t)                                         # and the table carries on


def test_leaving_between_hands_is_immediate():
    t = table()
    t.request_leave()
    assert t.guest is None


def test_the_other_human_is_marked_as_a_person_not_a_mystery_player():
    t = table()
    t.start_hand()
    seats = t.state(HOST)["seats"]
    assert seats[1]["human"] is True and seats[1]["name"] == "Sam" and not any(s["human"] for s in seats[2:])
    assert t.state(GUEST)["seats"][5]["human"] is True and t.state(GUEST)["seats"][5]["name"] == "Host"       # (the host sits one seat behind the friend)


def test_players_choose_their_own_nicknames_and_they_are_tidied():
    t = table()
    t.set_host_name("  Chris  ")
    t.start_hand()
    assert t.state(GUEST)["seats"][5]["name"] == "Chris" and t.state(GUEST)["host_name"] == "Chris"
    while not t.hand.over:
        fold_or_check(t, t.hand.to_act)
    t.set_host_name("")
    assert t.host_name == "Host"                                                      # never blank
    t.set_host_name("x" * 50 + "\n\t")
    assert t.host_name == "x" * 20
    t.leave()
    t.join("  Sam\x00 the Man \n")
    assert t.guest["name"] == "Sam the Man"
    t.start_hand()
    assert t.state(HOST)["seats"][1]["name"] == "Sam the Man"
