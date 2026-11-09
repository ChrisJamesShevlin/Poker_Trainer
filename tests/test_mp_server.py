"""The multiplayer web side: game code, who may do what, and the gate that keeps a guest away from the rest of the app."""
import json
import socket
import urllib.error
import urllib.request

import pytest

import server
from pokercalc import mp_server


@pytest.fixture
def mp():
    mp_server.reset_for_tests()
    c = server.app.test_client()
    html = c.get("/multiplayer").get_data(as_text=True)
    host = {"X-MP-Token": mp_server.HOST_TOKEN}
    yield c, host
    mp_server.stop_sharing()
    mp_server.reset_for_tests()


def join(c, name="Sam", code=None):
    return c.post("/api/mp/join", json={"name": name, "code": code or mp_server._code})


def play_to_my_turn(c, headers, other_headers=None):
    """Deal if needed and play whoever's turn it is with check/fold until the viewer has to act or the hand is over."""
    st = c.get("/api/mp/state", headers=headers).get_json()
    return st


def test_the_pages_carry_the_right_role_and_only_the_host_page_has_the_token(mp):
    c, host = mp
    assert mp_server.HOST_TOKEN in c.get("/multiplayer").get_data(as_text=True)
    guest_page = c.get("/play").get_data(as_text=True)
    assert '"role": "guest"' in guest_page and mp_server.HOST_TOKEN not in guest_page


def test_a_wrong_code_is_refused_and_too_many_wrong_codes_lock_the_door_for_a_minute(mp):
    c, _ = mp
    assert join(c, code="NOPE00").status_code == 403
    for _ in range(mp_server.MAX_FAILS_PER_MINUTE):
        join(c, code="NOPE00")
    assert join(c).status_code == 429                                                   # even the right code waits
    mp_server._fails.clear()
    assert join(c).status_code == 200


def test_one_friend_at_a_time_and_a_returning_friend_gets_back_in(mp):
    c, host = mp
    assert join(c, "Sam").status_code == 200
    assert join(c, "Alex").status_code == 409                                           # the seat is taken
    again = join(c, "Sam")
    assert again.status_code == 200                                                     # the same name: a reconnect
    assert c.get("/api/mp/state", headers=host).get_json()["guest"] == "Sam"


def test_you_must_be_at_the_table_to_see_or_play_it(mp):
    c, host = mp
    assert c.get("/api/mp/state").status_code == 401
    assert c.get("/api/mp/state", headers={"X-MP-Token": "guess"}).status_code == 401
    assert c.post("/api/mp/deal").status_code == 401


def test_both_players_play_hands_and_only_see_their_own_cards(mp):
    c, host = mp
    tok = join(c).get_json()["token"]
    guest = {"X-MP-Token": tok}
    c.post("/api/mp/deal", headers=host)
    for _ in range(40):
        for headers in (host, guest):
            st = c.get("/api/mp/state", headers=headers).get_json()
            if st["started"] and not st["over"] and st["to_act"] == 0:
                assert st["seats"][0]["hole"] and all(s["hole"] is None for s in st["seats"][1:])
                legal = st["legal"]
                c.post("/api/mp/act", headers=headers, json={"action": "check" if legal["can_check"] else "fold"})
        if c.get("/api/mp/state", headers=host).get_json()["over"]:
            break
    assert c.get("/api/mp/state", headers=host).get_json()["over"] is True
    assert c.get("/api/mp/state", headers=guest).get_json()["over"] is True
    wrong_turn = c.post("/api/mp/act", headers=guest, json={"action": "fold"})
    assert wrong_turn.status_code == 400                                                # nobody is to act now


def test_a_polling_page_only_gets_changes(mp):
    c, host = mp
    st = c.get("/api/mp/state", headers=host).get_json()
    again = c.get(f"/api/mp/state?version={st['version']}", headers=host).get_json()
    assert again == {"unchanged": True, "version": st["version"]}
    c.post("/api/mp/deal", headers=host)
    assert "unchanged" not in c.get(f"/api/mp/state?version={st['version']}", headers=host).get_json()


def test_the_settings_are_the_hosts_and_cannot_change_mid_hand(mp):
    c, host = mp
    tok = join(c).get_json()["token"]
    guest = {"X-MP-Token": tok}
    assert c.post("/api/mp/settings", headers=guest, json={"stake": "0.50/1"}).status_code == 401
    assert c.post("/api/mp/settings", headers=host, json={"stake": "0.50/1", "buy_in": 4000}).get_json()["bb"] == 100
    assert c.post("/api/mp/settings", headers=host, json={"strength": "hard"}).get_json()["strength"] == "hard"
    c.post("/api/mp/deal", headers=host)
    assert c.post("/api/mp/settings", headers=host, json={"stake": "0.01/0.02"}).status_code == 400
    assert c.post("/api/mp/kick", headers=guest).status_code == 401                     # only the host removes people
    assert c.get("/api/mp/host", headers=guest).status_code == 401


def test_the_host_can_remove_the_friend(mp):
    c, host = mp
    join(c)
    assert c.post("/api/mp/kick", headers=host).status_code == 200
    assert c.get("/api/mp/host", headers=host).get_json()["guest"] is None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(url, method="GET", headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}, **({"Content-Type": "application/json"} if data else {})))
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_the_guest_listener_only_lets_through_what_the_table_needs(mp):
    c, host = mp
    port = free_port()
    with server.app.app_context():
        mp_server.start_sharing(ip="127.0.0.1", port=port, allow_local=True)
    base = f"http://127.0.0.1:{port}"
    assert call(base + "/play")[0] == 200 and call(base + "/rta")[0] == 200 and call(base + "/static/analytics.js")[0] == 200
    assert call(base + "/api/session") == (200, '{"active": null, "last_balance": null}')
    for path in ("/api/analytics", "/api/hand", "/api/backup", "/api/coach", "/api/history/clear", "/trainer", "/grind", "/multiplayer",
                 "/api/trainer/state", "/api/mp/host", "/api/mp/settings", "/api/mp/share", "/api/mp/kick", "/api/session/start"):
        assert call(base + path, method="POST" if path.startswith("/api/") and path not in ("/api/analytics",) else "GET", body={})[0] == 403, path
    assert call(base + "/api/mp/state", headers={"X-MP-Token": mp_server.HOST_TOKEN})[0] == 401      # the host's token means nothing out here
    code, body = call(base + "/api/mp/join", "POST", body={"name": "Sam", "code": mp_server._code})
    assert code == 200
    token = json.loads(body)["token"]
    assert call(base + "/api/mp/state", headers={"X-MP-Token": token})[0] == 200                       # a joined guest can see the table
    assert call(base + "/api/calc", "POST", body={"hole": ["Ah", "Kd"], "board": [], "pot": 0.03, "call": 0, "opps": 1, "range_mode": "auto",
                                                  "position": "BTN", "stack": 2, "bb": 0.02})[0] == 200          # and use the calculator
    mp_server.stop_sharing()


def test_a_request_from_outside_the_private_network_is_refused(mp):
    c, host = mp
    guard = mp_server.GuestGuard(server.app)
    seen = []
    out = guard({"REMOTE_ADDR": "8.8.8.8", "PATH_INFO": "/play", "REQUEST_METHOD": "GET"}, lambda status, headers: seen.append(status))
    assert seen == ["403 Forbidden"] and b"private network" in out[0]


def test_sharing_without_tailscale_says_so(mp, monkeypatch):
    c, host = mp
    monkeypatch.setattr(mp_server, "tailscale_ip", lambda: None)
    r = c.post("/api/mp/share", headers=host, json={"on": True})
    assert r.status_code == 400 and "Tailscale" in r.get_json()["error"]


def test_tailscale_addresses_are_recognised():
    assert mp_server._is_tailscale("100.101.102.103") and not mp_server._is_tailscale("192.168.1.5") and not mp_server._is_tailscale("8.8.8.8")


def test_the_guest_door_will_not_open_on_an_address_that_is_not_tailscale(mp):
    c, host = mp
    r = c.post("/api/mp/share", headers=host, json={"on": True, "ip": "192.168.1.20"})
    assert r.status_code == 400 and "Tailscale address" in r.get_json()["error"]
    assert c.post("/api/mp/share", headers=host, json={"on": True, "ip": "0.0.0.0"}).status_code == 400            # never "everywhere"


def test_the_host_sets_a_nickname_and_the_friend_sees_it(mp):
    c, host = mp
    tok = join(c, "Sam").get_json()["token"]
    guest = {"X-MP-Token": tok}
    assert c.post("/api/mp/settings", headers=host, json={"host_name": "Chris"}).get_json()["host_name"] == "Chris"
    assert c.get("/api/mp/state?force=1", headers=guest).get_json()["host_name"] == "Chris"
    assert c.post("/api/mp/settings", headers=guest, json={"host_name": "Hacker"}).status_code == 401      # only the host renames the host
