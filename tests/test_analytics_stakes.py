"""Analytics across stakes: money is real money, big blinds are comparable, and one stake can be looked at alone."""
import json
import time

import pytest

import server
from pokercalc import analytics, store
from pokercalc.parser import Query


def hand(con, sid, buy, result, taken="RAISE", position="BTN"):
    q = Query(["Ah", "Kd"], ["2c", "3d", "4h"], 1, 0, 1, None)
    store.log_hand(con, sid, q, position, buy, 0.5, 0.0, "RAISE", taken, result,
                   [{"street": 3, "suggested": "RAISE", "taken": taken}])


def session(con, buy, bb, result, **kw):
    sid = store.start_session(con, buy, buy, bb)
    hand(con, sid, buy, result, **kw)
    store.end_session(con, sid, buy + result)


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "a.db")
    session(c, 5, 0.05, +5)                        # micro: a 5.00 buy-in, win 5.00 = +100 big blinds
    session(c, 100, 1.00, -10, taken="CALL")       # bigger stake: lose 10.00 = -10 big blinds
    return c


def test_money_and_big_blinds_tell_different_stories(con):
    s = analytics.summary(con)
    assert s["total_pl"] == -5                                     # in money you are down
    assert s["winnings_bb"] == [0.0, 100.0, 90.0]                  # in big blinds you are up 90
    assert s["session_pl_bb"] == [100.0, -10.0]
    assert s["cum_results"] == [5, -5] and s["cum_results_bb"] == [100.0, 90.0]


def test_position_street_and_advice_charts_have_big_blind_versions(con):
    s = analytics.summary(con)
    (btn,) = [p for p in s["by_position"] if p["name"] == "BTN"]
    assert btn["net"] == -5 and btn["net_bb"] == 90 and btn["hands_bb"] == 2
    (flop,) = s["by_street"]
    assert flop["net"] == -5 and flop["net_bb"] == 90
    by = {a["name"]: a for a in s["advice_split"]}
    assert by["Followed the suggestion"]["avg"] == 5 and by["Followed the suggestion"]["avg_bb"] == 100
    assert by["Did something else"]["avg"] == -10 and by["Did something else"]["avg_bb"] == -10


def test_the_list_of_stakes_is_always_complete(con):
    s = analytics.summary(con, stake=0.05)
    assert [(x["bb"], x["sessions"], x["hands"]) for x in s["stakes"]] == [(0.05, 1, 1), (1.0, 1, 1)]
    assert s["stake"] == 0.05


def test_looking_at_one_stake_leaves_out_the_other(con):
    micro, big = analytics.summary(con, stake=0.05), analytics.summary(con, stake=1.0)
    assert (micro["hands"], micro["sessions"], micro["total_pl"], micro["cum_results"]) == (1, 1, 5, [5])
    assert (big["hands"], big["sessions"], big["total_pl"], big["cum_results"]) == (1, 1, -10, [-10])
    assert micro["winnings_bb"] == [0.0, 100.0] and big["winnings_bb"] == [0.0, -10.0]
    assert [p["name"] for p in micro["advice_split"]] == ["Followed the suggestion"]
    nothing = analytics.summary(con, stake=0.5)                    # a stake you never played
    assert nothing["hands"] == 0 and nothing["sessions"] == 0 and nothing["total_pl"] == 0


def test_big_blind_ev_of_calls(tmp_path):
    c = store.connect(tmp_path / "b.db")
    sid = store.start_session(c, 100, 100, 2.0)
    q = Query(["Ah", "Kd"], ["2c", "3d", "4h"], 10, 5, 1, None)
    store.log_hand(c, sid, q, "BTN", 100, 0.2, 0.33, "CALL", "CALL", -5, [])       # a bad call: 20% equity, needs 33%
    store.end_session(c, sid, 95)
    s = analytics.summary(c)
    assert s["ev_bad_calls"] < 0 and s["ev_bad_calls_bb"] == pytest.approx(s["ev_bad_calls"] / 2.0)


def test_the_web_api_takes_a_stake(con, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "db", lambda: con)
    c = server.app.test_client()
    both = c.get("/api/analytics").get_json()
    micro = c.get("/api/analytics?stake=0.05").get_json()
    assert both["hands"] == 2 and micro["hands"] == 1 and micro["total_pl"] == 5
    assert [x["bb"] for x in micro["stakes"]] == [0.05, 1.0]
    assert c.get("/api/analytics?stake=banana").get_json()["hands"] == 2      # a bad value is ignored, not an error


# ---------------------------------------------------------------- VPIP and mismatched sessions (found checking real data)
def _hand(con, sid, board, taken, decisions, result=0):
    con.execute("INSERT INTO hands(session_id,ts,hole,board,pot,call,opps,range_pct,position,stack,equity,required,suggested,taken,result,decisions) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, time.time(), "As Ks", board, 1, 1, 1, 0, "BTN", 10, 0.5, 0.3, taken, taken, result, json.dumps(decisions) if decisions is not None else None))
    con.commit()


def test_vpip_counts_hands_that_reached_the_flop():
    con = store.connect(":memory:")
    sid = store.start_session(con, 100, 100, 2)
    for _ in range(8):                                               # eight hands folded before the flop
        _hand(con, sid, "", "FOLD", [{"street": 0, "suggested": "FOLD", "taken": "FOLD"}])
    _hand(con, sid, "", "RAISE", [{"street": 0, "suggested": "RAISE", "taken": "RAISE"}])                     # raised, everyone folded
    _hand(con, sid, "Ah 7d 2c", "BET", [{"street": 0, "suggested": "CALL", "taken": "CALL"}, {"street": 3, "suggested": "BET", "taken": "BET"}])   # saw a flop
    s = analytics.summary(con)
    assert s["vpip"] == 2 / 10                                       # 2 of 10 hands (it used to say 1 of 9)


def test_vpip_for_older_hands_without_decisions_and_free_big_blind_checks():
    con = store.connect(":memory:")
    sid = store.start_session(con, 100, 100, 2)
    _hand(con, sid, "Ah 7d 2c", "CHECK", None)                       # old hand that saw a flop: counted
    _hand(con, sid, "", "FOLD", None)
    _hand(con, sid, "Ah 7d 2c", "CHECK", [{"street": 0, "suggested": "CHECK", "taken": "CHECK"}, {"street": 3, "suggested": "CHECK", "taken": "CHECK"}])   # free look from the big blind: not voluntary
    assert analytics.summary(con)["vpip"] == 1 / 3


def test_a_session_whose_balances_disagree_with_its_hands_is_flagged():
    con = store.connect(":memory:")
    bad = store.start_session(con, 100.0, 5.0, 0.05)                 # started with the whole account, ended with the table stack
    _hand(con, bad, "", "FOLD", None, result=-0.5)
    store.end_session(con, bad, 4.68)
    ok = store.start_session(con, 1000, 1000, 2)
    _hand(con, ok, "", "FOLD", None, result=300)                     # 150 bb
    store.end_session(con, ok, 1296)                                 # 148 bb: close enough
    m = analytics.summary(con)["mismatches"]
    assert len(m) == 1 and m[0]["balance_before"] == 100.0 and m[0]["session_bb"] < -1000 and m[0]["hands_bb"] == -10.0
