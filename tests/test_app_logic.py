import time

import pytest
from pokercalc import advice, analytics, describe, preflop, store
from pokercalc.parser import Query


def test_describe_bottom_pair():
    r = describe.analyse(["6d", "5h"], ["5s", "7h", "Qh"])
    assert "bottom pair" in r["text"] and "backdoor flush" in r["text"]

def test_outs_flush_plus_gutshot():
    assert describe.analyse(["Ah", "Kh"], ["Qh", "Jh", "2c"])["outs"] == 12

def test_outs_open_ended():
    assert describe.analyse(["9h", "8h"], ["7c", "6d", "2s"])["outs"] == 8

def test_rule_of_4():
    assert describe.rule_of_2_and_4(8, 3)[0] == 0.32

def test_preflop():
    assert preflop.advise(["Ah", "Kd"], "UTG", 100, False)["action"] == "RAISE"
    assert preflop.advise(["7h", "2d"], "BTN", 100, False)["action"] == "FOLD"
    assert preflop.advise(["7h", "2d"], "BB", 100, False)["action"] == "CHECK"
    assert preflop.advise(["Ah", "Ad"], "CO", 100, True)["action"] == "RAISE"

def test_advice():
    assert advice.suggest(0.20, 1000, 500, 1)[0] == "FOLD"
    assert advice.suggest(0.80, 1000, 300, 1)[0] == "RAISE"
    assert advice.suggest(0.80, 1000, 0, 1)[0] == "BET"
    assert advice.suggest(0.30, 1000, 0, 1)[0] == "CHECK"

def test_store_and_analytics(tmp_path):
    con = store.connect(tmp_path / "t.db")
    sid = store.start_session(con, 10000, 2000, 20)
    q = Query(["Ah", "Kd"], [], 60, 40, 1, None)
    store.log_hand(con, sid, q, "BTN", 2000, 0.6, 0.4, "CALL", "CALL", 100, [{"street": 0, "suggested": "CALL", "taken": "CALL"}])
    store.log_hand(con, sid, q, "BTN", 2000, 0.3, 0.4, "FOLD", "CALL", -40, [{"street": 0, "suggested": "FOLD", "taken": "CALL"}])
    store.end_session(con, sid, 10500)
    s = analytics.summary(con)
    assert s["hands"] == 2 and s["total_pl"] == 500
    assert s["cum_results"] == [100, 60] and s["bad_calls"] == 1
    assert s["follow_rate"] == 0.5 and s["vpip"] == 1.0


def test_hand_names():
    n = describe.made_hand_name
    assert n(["Ah", "Kh", "Qh", "Jh", "2h"]) == "Flush, Ace high"
    assert n(["9h", "8h", "7c", "6d", "5s"]) == "Straight, Nine high"
    assert n(["Ah", "2d", "3c", "4s", "5h"]) == "Straight, Five high"
    assert n(["Ah", "Ad", "Kc", "Ks", "2d"]) == "Two pair, Aces and Kings"
    assert n(["5h", "5d", "5s", "Kc", "Kd"]) == "Full house, Fives full of Kings"

def test_hold_summary():
    r = describe.hold_summary(["Ah", "Kh"], ["Qh", "Jh", "2h"])
    assert r["title"].startswith("Flush, Ace high") and "best possible" in r["nuts"]
    r = describe.hold_summary(["6d", "5h"], ["5s", "7h", "Qh"])
    assert "bottom pair" in r["title"] and "backdoor flush draw" in r["draws"]

def test_chart_sizes():
    from pokercalc.preflop import OPEN, _n
    pct = {k: 100 * _n(v) / 1326 for k, v in OPEN.items()}
    assert 14 < pct["UTG"] < 18 and 40 < pct["BTN"] < 50

def test_preflop_charts():
    assert preflop.advise(["7h", "6h"], "BTN", 100, False)["action"] == "RAISE"
    assert preflop.advise(["7h", "6h"], "UTG", 100, False)["action"] == "FOLD"      # 76s is not in the UTG chart
    assert preflop.advise(["7h", "2d"], "UTG", 100, False)["action"] == "FOLD"
    assert preflop.advise(["9h", "9d"], "BTN", 100, True)["action"] == "CALL"


def test_auto_range_tightens_with_bet_size():
    from pokercalc.oprange import resolve
    small = resolve(1100, 100, 3)[2]
    big = resolve(2000, 1000, 3)[2]
    assert big < small
    assert resolve(2000, 1000, 3, table="loose")[2] > big
    assert resolve(2000, 1000, 3, table="tight")[2] < big
    assert resolve(1500, 500, 4, multi_street=True)[2] < resolve(1500, 500, 4)[2]

def test_raiser_range_is_open_chart():
    from pokercalc.oprange import resolve
    full = resolve(300, 100, 0, raiser="UTG")
    assert 14 <= full[2] <= 18
    assert resolve(2000, 1000, 3, raiser="UTG")[2] < full[2]

def test_service_auto_range():
    from pokercalc.service import evaluate
    r = evaluate(dict(hole=["Ah", "Kd"], board=["Qc", "7h", "2d"], pot=1000, call=400,
                      opps=1, range_mode="auto"), sims=2000)
    assert r["range_label"].startswith("Auto: ") and r["range_pct_used"]


def test_history_persists_and_clears(tmp_path):
    path = tmp_path / "p.db"
    con = store.connect(path)
    sid = store.start_session(con, 1000, 500, 20)
    q = Query(["Ah", "Kd"], [], 60, 40, 1, None)
    store.log_hand(con, sid, q, "BTN", 500, 0.5, 0.4, "CALL", "CALL", 100, [{"street": 0, "suggested": "CALL", "taken": "CALL"}])
    store.end_session(con, sid, 1100)
    con.close()
    con = store.connect(path)  # fresh connection = app restart
    s = analytics.summary(con)
    assert s["hands"] == 1 and s["sessions"] == 1 and s["total_pl"] == 100
    store.clear_history(con)
    s = analytics.summary(con)
    assert s["hands"] == 0 and s["sessions"] == 0 and store.open_session(con) is None


def test_deposits_are_not_winnings(tmp_path):
    con = store.connect(tmp_path / "d.db")
    a = store.start_session(con, 1000, 500, 20)
    store.end_session(con, a, 800)                       # lost 200
    b = store.start_session(con, 5800, 500, 20, deposit_before=5000)  # topped up 5000 -> 5800
    store.end_session(con, b, 6600, deposit_during=300)  # +800 on the balance, 300 of it a deposit
    s = analytics.summary(con)
    assert s["session_pl"] == [-200, 500]
    assert s["total_pl"] == 300
    assert s["balance_points"] == [1000, 800, 6600]
    assert s["balance_ex_deposits"] == [1000, 800, 1300]
    assert s["total_deposits"] == 5300

def test_rolling_and_breakdowns(tmp_path):
    con = store.connect(tmp_path / "r.db")
    sid = store.start_session(con, 1000, 500, 20)
    for i in range(12):
        q = Query(["Ah", "Kd"], ["2c", "7h", "9d"] if i % 2 else [], 100, 20, 1, None)
        store.log_hand(con, sid, q, "BTN" if i % 2 else "UTG", 500, 0.5, 0.2, "CALL", "CALL",
                       50 if i % 3 else -20)
    store.end_session(con, sid, 1100)
    s = analytics.summary(con)
    assert len(s["win_rate_roll"]) == 12 and s["win_rate_roll"][0] is None and s["win_rate_roll"][-1] is not None
    assert {p["name"] for p in s["by_position"]} == {"BTN", "UTG"}
    assert {p["name"] for p in s["by_street"]} == {"Preflop", "Flop"}
    assert s["per_session"][0]["hands"] == 12 and s["ev_calls"] > 0


def test_advice_split(tmp_path):
    con = store.connect(tmp_path / "a.db")
    sid = store.start_session(con, 1000, 500, 20)
    q = Query(["Ah", "Kd"], [], 60, 40, 1, None)
    store.log_hand(con, sid, q, "BTN", 500, 0.5, 0.4, "CALL", "CALL", 100, [{"street": 0, "suggested": "CALL", "taken": "CALL"}])
    store.log_hand(con, sid, q, "BTN", 500, 0.5, 0.4, "CALL", "FOLD", -40, [{"street": 0, "suggested": "CALL", "taken": "FOLD"}])
    s = analytics.summary(con)
    by = {r["name"]: r for r in s["advice_split"]}
    assert by["Followed the suggestion"]["avg"] == 100 and by["Did something else"]["net"] == -40


def test_follow_is_judged_per_decision(tmp_path):
    con = store.connect(tmp_path / "d.db")
    sid = store.start_session(con, 1000, 500, 20)
    q = Query(["Ah", "Kd"], [], 60, 40, 1, None)
    # one hand, three decisions: deviated on the first, followed the next two (the later ones must not hide it)
    three = [{"street": 0, "suggested": "FOLD", "taken": "CALL"},
             {"street": 3, "suggested": "CHECK", "taken": "CHECK"},
             {"street": 4, "suggested": "BET", "taken": "BET"}]
    store.log_hand(con, sid, q, "BTN", 500, 0.5, 0.4, "BET", "BET", 50, three)
    store.log_hand(con, sid, q, "BTN", 500, 0.5, 0.4, "CALL", "CALL", 20)   # saved before decisions were recorded
    s = analytics.summary(con)
    assert s["decisions_total"] == 3 and abs(s["follow_rate"] - 2 / 3) < 1e-9
    assert [r["name"] for r in s["advice_split"]] == ["Did something else"]


def test_coach_prompt_and_runner(tmp_path):
    import json
    from pokercalc import coach
    con = store.connect(tmp_path / "c.db")
    sid = store.start_session(con, 1000, 500, 20)
    q = Query(["Ah", "Kd"], ["2c", "7h", "9d"], 600, 200, 2, 25)
    store.log_hand(con, sid, q, "BTN", 500, 0.31, 0.25, "CALL", "CALL", -200)
    store.end_session(con, sid, 800)
    prompt = coach.build_prompt(con)
    assert "STATS:" in prompt and "RECENT HANDS" in prompt and "Ah Kd" in prompt and "net_result_chips" in prompt
    json.loads(prompt.split("STATS:\n")[1].split("\n\nRECENT")[0])  # stats block is valid JSON

    class Fake:
        returncode, stdout, stderr = 0, "## Overall picture\nok", ""
    seen = {}
    def runner(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return Fake()
    ok, text = coach.run_coach("hi", runner=runner)
    assert ok and text.startswith("## Overall") and "--tools" in seen["cmd"] and seen["kw"]["input"] == "hi"


def test_raise_info_tiers():
    ri = preflop.raise_info
    assert ri(0, 100, 5000, "BTN")["tier"] == "none"
    assert ri(100, 100, 5000, "BTN")["tier"] == "limp"                 # 1bb to call = a limp
    assert ri(250, 100, 5000, "BTN")["tier"] == "normal"               # raise to 2.5bb
    assert ri(150, 100, 5000, "BB")["raise_bb"] == 2.5                 # BB already posted 1bb
    assert ri(700, 100, 5000, "CO")["tier"] == "big"
    assert ri(1500, 100, 10000, "CO")["tier"] == "shove"
    assert ri(2500, 100, 5000, "CO")["tier"] == "shove"                # 50% of stack
    assert ri(300, None, None, "BTN")["tier"] == "normal"              # no big blind known

def test_preflop_by_raise_size():
    aj = ["Ah", "Jd"]
    assert preflop.advise(aj, "BTN", 100, True, "normal", 2.5)["action"] == "CALL"
    assert preflop.advise(aj, "BTN", 100, True, "big", 8)["action"] == "FOLD"      # AJo too weak for a 3-bet
    assert preflop.advise(aj, "BTN", 100, True, "shove", 40)["action"] == "FOLD"
    assert preflop.advise(["Ah", "Kd"], "BTN", 100, True, "shove", 40)["action"] == "CALL"
    assert preflop.advise(["Kh", "Kd"], "CO", 100, True, "big", 8)["action"] == "RAISE"
    assert preflop.advise(["Th", "Td"], "CO", 100, True, "big", 8)["action"] == "CALL"

def test_service_detects_raise_size():
    from pokercalc.service import evaluate
    base = dict(hole=["Ah", "Jd"], board=[], pot=450, opps=1, position="BTN", stack=10000, bb=100, range_mode="auto")
    n = evaluate({**base, "call": 250}, sims=1500)
    b = evaluate({**base, "call": 800, "pot": 1000}, sims=1500)
    assert "normal-size raise" in n["preflop"]["size"] and n["action"] == "CALL"
    assert "big raise" in b["preflop"]["size"] and b["action"] == "FOLD"
    assert b["range_pct_used"] < n["range_pct_used"]                    # bigger raise = tighter assumed range


def test_old_database_is_upgraded(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))   # a database from before deposits existed
    old.executescript("""
      CREATE TABLE sessions(id INTEGER PRIMARY KEY, start_ts REAL, end_ts REAL,
        balance_before REAL, buy_in REAL, bb REAL, balance_after REAL);
      CREATE TABLE hands(id INTEGER PRIMARY KEY, session_id INTEGER, ts REAL, hole TEXT, board TEXT,
        pot REAL, call REAL, opps INTEGER, range_pct INTEGER, position TEXT, stack REAL, equity REAL,
        required REAL, suggested TEXT, taken TEXT, result REAL);
      INSERT INTO sessions VALUES(1, 1000, 2000, 5000, 500, 20, 5300);
    """)
    old.commit(); old.close()
    con = store.connect(path)          # should add the new columns without losing the row
    cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
    assert {"deposit_before", "deposit_during"} <= cols
    s = analytics.summary(con)
    assert s["session_pl"] == [300] and s["total_deposits"] == 0
    store.connect(path)                # running it again must be harmless


# ---- postflop advice v2 -------------------------------------------------------------------
FD = {"draws": ["flush draw"], "outs": 9, "texture": ["3 hearts on board - flush possible"], "made": "High Card"}
DRY = {"draws": [], "outs": 0, "texture": [], "made": "Pair"}
COMBO = {"draws": ["flush draw", "open-ended straight draw"], "outs": 15, "texture": ["3 hearts on board - flush possible"], "made": "High Card"}
FLOP = ["Kc", "7h", "2d"]


def pf(eq, pot=1000, call=0, opps=1, stack=10000, pos="BTN", hold=DRY, board=FLOP):
    return advice.suggest_postflop(eq, pot, call, opps, stack, pos, board, hold)


def test_value_bet_size_follows_board_texture():
    a, _, sz = pf(0.90, hold=DRY)
    assert a == "BET" and "500" in sz                                   # dry board: half pot
    a, _, sz = pf(0.90, hold={**DRY, "texture": ["straight possible"]})
    assert a == "BET" and "750" in sz                                   # wet board: bigger

def test_position_moves_the_value_line():
    assert pf(0.64, pos="BTN")[0] == "BET"
    assert pf(0.64, pos="BB")[0] == "CHECK"

def test_semi_bluff_with_a_draw():
    a, why, sz = pf(0.35, hold=FD)
    assert a == "BET" and "semi-bluff" in why and sz
    assert pf(0.35, hold=FD, opps=3)[0] == "CHECK"                      # too many opponents to bluff
    assert pf(0.25, hold=FD, pos="BB")[0] == "CHECK"                    # weak, out of position: free card
    assert pf(0.35, hold=FD, board=FLOP + ["3c", "9s"])[0] == "CHECK"   # river: the draw is gone

def test_medium_hand_checks():
    assert pf(0.45)[0] == "CHECK"

def test_facing_bet_fold_call_and_implied_odds():
    assert pf(0.20, call=500, pot=1500)[0] == "FOLD"
    a, why, _ = pf(0.22, call=500, pot=1500, hold=FD, stack=10000)
    assert a == "CALL" and "implied odds" in why
    assert pf(0.22, call=500, pot=1500, hold=FD, stack=1500)[0] == "FOLD"   # shallow: no implied odds

def test_raise_sizing_and_commitment():
    a, _, sz = pf(0.90, call=300, pot=1300, stack=20000)
    assert a == "RAISE" and "900" in sz                                  # 3x their bet
    a, _, sz = pf(0.90, call=300, pot=1300, stack=1200)
    assert a == "RAISE" and "all-in" in sz                               # raise would commit the stack
    assert pf(0.90, call=1000, pot=2000, stack=1000)[0] == "CALL"        # already all-in: only call

def test_combo_draw_semi_bluff_raise():
    a, why, _ = pf(0.55, call=300, pot=1300, hold=COMBO)
    assert a == "RAISE" and "semi-bluff" in why
    assert pf(0.45, call=300, pot=1300, hold=COMBO, opps=2)[0] == "CALL"  # not into several opponents

def test_service_returns_sizing_postflop():
    from pokercalc.service import evaluate
    r = evaluate(dict(hole=["Ah", "Ad"], board=["Kc", "7h", "2d"], pot=600, call=0, opps=1, position="BTN",
                      stack=10000, bb=100, range_mode=40), sims=2000)
    assert r["action"] == "BET" and r["sizing"]


def test_open_charts_match_published_percentages():
    from pokercalc.preflop import OPEN, _n
    # PokerSkill 6-max opening ranges: UTG 14.3, HJ 18.9, CO 25.5, BTN 43.3, SB 33.0 (% of hands)
    for pos, want in {"UTG": 14.3, "HJ": 18.9, "CO": 25.5, "BTN": 43.3, "SB": 33.0}.items():
        assert abs(100 * _n(OPEN[pos]) / 1326 - want) < 0.1, pos

def test_range_syntax_with_descending_kickers():
    from pokercalc.preflop import expand
    assert expand("A5s-A2s") == {"A5s", "A4s", "A3s", "A2s"}
    assert expand("A9s-A6s") == {"A9s", "A8s", "A7s", "A6s"}

def test_facing_raise_by_raiser_position():
    aqs, jj = ["Ah", "Qs"], ["Jh", "Jd"]
    assert preflop.advise(aqs, "BTN", 100, True, "normal", 2.5, "CO")["action"] == "CALL"     # call, not 3-bet
    assert preflop.advise(jj, "BTN", 100, True, "normal", 2.5, "CO")["action"] == "RAISE"     # JJ 3-bets a CO open
    assert preflop.advise(jj, "BTN", 100, True, "normal", 2.5, "UTG")["action"] == "CALL"     # but only calls a UTG open
    assert preflop.advise(["Qh", "Qd"], "BTN", 100, True, "normal", 2.5, "UTG")["action"] == "RAISE"


def test_big_blind_defence_widths_and_sanity():
    from pokercalc.bbdefense import defend_set, TARGET
    from pokercalc.preflop import _n
    for r, want in TARGET.items():
        assert abs(100 * _n(defend_set(r)) / 1326 - want) < 1.0, r
    assert len(defend_set("UTG")) < len(defend_set("CO")) < len(defend_set("BTN"))
    btn = defend_set("BTN")
    assert {"76s", "T9s", "22", "A9o"} <= btn and not ({"72o", "K4o", "32o"} & btn)

def test_big_blind_vs_raise_and_small_blind_rules():
    assert preflop.advise(["7h", "6h"], "BB", 100, True, "normal", 2.0, "BTN")["action"] == "CALL"
    assert preflop.advise(["7h", "2d"], "BB", 100, True, "normal", 2.0, "BTN")["action"] == "FOLD"
    assert preflop.advise(["7h", "6h"], "BB", 100, True, "normal", 2.0, "UTG")["action"] == "FOLD"   # tighter vs UTG
    assert preflop.advise(["Ah", "As"], "BB", 100, True, "normal", 2.0, "CO")["action"] == "RAISE"
    assert preflop.advise(["Th", "Td"], "SB", 100, True, "normal", 2.0, "UTG")["action"] == "FOLD"    # SB never flats an early open
    assert preflop.advise(["Ah", "5h"], "SB", 100, True, "normal", 2.0, "UTG")["action"] == "RAISE"   # A5s is a 3-bet bluff


def test_limped_pot_advice():
    from pokercalc.service import evaluate
    assert preflop.advise(["2s", "2h"], "BTN", 100, False, limpers=True, opps=4)["action"] == "CALL"   # set-mine, don't raise
    assert preflop.advise(["Ah", "Ad"], "BTN", 100, False, limpers=True, opps=4)["action"] == "RAISE"  # isolate
    assert preflop.advise(["7h", "2d"], "BTN", 100, False, limpers=True)["action"] == "FOLD"
    assert preflop.advise(["7h", "2d"], "BB", 100, False, limpers=True)["action"] == "CHECK"
    d = evaluate(dict(hole=["Ah", "Ad"], board=[], pot=10, call=0, opps=3, range_mode="auto",
                      position="BTN", facing="limp", bb=4), sims=300)
    assert d["action"] == "RAISE" and "3.5bb" in d["sizing"]


def test_one_pair_on_wet_board_calls_not_raises():
    from pokercalc.service import evaluate
    b = dict(hole=["Ts", "As"], board=["3h", "Tc", "8h", "9s"], pot=400, call=66, opps=1,
             range_mode="auto", position="BTN", stack=843)
    d = evaluate(b, sims=2000)
    assert d["action"] == "CALL" and "Straight/flush" not in d["range_label"] and "tighter" in d["range_label"]
    dry = evaluate({**b, "board": ["3h", "Tc", "8d", "Ks"]}, sims=2000)
    assert "tighter" not in dry["range_label"]


def test_limpers_detected_from_pot():
    from pokercalc.service import evaluate
    base = dict(hole=["2s", "2h"], board=[], opps=3, range_mode="auto", position="BTN", bb=4)
    assert evaluate({**base, "pot": 6, "call": 0}, sims=200)["action"] == "RAISE"           # blinds only: open
    assert evaluate({**base, "pot": 14, "call": 0}, sims=200)["action"] == "CALL"           # 2 limpers: set-mine


def test_backup_round_trip(tmp_path):
    import subprocess
    from pokercalc import backup
    con = store.connect(tmp_path / "a.db")
    sid = store.start_session(con, 1000, 500, 20)
    q = Query(["Ah", "Kd"], [], 60, 40, 1, None)
    store.log_hand(con, sid, q, "BTN", 500, 0.5, 0.4, "CALL", "CALL", 100, [{"street": 0, "suggested": "CALL", "taken": "CALL"}])
    store.end_session(con, sid, 1100)
    store.trainer_save(con, {"stake": "0.50/1", "hero_stack": 12345})
    store.log_trainer_hand(con, "0.50/1", 1, "BTN", ["Ah", "Kd"], ["2c", "3d", "4h"], 600, 250, True, [])
    folder = tmp_path / "data"
    folder.mkdir()
    backup.export_data(con, folder)
    new = store.connect(tmp_path / "b.db")
    assert backup.restore(new, folder) == {"sessions": 1, "hands": 1, "trainer_hands": 1, "trainer_state": 1, "tournaments": 0}
    assert store.trainer_load(new)["hero_stack"] == 12345
    assert analytics.summary(new)["total_pl"] == 100 and analytics.summary(new)["decisions_total"] == 1
    with pytest.raises(SystemExit):   # never overwrites a database that already has data
        backup.restore(new, folder)


# ---- opponent type and fold-equity maths
def test_fold_math_values():
    pure, with_eq = advice.fold_math(100, 50, 0.40)
    assert abs(pure - 1 / 3) < 1e-9 and with_eq == 0.0           # 40% equity carries a half-pot bet by itself
    _, w = advice.fold_math(60, 30, 0.11)
    assert 0.20 < w < 0.24                                         # with little equity it needs ~22% folds
    assert advice.fold_math(0, 50) == (None, None)


def test_bluffs_against_folders_and_ordinary_players_but_not_callers():
    from pokercalc import service
    base = dict(hole=["7h", "2c"], board=["Ks", "8d", "3c"], pot=60, call=0, opps=1, position="BTN", stack=400,
                bb=2, range_mode="auto")
    folds = service.evaluate({**base, "opp_type": "folds"}, sims=1500)
    assert folds["action"] == "BET" and folds["fold_equity"]
    assert service.evaluate({**base, "opp_type": "normal"}, sims=1500)["action"] == "BET"      # air, dry board, in position, one opponent
    assert service.evaluate({**base, "opp_type": "calls"}, sims=1500)["action"] == "CHECK"     # never bluff a calling station


def test_the_advice_says_when_it_is_bluffing():
    from pokercalc import service
    base = dict(hole=["7h", "2c"], board=["Ks", "8d", "3c"], pot=60, call=0, opps=1, position="BTN", stack=400, bb=2, range_mode="auto")
    assert service.evaluate({**base, "opp_type": "folds"}, sims=1500)["bluff"] == "BLUFF"
    draw = dict(base, hole=["Ah", "5h"], board=["Kh", "8h", "3c"])
    assert service.evaluate({**draw, "opp_type": "normal"}, sims=1500)["bluff"] in ("SEMI-BLUFF", None)
    value = dict(base, hole=["Kh", "Kd"], board=["Ks", "8d", "3c"])
    assert service.evaluate({**value, "opp_type": "normal"}, sims=1500)["bluff"] is None
