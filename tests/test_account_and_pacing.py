"""Your account (bankroll vs the stack on the table, in a currency) and how fast the bots act."""
import random
import statistics

import pytest

from pokercalc import stakes
from pokercalc.bots import Bot, make_traits
from pokercalc.game import Hand, IllegalAction
from pokercalc.table import DEAL_PAUSE_S, SPEEDS, Table


def table(stake="0.02/0.05", **kw):
    kw.setdefault("seed", 1)
    kw.setdefault("bot_sims", 10)
    kw.setdefault("speed", "instant")
    return Table(stake, **kw)


def play_out(t):
    while not t.hand.over:
        L = t.hand.legal(0)
        t.hero_act("check" if L["can_check"] else "fold")


# ---------------------------------------------------------------- account
def test_buy_in_comes_out_of_the_account():
    t = table(account=10000)                       # 100.00 in the account, a 5.00 buy-in
    assert (t.hero_stack, t.bankroll, t.state()["account"]) == (500, 9500, 10000)
    assert t.state()["account_buy_ins"] == 20.0 and t.state()["currency"] == "£"


def test_cash_out_and_rebuy_move_money_between_account_and_table():
    t = table(account=10000)
    t.cash_out()
    assert (t.hero_stack, t.bankroll) == (0, 10000)
    t.rebuy()                                       # no amount: the buy-in
    assert (t.hero_stack, t.bankroll) == (500, 9500)
    t.set_hero_stack(300)                           # 2.00 goes back to the account
    assert (t.hero_stack, t.bankroll) == (300, 9700)
    t.rebuy(100)
    assert (t.hero_stack, t.bankroll) == (400, 9600)


def test_you_cannot_spend_money_you_do_not_have():
    t = table(account=600, buy_in=300)             # 3.00 on the table, 3.00 in the account
    assert (t.hero_stack, t.bankroll) == (300, 300)
    with pytest.raises(ValueError, match="off the table"):
        t.set_hero_stack(900)                      # needs 6.00 more, only 3.00 is there
    t.rebuy(900)                                   # capped at the maximum buy-in (5.00): 2.00 more
    assert (t.hero_stack, t.bankroll) == (500, 100)
    t.set_account(500)                             # withdraw the rest
    with pytest.raises(ValueError, match="no money left"):
        t.rebuy(100)


def test_a_stake_you_cannot_afford_is_refused_with_a_reason():
    with pytest.raises(ValueError, match="account"):
        Table("0.02/0.05", account=150, seed=1, bot_sims=5)        # minimum buy-in is 2.00
    t = table(account=10000)
    with pytest.raises(ValueError, match="Not enough in your account"):
        t.set_stake("5/10")                         # minimum buy-in 400
    assert (t.stake, t.hero_stack, t.bankroll) == ("0.02/0.05", 500, 9500)      # nothing changed


def test_changing_stake_returns_your_stack_and_takes_the_new_buy_in():
    t = table(account=10000)
    t.set_hero_stack(300)
    t.set_stake("0.50/1", buy_in=4000)              # the minimum, 40bb
    assert (t.stake, t.hero_stack, t.bankroll) == ("0.50/1", 4000, 6000)
    assert t.state()["account"] == 10000


def test_set_account_is_a_deposit_or_withdrawal():
    t = table(account=10000)
    t.set_account(25000)
    assert (t.hero_stack, t.bankroll) == (500, 24500)
    with pytest.raises(ValueError, match="less than"):
        t.set_account(100)                          # below what is on the table


def test_account_changes_by_exactly_what_you_win_or_lose():
    t = table(account=10000)
    for _ in range(8):
        before = t.bankroll + t.hero_stack
        for _ in range(40):
            st = t.start_hand()
            if "legal" in st or t.hand.over:
                break
        if not t.hand.over:
            play_out(t)
        assert t.bankroll + t.hero_stack == before + t.last["net"]
        if t.hero_stack < t.bb:
            t.rebuy()


def test_currency_is_only_a_label():
    t = table()
    t.set_currency("$")
    assert t.state()["currency"] == "$" and stakes.ladder("$")[1]["blinds"] == "$0.02/$0.05"
    assert stakes.ladder("£")[1]["buy_in"] == "£2 - £5"
    with pytest.raises(ValueError):
        t.set_currency("¥")


def test_the_account_survives_a_save_even_when_nearly_empty():
    t = table(account=10000)
    t.set_hero_stack(200)
    t.bankroll = 3                                  # almost nothing off the table
    back = Table.from_dict(t.to_dict(), seed=2, bot_sims=5, speed="instant")
    assert (back.hero_stack, back.bankroll, back.currency) == (200, 3, "£")


# ---------------------------------------------------------------- pacing
def first_frames(speed, seed=5, timer_s=0, **kw):
    t = Table("0.02/0.05", seed=3, bot_sims=10, speed=speed, timer_s=timer_s, account=10 ** 7, **kw)
    t.start_hand(seed=seed)
    return t, t.pop_frames()


def test_instant_has_no_waits_and_normal_does():
    _, inst = first_frames("instant")
    _, norm = first_frames("normal")
    assert inst[0]["event"] == "deal" and sum(f["delay"] for f in inst) == 0
    assert len(inst) == len(norm)                  # the same actions happen, only the waiting differs
    assert all(f["delay"] > 0 for f in norm[1:])
    if len(norm) > 1:
        assert norm[1]["delay"] >= DEAL_PAUSE_S    # the first action waits for the cards to be dealt


def test_speed_setting_scales_the_waits():
    totals = {s: sum(f["delay"] for f in first_frames(s)[1]) for s in ("fast", "normal", "slow")}
    assert totals["fast"] < totals["normal"] < totals["slow"]
    assert SPEEDS["instant"] == 0 and SPEEDS["fast"] < SPEEDS["normal"] < SPEEDS["slow"]
    t = table()
    with pytest.raises(ValueError):
        t.set_speed("warp")


def test_each_step_is_a_snapshot_and_the_last_one_matches_the_table():
    t, frames = first_frames("normal")
    states = [f["state"] for f in frames]
    assert all("legal" not in s and "seconds_left" not in s for s in states)      # steps carry no controls
    pots = [s["pot"] for s in states]
    assert pots == sorted(pots)                    # the pot only grows within a hand's early steps
    now = t.state()
    assert states[-1]["pot"] == now["pot"] and states[-1]["street"] == now["street"] and states[-1]["to_act"] == now["to_act"]


def test_the_shot_clock_starts_after_the_bots_have_finished():
    now = [1000.0]
    t = Table("0.02/0.05", seed=3, bot_sims=10, speed="normal", timer_s=10, clock=lambda: now[0], account=10 ** 7)
    for seed in range(1, 40):
        st = t.start_hand(seed=seed)
        frames = t.pop_frames()
        if "legal" in st:
            wait = sum(f["delay"] for f in frames)
            assert st["seconds_left"] == pytest.approx(wait + 10, abs=0.02)      # the page plays `wait` seconds, then 10 remain
            return
        play_out(t) if not t.hand.over else None
    pytest.fail("never reached a hand where we had to act")


def test_your_own_action_appears_immediately():
    t = table(speed="normal")
    for seed in range(1, 40):
        st = t.start_hand(seed=seed)
        t.pop_frames()
        if "legal" in st:
            L = st["legal"]
            t.hero_act("check" if L["can_check"] else "fold")
            frames = t.pop_frames()
            assert frames[0]["event"] == "hero" and frames[0]["delay"] == 0
            return
        if not t.hand.over:
            play_out(t)
    pytest.fail("never got to act")


def test_snap_folds_are_faster_than_raises_and_players_have_their_own_tempo():
    times = {"fold": [], "raise": []}
    for g in range(120):
        bots = [Bot("tag", seed=g * 10 + i, sims=10) for i in range(6)]
        h = Hand([200] * 6, button=g % 6, seed=g)
        while not h.over:
            s = h.to_act
            a = bots[s].decide(h, s)
            k = a if isinstance(a, str) else a[0]
            if k in times:
                times[k].append(bots[s].think_time(h, s, a))
            h.act(s, a)
    assert statistics.median(times["fold"]) < statistics.median(times["raise"])
    assert min(times["fold"]) < 0.8 and max(times["raise"]) > 2.0              # some very quick, some slow
    tempos = {make_traits("station", random.Random(i))["tempo"] for i in range(8)}
    assert len(tempos) > 4                          # individuals, not clones


def test_thinking_time_never_changes_how_a_bot_plays():
    a = Hand([200] * 6, button=0, seed=9)
    b = Hand([200] * 6, button=0, seed=9)
    bots_a = [Bot("reg", seed=4 + i, sims=10) for i in range(6)]
    bots_b = [Bot("reg", seed=4 + i, sims=10) for i in range(6)]
    while not a.over:
        s = a.to_act
        act_a = bots_a[s].decide(a, s)
        bots_a[s].think_time(a, s, act_a)           # a asks how long it would think, b does not
        act_b = bots_b[s].decide(b, s)
        assert act_a == act_b
        a.act(s, act_a)
        b.act(s, act_b)
