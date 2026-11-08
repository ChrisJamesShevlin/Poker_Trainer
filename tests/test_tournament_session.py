"""RTA tournament sessions: an entry fee instead of a balance, hands typed in chips and saved in big blinds, a result at the end."""
import pytest

import server
from pokercalc import analytics, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(server, "db", lambda: store.connect(path))
    for name, val in (("_table", None), ("_practice", None), ("_tourney", None), ("_mode", None), ("_logged", 0), ("_logged_practice", 0), ("_logged_tourney", 0), ("_last_practice_hand", 0.0)):
        monkeypatch.setattr(server, name, val)
    return server.app.test_client()


def start(client, entry=2, chips=3000):
    return client.post("/api/session/start", json={"format": "tournament", "buy_in": entry, "start_chips": chips})


def hand(client, **kw):
    body = dict(hole=["Ah", "Kd"], board=["2c", "7h", "Jd"], pot=195, call=75, opps=3, equity=0.4, required=0.3, suggested="CALL", taken="CALL",
                result=-75, stack=2970, bb=30, decisions=[{"street": 3, "suggested": "CALL", "taken": "CALL"}])
    body.update(kw)
    return client.post("/api/hand", json=body)


def test_a_tournament_session_has_an_entry_fee_and_no_balance(client):
    assert start(client).status_code == 200
    s = client.get("/api/session").get_json()["active"]
    assert s["format"] == "tournament" and s["buy_in"] == 2 and abs(s["bb"] - 0.02) < 1e-9 and s["start_chips"] == 3000


def test_hands_are_typed_in_chips_and_saved_in_big_blinds(client):
    start(client)
    assert hand(client).status_code == 200
    r = server.db().execute("SELECT h.*, s.bb AS sbb FROM hands h JOIN sessions s ON s.id=h.session_id").fetchone()
    assert r["practice"] == 2 and r["source"] == "rta"
    assert abs(r["result"] / r["sbb"] - (-75 / 30)) < 1e-9                      # -75 chips at a 30 big blind = -2.5 bb
    assert abs(r["pot"] / r["sbb"] - 195 / 30) < 1e-9 and abs(r["stack"] / r["sbb"] - 2970 / 30) < 1e-9


def test_a_tournament_hand_needs_the_big_blind(client):
    start(client)
    r = hand(client, bb=None)
    assert r.status_code == 400 and "big blind" in r.get_json()["error"]
    assert server.db().execute("SELECT COUNT(*) FROM hands").fetchone()[0] == 0


def test_the_tournament_filter_shows_these_hands_and_cash_does_not(client):
    start(client)
    hand(client)
    c = server.db()
    assert [analytics.summary(c, fmt=f)["hands"] for f in ("cash", "tournament", "all")] == [0, 1, 1]
    assert analytics.summary(c, fmt="tournament")["sessions"] == 1 and analytics.summary(c)["sessions"] == 0
    assert analytics.summary(c, fmt="all")["total_pl"] == 0                      # a tournament is not cash profit


def test_ending_records_place_and_prize_and_the_stake_filter_finds_it(client):
    start(client)
    hand(client)
    r = client.post("/api/session/end", json={"place": 5, "size": 36, "prize": 3}).get_json()
    assert r["recorded"] is True
    t = analytics.summary(server.db(), fmt="tournament")["tournaments"]
    assert t["count"] == 1 and t["entries"] == 2.0 and t["prizes"] == 3.0 and t["cashes"] == 1
    assert t["recent"][0]["stake"] == "real site" and t["recent"][0]["place"] == 5
    assert analytics.summary(server.db(), stake=0.02, fmt="tournament")["tournaments"]["count"] == 1
    assert analytics.summary(server.db(), stake=1.0, fmt="tournament")["tournaments"] is None


def test_a_tournament_the_casino_already_recorded_is_not_counted_twice(client):
    start(client)
    c = server.db()
    c.execute("INSERT INTO tournaments(ts,stake,place,size,buy_in,prize,hands,knockouts,level,final_table,heads_up,min_bb,table_moves,sharks,source,bb) "
              "VALUES(strftime('%s','now')+5,'0.01/0.02',3,36,200,300,60,2,5,1,0,8,2,4,'casino',0.02)")
    c.commit()
    r = client.post("/api/session/end", json={"place": 3, "size": 36, "prize": 3}).get_json()
    assert r["recorded"] is False
    assert server.db().execute("SELECT COUNT(*) FROM tournaments").fetchone()[0] == 1


def test_the_same_hand_in_the_casino_and_the_rta_is_one_row(client):
    start(client)                                                               # an RTA tournament session at the NL2 scale (entry 2)
    client.post("/api/trainer/settings", json={"mode": "tournament"})
    client.post("/api/tourney/register", json={"stake": "0.01/0.02"})
    d = client.post("/api/trainer/deal").get_json()
    while d.get("legal"):
        d = client.post("/api/trainer/act", json={"action": "check" if d["legal"]["can_check"] else "fold"}).get_json()
    c = server.db()
    first = c.execute("SELECT * FROM hands").fetchone()
    assert hand(client, hole=first["hole"].split(), board=first["board"].split(), result=-30, stack=2970, bb=30).status_code == 200
    rows = server.db().execute("SELECT source, practice FROM hands").fetchall()
    assert len(rows) == 1 and rows[0]["source"] == "both" and rows[0]["practice"] == 2
