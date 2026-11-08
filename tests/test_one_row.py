"""A hand is ONE row: Casino hands count in Analytics even without the RTA, and using both never duplicates it."""
import sqlite3
import time

import pytest

import server
from pokercalc import analytics, grind, store
from pokercalc.parser import Query


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(server, "db", lambda: store.connect(path))
    for name, val in (("_table", None), ("_practice", None), ("_tourney", None), ("_mode", None), ("_logged", 0), ("_logged_practice", 0), ("_logged_tourney", 0), ("_last_practice_hand", 0.0)):
        monkeypatch.setattr(server, name, val)
    return server.app.test_client()


def con():
    return store.connect(":memory:")


def casino_hand(hole=("As", "Kd"), board=("2c", "7h", "Jd"), net=0.5, bb=0.02, practice=False, ts=None, decisions=None, position="BTN"):
    return {"hole": list(hole), "board": list(board), "pot": 1.0, "net": net, "stack": 2.0, "bb": bb, "buy_in": 2.0, "position": position, "opps": 3,
            "decisions": decisions if decisions is not None else [{"street": 0, "suggested": None, "taken": "CALL"}], "taken": "CALL",
            "ts": ts if ts is not None else time.time(), "practice": practice}


def rta_log(c, sid, hole=("As", "Kd"), board=("2c", "7h", "Jd"), taken="CALL", result=0.5):
    q = Query(list(hole), list(board), 1.0, 0.1, 3, None)
    return store.log_hand(c, sid, q, "BTN", 2.0, 0.55, 0.3, "CALL", taken, result, [{"street": 0, "suggested": "CALL", "taken": taken}])


def rows(c):
    return [dict(r) for r in c.execute("SELECT * FROM hands ORDER BY id")]


# ---------------------------------------------------------------- Casino only: it counts
def test_a_casino_hand_with_no_rta_counts_in_analytics_as_one_row_in_its_own_session():
    c = con()
    store.sync_casino_hand(c, 1, casino_hand(net=0.5))
    r = rows(c)
    assert len(r) == 1 and r[0]["source"] == "casino" and r[0]["trainer_hand_id"] == 1 and r[0]["result"] == 0.5
    assert store.open_session(c) is None                                              # the Casino never leaves an RTA session open
    s = analytics.summary(c)
    assert s["hands"] == 1 and s["sessions"] == 1 and s["total_pl"] == 0.5 and s["bb_per_100"] == pytest.approx(100 * 0.5 / 0.02)
    assert s["mismatches"] == [] and s["follow_rate"] is None and s["decisions_total"] == 0     # no advice was used, so no advice stats
    assert s["stakes"][0]["bb"] == 0.02 and s["vpip"] == 1.0                                       # the flop was seen: voluntary


def test_casino_hands_in_one_sitting_share_a_session_and_the_session_result_is_their_sum():
    c = con()
    t0 = time.time()
    for i, net in enumerate((1.0, -0.4, 0.2)):
        store.sync_casino_hand(c, i + 1, casino_hand(hole=("As", "Kd") if i == 0 else ("Qs", "Qd") if i == 1 else ("9s", "9d"), net=net, ts=t0 + i * 60))
    sess = c.execute("SELECT * FROM sessions").fetchall()
    assert len(sess) == 1 and sess[0]["source"] == "casino" and sess[0]["balance_after"] == pytest.approx(0.8) and sess[0]["balance_before"] == 0
    s = analytics.summary(c)
    assert s["sessions"] == 1 and s["session_pl"] == [pytest.approx(0.8)] and s["mismatches"] == []
    store.sync_casino_hand(c, 4, casino_hand(hole=("8s", "8d"), net=0.1, ts=t0 + 40 * 60 + 200))          # after a 30 minute gap: a new session
    store.sync_casino_hand(c, 5, casino_hand(hole=("7s", "7d"), net=0.1, ts=t0 + 40 * 60 + 260, bb=0.05))    # another stake: another session
    store.sync_casino_hand(c, 6, casino_hand(hole=("6s", "6d"), net=0.1, ts=t0 + 40 * 60 + 300, practice=True))   # practice is kept apart
    assert sorted(r["source"] for r in c.execute("SELECT source FROM sessions")) == ["casino", "casino", "casino", "practice"]


def test_a_hand_can_only_ever_be_saved_once():
    c = con()
    first = store.sync_casino_hand(c, 7, casino_hand())
    assert first and store.sync_casino_hand(c, 7, casino_hand()) is None and len(rows(c)) == 1                 # the second call does nothing
    with pytest.raises(sqlite3.IntegrityError):                                                                 # and the database itself refuses a second row
        c.execute("INSERT INTO hands(session_id, ts, hole, board, trainer_hand_id) VALUES(1, 1, 'As Kd', '', 7)")


# ---------------------------------------------------------------- Casino and RTA together: still one row
def test_typing_a_casino_hand_into_the_rta_fills_in_the_same_row():
    c = con()
    store.sync_casino_hand(c, 1, casino_hand(net=0.5, position="CO"))
    sid = store.start_session(c, 60, 2, 0.02)                                                                   # you are using the RTA too
    got = rta_log(c, sid, board=("2c", "7h"), taken="RAISE", result=99)                                          # (typed result is ignored: the Casino knows it)
    r = rows(c)
    assert len(r) == 1 and got == r[0]["id"]
    assert r[0]["source"] == "both" and r[0]["trainer_hand_id"] == 1 and r[0]["result"] == 0.5 and r[0]["position"] == "CO"
    assert r[0]["equity"] == 0.55 and r[0]["suggested"] == "CALL" and r[0]["taken"] == "RAISE" and r[0]["session_id"] == sid
    assert c.execute("SELECT COUNT(*) FROM sessions WHERE source='casino'").fetchone()[0] == 0                   # the empty Casino session is gone
    s = analytics.summary(c)
    assert s["hands"] == 1 and s["decisions_total"] == 1 and s["follow_rate"] == 0.0                              # now it carries your advice data (it said CALL, you raised)


def test_a_hand_typed_into_the_rta_first_is_matched_when_the_casino_hand_finishes():
    c = con()
    sid = store.start_session(c, 60, 2, 0.02)
    rta_log(c, sid, board=("2c", "7h", "Jd"), result=0.4)
    assert len(rows(c)) == 1 and rows(c)[0]["source"] == "rta"
    assert store.sync_casino_hand(c, 3, casino_hand(net=0.5)) == rows(c)[0]["id"]
    r = rows(c)
    assert len(r) == 1 and r[0]["source"] == "both" and r[0]["trainer_hand_id"] == 3 and r[0]["result"] == 0.5 and r[0]["session_id"] == sid
    assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1                                          # no extra Casino session


def test_different_hands_are_never_merged_and_old_or_other_stake_ones_are_not_either():
    c = con()
    sid = store.start_session(c, 60, 2, 0.02)
    rta_log(c, sid, hole=("Ah", "Ad"))                                                                          # a different deal
    store.sync_casino_hand(c, 1, casino_hand(hole=("As", "Kd")))
    assert len(rows(c)) == 2 and sorted(r["source"] for r in rows(c)) == ["casino", "rta"]
    c2 = con()
    sid = store.start_session(c2, 60, 2, 0.02)
    rta_log(c2, sid)
    store.sync_casino_hand(c2, 1, casino_hand(ts=time.time() + 25 * 60))                                         # 25 minutes later: not the same hand
    assert len(rows(c2)) == 2
    c3 = con()
    sid = store.start_session(c3, 60, 2, 0.05)                                                                   # your RTA session is a different stake
    rta_log(c3, sid)
    store.sync_casino_hand(c3, 1, casino_hand(bb=0.02))
    assert len(rows(c3)) == 2


def test_a_board_logged_before_the_river_still_matches_and_the_casino_row_is_used_once():
    c = con()
    store.sync_casino_hand(c, 1, casino_hand(board=("2c", "7h", "Jd", "Ts", "3c")))
    sid = store.start_session(c, 60, 2, 0.02)
    rta_log(c, sid, board=("2c", "7h", "Jd"))                                                                    # you stopped typing at the flop
    assert len(rows(c)) == 1
    rta_log(c, sid, board=("2c", "7h", "Jd"))                                                                    # a second log of the same cards is a new row
    assert len(rows(c)) == 2 and [r["source"] for r in rows(c)] == ["both", "rta"]


# ---------------------------------------------------------------- what counts where
def test_casino_only_hands_do_not_count_as_rta_decisions_for_grinding_but_merged_ones_do():
    c = con()
    store.sync_casino_hand(c, 1, casino_hand(hole=("9s", "9d")))
    assert grind.summary(c)["rta"] == {"hands": 0, "decisions": 0, "followed": 0}
    store.sync_casino_hand(c, 2, casino_hand())
    sid = store.start_session(c, 60, 2, 0.02)
    rta_log(c, sid)
    assert grind.summary(c)["rta"] == {"hands": 1, "decisions": 1, "followed": 1}                                 # only the one you ran through the RTA


def test_practice_hands_count_in_analytics_but_never_for_the_rta_ladder_and_are_flagged():
    c = con()
    store.sync_casino_hand(c, 1, casino_hand(practice=True))
    r = rows(c)
    assert len(r) == 1 and r[0]["practice"] == 1 and analytics.summary(c)["hands"] == 1
    sid = store.start_session(c, 60, 2, 0.02)
    rta_log(c, sid)
    assert rows(c)[0]["practice"] == 1 and grind.summary(c)["rta"]["decisions"] == 0                               # still practice


# ---------------------------------------------------------------- through the app
def deal_and_fold(client):
    d = client.post("/api/trainer/deal").get_json()
    while d.get("legal"):
        L = d["legal"]
        d = client.post("/api/trainer/act", json={"action": "check" if L["can_check"] else "fold"}).get_json()
    return d


def test_playing_in_the_casino_without_the_rta_fills_analytics_one_row_per_hand(client):
    for _ in range(4):
        deal_and_fold(client)
    c = server.db()
    n_trainer = c.execute("SELECT COUNT(*) FROM trainer_hands").fetchone()[0]
    r = rows(c)
    assert n_trainer == 4 and len(r) == 4 and len({x["trainer_hand_id"] for x in r}) == 4 and {x["source"] for x in r} == {"casino"}
    assert analytics.summary(c)["hands"] == 4 and store.open_session(c) is None
    assert all(x["position"] and x["hole"] and x["practice"] == 0 for x in r)


def test_using_the_rta_alongside_the_casino_still_gives_one_row_per_hand(client):
    client.post("/api/session/start", json=dict(balance=60, buy_in=2, bb=0.02))
    deal_and_fold(client)
    c = server.db()
    first = rows(c)[0]
    hole, board = first["hole"].split(), first["board"].split()
    assert len(rows(c)) == 1 and first["source"] == "casino"
    client.post("/api/hand", json=dict(hole=hole, board=board, pot=0.05, call=0.02, opps=3, equity=0.4, required=0.3, suggested="FOLD", taken="FOLD",
                                       result=None, decisions=[{"street": 0, "suggested": "FOLD", "taken": "FOLD"}]))
    r = rows(server.db())
    assert len(r) == 1 and r[0]["source"] == "both" and r[0]["suggested"] == "FOLD"
    assert server.db().execute("SELECT COUNT(*) FROM trainer_hands").fetchone()[0] == 1


def test_tournament_hands_count_in_analytics_with_their_own_filter(client):
    client.post("/api/trainer/settings", json={"mode": "tournament"})
    client.post("/api/tourney/register", json={"stake": "0.01/0.02"})
    deal_and_fold(client)
    c = server.db()
    t = c.execute("SELECT net FROM trainer_hands").fetchone()["net"]                        # chips; the blinds were 15/30
    r = rows(c)
    assert len(r) == 1 and r[0]["practice"] == 2 and r[0]["source"] == "casino"
    sess = c.execute("SELECT bb, source FROM sessions WHERE id=?", (r[0]["session_id"],)).fetchone()
    assert sess["source"] == "tournament" and abs(sess["bb"] - 0.02) < 1e-9               # the level's big blind
    assert abs(r[0]["result"] / sess["bb"] - t / 30) < 1e-6                                 # in big blinds it reads like a cash hand
    assert analytics.summary(c)["hands"] == 0                                               # the default view is cash only
    assert analytics.summary(c, fmt="tournament")["hands"] == 1 and analytics.summary(c, fmt="all")["hands"] == 1
    assert analytics.summary(c, fmt="all")["total_pl"] == 0                                 # tournament chips are not cash profit


def test_the_analytics_filter_separates_cash_from_tournament_hands(client):
    deal_and_fold(client)                                                                   # one cash hand
    client.post("/api/trainer/settings", json={"mode": "tournament"})
    client.post("/api/tourney/register", json={"stake": "0.01/0.02"})
    deal_and_fold(client)                                                                   # one tournament hand
    c = server.db()
    assert [analytics.summary(c, fmt=f)["hands"] for f in ("cash", "tournament", "all")] == [1, 1, 2]
    assert client.get("/api/analytics?fmt=tournament").get_json()["format"] == "tournament"
    assert client.get("/api/analytics").get_json()["format"] == "cash"


def test_finished_tournaments_show_entries_prizes_and_roi(client):
    c = server.db()
    c.execute("INSERT INTO tournaments(ts,stake,place,size,buy_in,prize,hands,knockouts,level,final_table,heads_up,min_bb,table_moves,sharks) "
              "VALUES(1,'0.01/0.02',1,36,200,1000,80,3,5,1,1,10,1,4),(2,'0.01/0.02',20,36,200,0,50,0,4,0,0,5,2,4)")
    c.commit()
    t = analytics.summary(c, fmt="tournament")["tournaments"]
    assert t["count"] == 2 and t["entries"] == 4.0 and t["prizes"] == 10.0 and t["cashes"] == 1 and t["wins"] == 1
    assert abs(t["roi"] - 1.5) < 1e-9 and analytics.summary(c)["tournaments"] is None


def test_old_databases_get_the_new_columns_and_the_unique_index(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript("CREATE TABLE sessions(id INTEGER PRIMARY KEY, start_ts REAL, end_ts REAL, balance_before REAL, buy_in REAL, bb REAL, balance_after REAL,"
                      " deposit_before REAL DEFAULT 0, deposit_during REAL DEFAULT 0);"
                      "CREATE TABLE hands(id INTEGER PRIMARY KEY, session_id INTEGER, ts REAL, hole TEXT, board TEXT, pot REAL, call REAL, opps INTEGER,"
                      " range_pct INTEGER, position TEXT, stack REAL, equity REAL, required REAL, suggested TEXT, taken TEXT, result REAL, decisions TEXT);"
                      "INSERT INTO hands(session_id, ts, hole, board, result) VALUES(1, 1, 'As Kd', '', 5);")
    raw.commit()
    raw.close()
    c = store.connect(path)
    cols = {r[1] for r in c.execute("PRAGMA table_info(hands)")}
    assert {"source", "trainer_hand_id", "practice"} <= cols
    assert c.execute("SELECT source FROM hands").fetchone()[0] == "rta"                                          # existing hands are RTA hands
    assert any(r["name"] == "idx_hands_trainer" for r in c.execute("PRAGMA index_list(hands)"))
