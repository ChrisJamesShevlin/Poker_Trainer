"""The trainer's web API, run against a throw-away database (never the real poker.db)."""
import pytest

import server
from pokercalc import stakes, store


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(server, "db", lambda: store.connect(path))
    monkeypatch.setattr(server, "_table", None)
    monkeypatch.setattr(server, "_logged", 0)
    monkeypatch.setattr(server, "_practice", None)
    monkeypatch.setattr(server, "_tourney", None)
    monkeypatch.setattr(server, "_last_practice_hand", 0.0)
    monkeypatch.setattr(server, "_mode", None)
    monkeypatch.setattr(server, "allowed_stakes", lambda t: set(stakes.STAKES))      # these tests are not about the Grind lock or the bankroll rules
    monkeypatch.setattr(server, "locks", lambda t: {})                                # nor about which settings the Grinding mode fixes
    monkeypatch.setattr(server, "enforce_settings", lambda t: None)
    c = server.app.test_client()
    c.post("/api/trainer/settings", json={"speed": "instant"})                           # these tests are not about pacing
    server.trainer_table().bankroll = 10 ** 7                                             # a big account (it cannot be edited through the API)
    return c


def post(c, url, **body):
    return c.post(url, json=body)


def play_out(c):
    """Check or fold until the hand ends."""
    st = c.get("/api/trainer/state").get_json()
    while st["started"] and not st["over"]:
        st = post(c, "/api/trainer/act", action="check" if st["legal"]["can_check"] else "fold").get_json()
    return st


def deal_until_we_act(c):
    for _ in range(40):
        st = post(c, "/api/trainer/deal").get_json()
        if "legal" in st:
            return st
    raise AssertionError("never got to act")


def test_config_lists_the_stakes_like_a_lobby(client):
    cfg = client.get("/api/trainer/config").get_json()
    assert [s["blinds"] for s in cfg["stakes"]] == ["£0.01/£0.02", "£0.02/£0.05", "£0.05/£0.10", "£0.10/£0.25", "£0.25/£0.50", "£0.50/£1", "£1/£2", "£2/£5", "£5/£10",
                                                        "£10/£20", "£25/£50", "£50/£100", "£100/£200", "£200/£400", "£500/£1,000"]      # pounds by default
    assert cfg["stakes"][1]["buy_in"] == "£2 - £5" and 0 in cfg["timers"] and cfg["currencies"] == ["£", "$", "€"]
    assert "instant" in cfg["speeds"] and cfg["default_account"] == 6000
    post(client, "/api/trainer/settings", currency="$")                  # the symbol is just a display choice
    cfg = client.get("/api/trainer/config").get_json()
    assert [s["blinds"] for s in cfg["stakes"]] == ["$0.01/$0.02", "$0.02/$0.05", "$0.05/$0.10", "$0.10/$0.25", "$0.25/$0.50", "$0.50/$1", "$1/$2", "$2/$5", "$5/$10",
                                                        "$10/$20", "$25/$50", "$50/$100", "$100/$200", "$200/$400", "$500/$1,000"]
    assert cfg["stakes"][1]["buy_in"] == "$2 - $5"


def test_deal_act_and_history(client):
    st = deal_until_we_act(client)
    assert len(st["seats"][0]["hole"]) == 2 and st["legal"]["to_call"] >= 0
    end = play_out(client)
    assert end["over"] and end["last"]["hand_no"] == end["hand_no"]
    rows = server.db().execute("SELECT * FROM trainer_hands").fetchall()
    assert len(rows) == 1 and rows[0]["hand_no"] == end["hand_no"]


def test_settings_save_and_survive_a_restart(client, monkeypatch):
    r = post(client, "/api/trainer/settings", stake="0.50/1", buy_in=8000, timer_s=20)
    assert r.status_code == 200
    st = post(client, "/api/trainer/settings", hero_stack=15000).get_json()
    assert (st["stake"], st["bb"], st["buy_in"], st["timer_s"], st["hero_stack"]) == ("0.50/1", 100, 8000, 20, 15000)
    monkeypatch.setattr(server, "_table", None)                     # as if the server had been restarted
    again = client.get("/api/trainer/state").get_json()
    assert (again["stake"], again["buy_in"], again["timer_s"], again["hero_stack"]) == ("0.50/1", 8000, 20, 15000)


def test_bad_settings_are_refused_with_a_message(client):
    r = post(client, "/api/trainer/settings", stake="0.50/1", buy_in=100)
    assert r.status_code == 400 and "buy-in" in r.get_json()["error"]
    assert post(client, "/api/trainer/settings", timer_s=999).status_code == 400
    deal_until_we_act(client)
    assert post(client, "/api/trainer/settings", stake="2/5").status_code == 400      # not mid-hand
    assert post(client, "/api/trainer/deal").status_code == 400


def test_out_of_chips_needs_a_rebuy(client):
    client.get("/api/trainer/state")
    server._table.hero_stack = 0
    r = post(client, "/api/trainer/deal")
    assert r.status_code == 409 and r.get_json()["needs_rebuy"] is True
    st = post(client, "/api/trainer/rebuy").get_json()
    assert st["hero_stack"] == st["buy_in"]


def test_illegal_moves_are_refused(client):
    st = deal_until_we_act(client)
    assert post(client, "/api/trainer/act", action="raise", amount=st["legal"]["max_raise_to"] + 1).status_code == 400
    assert post(client, "/api/trainer/act", action="bogus").status_code == 400


def test_advice_only_uses_your_own_cards(client):
    deal_until_we_act(client)
    a = post(client, "/api/trainer/advice").get_json()
    assert a["action"] in ("FOLD", "CALL", "RAISE", "CHECK", "BET") and 0 <= a["equity"] <= 1
    play_out(client)
    assert post(client, "/api/trainer/advice").status_code == 400      # no hand in progress


def test_shot_clock_is_enforced_when_the_page_polls(client):
    post(client, "/api/trainer/settings", timer_s=10)
    st = deal_until_we_act(client)
    assert st["seconds_left"] is not None and st["seconds_left"] <= 10
    server._table.deadline -= 60                                      # the clock ran out a minute ago
    st = client.get("/api/trainer/state").get_json()
    assert any(a[1] == 0 and a[2] in ("check", "fold") for a in server._table.hand.log)


def test_table_strength_setting_is_listed_saved_and_applied(client, monkeypatch):
    cfg = client.get("/api/trainer/config").get_json()
    assert [s["key"] for s in cfg["strengths"]] == ["very_loose", "loose", "balanced", "tough", "hard"]
    st = post(client, "/api/trainer/settings", stake="0.50/1", strength="hard").get_json()
    assert st["strength"] == "hard"
    monkeypatch.setattr(server, "_table", None)                     # restart: the strength is remembered
    assert client.get("/api/trainer/state").get_json()["strength"] == "hard"
    assert post(client, "/api/trainer/settings", strength="nonsense").status_code == 400
    deal_until_we_act(client)
    assert post(client, "/api/trainer/settings", strength="loose").status_code == 400      # not mid-hand


# ---- account, currency, game speed and the steps played back by the page
def test_replies_carry_the_steps_once(client):
    post(client, "/api/trainer/settings", speed="normal")
    st = post(client, "/api/trainer/deal").get_json()
    frames = st["frames"]
    assert frames and frames[0]["event"] == "deal" and "legal" not in frames[0]["state"]
    assert sum(f["delay"] for f in frames) > 0                           # the other players take a moment
    assert client.get("/api/trainer/state").get_json()["frames"] == []   # each step is delivered only once


def test_instant_speed_has_no_waits(client):
    post(client, "/api/trainer/settings", speed="instant")
    st = post(client, "/api/trainer/deal").get_json()
    assert sum(f["delay"] for f in st["frames"]) == 0 and st["speed"] == "instant"


def test_speed_and_currency_are_saved_and_validated(client, monkeypatch):
    post(client, "/api/trainer/settings", speed="slow", currency="€")
    monkeypatch.setattr(server, "_table", None)                           # restart
    st = client.get("/api/trainer/state").get_json()
    assert (st["speed"], st["currency"]) == ("slow", "€")
    assert post(client, "/api/trainer/settings", speed="warp").status_code == 400
    assert post(client, "/api/trainer/settings", currency="¥").status_code == 400


def test_account_cash_out_rebuy_and_unaffordable_stakes(client):
    server.trainer_table().set_account(10000)                            # (the API no longer lets you edit the account)
    st = client.get("/api/trainer/state").get_json()
    assert st["account"] == 10000 and st["bankroll"] == 10000 - st["hero_stack"]
    st = post(client, "/api/trainer/cashout").get_json()
    assert (st["hero_stack"], st["bankroll"]) == (0, 10000)
    r = post(client, "/api/trainer/deal")
    assert r.status_code == 409 and r.get_json()["needs_rebuy"] is True
    st = post(client, "/api/trainer/rebuy").get_json()
    assert st["hero_stack"] == st["buy_in"] and st["bankroll"] == 10000 - st["buy_in"]
    r = post(client, "/api/trainer/settings", stake="5/10")              # minimum buy-in is far above 100.00
    assert r.status_code == 400 and "account" in r.get_json()["error"]
    assert client.get("/api/trainer/state").get_json()["stake"] == "0.01/0.02"       # nothing changed
    r = post(client, "/api/trainer/settings", account=10 ** 9)                       # the account cannot be edited
    assert r.status_code == 403 and "can't be edited" in r.get_json()["error"]
    assert client.get("/api/trainer/state").get_json()["account"] == 10000


def test_out_of_money_is_explained(client):
    server.trainer_table().set_account(500)                              # exactly the buy-in: nothing off the table
    post(client, "/api/trainer/cashout")
    post(client, "/api/trainer/rebuy")                                   # sits down with all of it
    r = post(client, "/api/trainer/rebuy")
    assert r.status_code == 400 and "no money left" in r.get_json()["error"].lower() or "nothing to add" in r.get_json()["error"]
