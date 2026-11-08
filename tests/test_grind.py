"""The Grind ladder (levels, clearing rules, badges) and how table difficulty scales with the stake."""
import json
import random

import pytest

import server
from pokercalc import grind, stakes, store
from pokercalc.table import Table

KEYS = list(stakes.STAKES)


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(server, "db", lambda: store.connect(path))
    monkeypatch.setattr(server, "_table", None)
    monkeypatch.setattr(server, "_logged", 0)
    monkeypatch.setattr(server, "_practice", None)
    monkeypatch.setattr(server, "_mode", None)
    monkeypatch.setattr(server, "_logged_practice", 0)
    monkeypatch.setattr(server, "_tourney", None)
    monkeypatch.setattr(server, "_last_practice_hand", 0.0)
    monkeypatch.setattr(server, "_logged_tourney", 0)
    return server.app.test_client()


def con():
    return store.connect(":memory:")


def add(c, stake, net_bb, n=1, strength="balanced", vpip=True, pfr=True, ts0=1_000_000.0, gap=1.0):
    """n hands at `stake`, each worth net_bb big blinds; the hero's preflop actions make VPIP/PFR as asked."""
    bb = stakes.STAKES[stake]["bb"]
    acts = []
    if vpip:
        acts.append(["preflop", 0, "raise" if pfr else "call", 3 * bb])
    start = c.execute("SELECT COALESCE(MAX(ts), ?) FROM trainer_hands", (ts0,)).fetchone()[0]
    for i in range(n):
        c.execute("INSERT INTO trainer_hands(ts,stake,hand_no,position,hole,board,pot,net,showdown,actions,strength) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (start + gap * (i + 1), stake, i, "BTN", "As Ks", "", 0, net_bb * bb, 0, json.dumps(acts), strength))
    c.commit()


def rta(c, stake, n=100, follow=1.0, ts=1_000_000.0):
    """n hands logged in the manual calculator at this stake, one decision each; `follow` of them match its advice."""
    sid = c.execute("INSERT INTO sessions(start_ts,balance_before,buy_in,bb) VALUES(?,?,?,?)",
                    (ts, 100.0, 2.0, stakes.STAKES[stake]["bb"] / 100)).lastrowid
    for i in range(n):
        d = [{"suggested": "CALL", "taken": "CALL" if i < n * follow else "FOLD"}]
        c.execute("INSERT INTO hands(session_id,ts,hole,board,pot,call,opps,range_pct,position,stack,equity,required,suggested,taken,result,decisions) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid, ts + i, "As Ks", "", 1, 1, 1, 0, "BTN", 10, 0.5, 0.3, "CALL", "CALL", 0, json.dumps(d)))
    c.commit()


def test_ladder_runs_from_nl2_to_nl100000_and_has_one_rule_per_level():
    assert KEYS[0] == "0.01/0.02" and KEYS[-1] == "500/1000"
    assert set(grind.RULES) == set(KEYS) and len({r["badge"] for r in grind.RULES.values()}) == len(KEYS)
    assert grind.level_name("0.01/0.02") == "NL2" and grind.level_name("500/1000") == "NL100,000"
    g = grind.summary(con())
    assert [lv["status"] for lv in g["levels"]] == ["current"] + ["locked"] * (len(KEYS) - 1)
    assert g["cleared"] == 0 and g["total_hands"] == 0 and not any(b["earned"] for b in g["badges"])


def test_a_level_is_cleared_with_hands_win_rate_and_a_winners_style():
    c, first = con(), KEYS[0]
    rule = grind.RULES[first]
    add(c, first, 0.2, n=rule["hands"])                       # +20 bb/100, VPIP and PFR 100%: style is wrong
    assert grind.summary(c)["levels"][0]["status"] == "current"
    c2 = con()
    for i in range(rule["hands"]):                            # a sensible mix: 25% VPIP, half of those raises
        add(c2, first, 0.2, vpip=i % 4 == 0, pfr=i % 8 == 0)
    assert grind.summary(c2)["levels"][0]["status"] == "current"                 # the calculator was never used
    rta(c2, first)
    g = grind.summary(c2)
    assert g["levels"][0]["status"] == "cleared" and g["levels"][1]["status"] == "current" and g["cleared"] == 1
    assert 24 <= g["levels"][0]["vpip"] <= 26 and g["levels"][0]["pfr"] == pytest.approx(12.5, abs=0.5)
    by = {b["id"]: b for b in g["badges"]}
    assert by[f"level-{first}"]["earned"] and by["sat-down"]["earned"] and not by["climber"]["earned"]


def test_losing_or_too_few_hands_do_not_clear_a_level():
    first, rule = KEYS[0], grind.RULES[KEYS[0]]
    c = con()
    for i in range(rule["hands"] - 1):
        add(c, first, 0.5, vpip=i % 4 == 0, pfr=i % 8 == 0)
    assert grind.summary(c)["levels"][0]["status"] == "current"             # one hand short
    c = con()
    for i in range(rule["hands"] + 500):
        add(c, first, -0.1, vpip=i % 4 == 0, pfr=i % 8 == 0)
    lv = grind.summary(c)["levels"][0]
    assert lv["status"] == "current" and lv["bb100"] < 0 and lv["checks"]["winrate"] is False


def test_only_hands_at_the_current_level_and_hard_enough_tables_count():
    c = con()
    add(c, KEYS[3], 0.5, n=50)                                  # a locked level: ignored
    g = grind.summary(c)
    assert g["levels"][3]["hands"] == 0 and g["off_ladder"] == 50 and g["total_hands"] == 50
    # jump a player straight to a level that needs hard tables: balanced-table hands are not counted there
    need_hard = next(k for k in KEYS if grind.RULES[k]["min_strength"] == "hard")
    assert grind.RULES[KEYS[0]]["min_strength"] == "very_loose" and grind.RULES[KEYS[-1]]["min_strength"] == "hard"
    rule = grind.RULES[need_hard]
    assert grind.STRENGTH_RANK["balanced"] < grind.STRENGTH_RANK[rule["min_strength"]]


def test_hands_with_no_recorded_strength_count_as_balanced():
    c, first = con(), KEYS[0]
    add(c, first, 0.1, n=10, strength=None)
    assert grind.summary(c)["levels"][0]["hands"] == 10


def test_checks_only_happen_every_so_many_hands():
    first, rule = KEYS[0], grind.RULES[KEYS[0]]
    c = con()
    n = rule["hands"] + 1                                        # past the minimum but not on a check point
    for i in range(n):
        add(c, first, 0.2, vpip=i % 4 == 0, pfr=i % 8 == 0)
    rta(c, first)
    assert n % grind.CHECK_EVERY != 0
    assert grind.summary(c)["levels"][0]["status"] == "cleared"      # the check at the minimum (a multiple of 250) passed


def test_sessions_and_milestone_badges():
    c, first = con(), KEYS[0]
    add(c, first, 1.0, n=15, gap=4000.0)                         # every hand 4000 s after the last: each is its own session
    g = grind.summary(c)
    assert g["sessions"] == 15
    assert {b["id"]: b["earned"] for b in g["badges"]}["hot-streak"]
    add(c, first, 150, n=1)
    assert {b["id"]: b["earned"] for b in grind.summary(c)["badges"]}["big-100"]


def test_tables_get_tougher_as_the_stake_rises():
    rng = random.Random(5)
    mixes = [stakes.table_mix(k) for k in KEYS]
    sharks = [sum(m[0]) / 2 for m in mixes]
    assert all(a < b for a, b in zip(sharks, sharks[1:]))                              # a balanced table has more sharks at every level
    assert all(a >= b for a, b in zip([m[2] for m in mixes], [m[2] for m in mixes][1:]))   # and the weak players loosen up less
    assert sharks[0] == 1.25 and sharks[-1] == 4.1
    toughness = [v["tough"] for v in stakes.STAKES.values()]
    assert toughness == sorted(toughness) and toughness[0] == 0 and toughness[-1] == 1

    def avg_sharks(stake, strength="balanced", n=600):
        return sum(sum(b["style"] == "reg" for b in stakes.population(stake, rng, strength=strength)) for _ in range(n)) / n
    assert avg_sharks(KEYS[0]) == pytest.approx(1.25, abs=0.15) and avg_sharks(KEYS[-1]) == pytest.approx(4.1, abs=0.15)
    for k in KEYS:                                                # softer strengths never have more sharks, at every level
        got = [avg_sharks(k, name) for name in stakes.STRENGTHS]
        assert all(a <= b + 0.1 for a, b in zip(got, got[1:]))
    assert avg_sharks(KEYS[0], "very_loose") == pytest.approx(stakes.MIN_SHARK, abs=0.1)                 # the softest table still has the odd regular
    assert avg_sharks(KEYS[-1], "hard") == pytest.approx(5 - stakes.MIN_WEAK, abs=0.15)                  # and the top the odd weaker player


def test_the_mix_shown_on_the_grinding_page_is_the_mix_that_is_dealt_and_has_the_right_shape():
    rng = random.Random(8)
    for k in KEYS:
        shown, level = grind.table_mix_text(k), grind.RULES[k]["min_strength"]
        n, got = 1500, {"shark": 0, "rec": 0, "fish": 0, "whale": 0}
        for _ in range(n):
            for b in stakes.population(k, rng, strength=level):
                got[stakes.player_type(b["style"])] += 1
        for t in got:
            assert got[t] / n == pytest.approx(shown[t], abs=0.12), (k, t)                    # page and table agree
        assert sum(shown.values()) == pytest.approx(5, abs=0.2)
    first, last = grind.table_mix_text(KEYS[0]), grind.table_mix_text(KEYS[-1])
    assert first["rec"] > first["fish"] > first["shark"]                                      # the lowest level: mostly recreational players, then callers
    assert first["shark"] <= 0.5 and first["whale"] <= 0.4                                    # the odd regular, and whales are rare
    assert all(grind.table_mix_text(k)["whale"] <= 0.4 for k in KEYS) and last["whale"] == 0
    shark_counts = [grind.table_mix_text(k)["shark"] for k in KEYS]
    assert shark_counts == sorted(shark_counts) and last["shark"] >= 4.5                      # sharks only ever go up, and take over at the top
    recs = [grind.table_mix_text(k)["rec"] for k in KEYS]
    assert max(recs) >= 2.4 and recs[-1] < 0.5                                                # recreational players are the biggest group low down, and fade out


def test_player_types_cover_every_bot_style():
    from pokercalc.bots import STYLES
    assert set(stakes.STYLE_TYPE) == set(STYLES) and set(stakes.STYLE_TYPE.values()) == set(stakes.PLAYER_TYPES)
    mix = grind.table_mix_text(KEYS[0]), grind.table_mix_text(KEYS[-1])
    assert mix[0]["fish"] > mix[1]["fish"] and mix[0]["whale"] > mix[1]["whale"] and mix[0]["shark"] < mix[1]["shark"]
    assert sum(mix[0].values()) == pytest.approx(5, abs=0.3)


def test_the_tiniest_stake_plays_a_hand_and_old_saved_strength_loads():
    t = Table("0.01/0.02", seed=2, bot_sims=10)
    assert (t.bb, t.buy_in) == (2, 200)
    t.start_hand()
    d = t.to_dict()
    d["strength"] = "regs"                                       # the name before it became "hard"
    assert Table.from_dict(d, seed=1, bot_sims=10).strength == "hard"


def test_grind_page_and_api(client):
    assert client.get("/grind").status_code == 200
    g = client.get("/api/grind").get_json()
    assert len(g["levels"]) == len(KEYS) and g["levels"][0]["status"] == "current" and g["levels"][0]["blinds"] == "£0.01/£0.02"
    assert g["levels"][0]["mix"]["fish"] > 0 and g["levels"][0]["bankroll"]


# ---------------------------------------------------------------- personas and achievements
def test_every_player_has_a_unique_nickname_and_there_are_plenty():
    from pokercalc import personas
    assert all(len(p) >= 20 for p in personas.POOLS.values()) and set(personas.POOLS) == set(stakes.STYLE_TYPE)
    names = [n for p in personas.POOLS.values() for n, _ in p]
    assert len(names) == len(set(names)) >= 120
    rng = random.Random(3)
    for k in KEYS:
        table = stakes.population(k, rng)
        got = [b["persona"]["name"] for b in table]
        assert len(set(got)) == 5 and all(b["persona"]["blurb"] for b in table)
    seen = {b["persona"]["name"] for _ in range(400) for b in stakes.population(KEYS[3], rng)}
    assert len(seen) > 60                                               # lots of different people, not the same few


def test_a_saved_table_without_nicknames_gets_them_and_keeps_them():
    t = Table("0.02/0.05", seed=2, bot_sims=10)
    d = t.to_dict()
    for b in d["bots"]:
        b.pop("persona")                                                # a table saved before nicknames existed
    back = Table.from_dict(d, seed=1, bot_sims=10)
    names = [b["persona"]["name"] for b in back.bots]
    assert len(set(names)) == 5 and [b["persona"] for b in Table.from_dict(back.to_dict(), seed=1, bot_sims=10).bots] == [b["persona"] for b in back.bots]


def test_seats_show_a_nickname_but_the_level_stays_hidden_until_you_cash_out():
    t = Table("0.02/0.05", seed=4, bot_sims=10)
    t.start_hand()
    villains = lambda st: [s for s in st["seats"] if s["seat"] != 0]
    st = t.state()
    assert all(s["name"] and s["type"] is None and s["style"] is None and s["blurb"] is None for s in villains(st))
    assert st["seats"][0]["name"] is None and st["revealed"] is False and st["bots"] == ["?"] * 5
    while not t.hand.over:
        t.hero_act("fold" if t.hand.legal(0)["to_call"] else "check")
    st = t.state()
    assert all(s["type"] is None and s["blurb"] is None for s in villains(st))                         # still hidden after the hand
    assert "?" in st["bots"]
    t.cash_out()
    st = t.state()
    assert st["revealed"] is True
    assert [s["type"] for s in villains(st)] == [stakes.player_type(b["style"]) for b in t.bots]        # the icon matches how they really play
    assert all(s["blurb"] for s in villains(st)) and st["bots"] == [b["style"] for b in t.bots]


def test_sitting_back_down_after_cashing_out_is_a_new_table_with_the_level_hidden_again():
    t = Table("0.02/0.05", seed=4, bot_sims=10)
    t.cash_out()
    seen = [b["persona"]["name"] for b in t.bots]
    assert t.state()["revealed"] is True
    t.rebuy()
    assert t.revealed is False and all(b["persona"]["name"] for b in t.bots) and len(seen) == 5       # a new table of five
    st = t.start_hand()
    assert st["revealed"] is False and all(s["type"] is None for s in st["seats"])
    t2 = Table("0.02/0.05", seed=5, bot_sims=10)
    t2.cash_out()
    t2.set_hero_stack(t2.buy_in)                                                                       # another way of sitting back down
    assert t2.state()["revealed"] is False
    t3 = Table("0.02/0.05", seed=6, bot_sims=10)
    t3.cash_out()
    assert Table.from_dict(t3.to_dict(), seed=1, bot_sims=5).state()["revealed"] is True               # remembered between sessions


def test_changing_stake_or_starting_fresh_also_hides_the_new_players():
    t = Table("0.02/0.05", seed=4, bot_sims=10)
    t.cash_out()
    t.set_stake("0.05/0.10")
    assert t.state()["revealed"] is False
    t.cash_out()
    t.reset()
    assert t.state()["revealed"] is False


def test_bluffs_are_won_pots_with_nothing_and_a_bet_after_the_flop():
    bet = json.dumps([["preflop", 0, "raise", 30], ["flop", 0, "raise", 40]])
    assert grind.is_bluff("7h 2c", "As Kd 9s", bet, 0, 100)                       # air, bet, everyone folded
    assert not grind.is_bluff("7h 2c", "As Kd 9s", bet, 1, 100)                   # went to showdown: not a bluff
    assert not grind.is_bluff("7h 2c", "As Kd 9s", bet, 0, -50)                   # did not win
    assert not grind.is_bluff("Ah 2c", "As Kd 9s", bet, 0, 100)                   # top pair is a real hand
    assert not grind.is_bluff("7h 7c", "As Kd 9s", bet, 0, 100)                   # pocket pair too
    assert grind.is_bluff("7h 2c", "As Ad 9s", bet, 0, 100)                       # the pair is on the board, not yours
    assert not grind.is_bluff("7h 2c", "As Kd 9s", json.dumps([["preflop", 0, "raise", 30]]), 0, 100)   # no bet after the flop
    assert not grind.is_bluff("7h 2c", "", bet, 0, 100)                           # won before the flop


def test_bluff_pot_and_showdown_achievements_count_up():
    c, first = con(), KEYS[0]
    bet = json.dumps([["flop", 0, "raise", 40]])
    for i in range(10):
        c.execute("INSERT INTO trainer_hands(ts,stake,hand_no,position,hole,board,pot,net,showdown,actions,strength) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (1_000_000 + i, first, i, "BTN", "7h 2c", "As Kd 9s", 10, 10, 0, bet, "balanced"))
    c.commit()
    by = {b["id"]: b for b in grind.summary(c)["badges"]}
    assert by["bluffs-1"]["earned"] and by["bluffs-5"]["earned"] and by["bluffs-10"]["earned"] and not by["bluffs-25"]["earned"]
    assert by["bluffs-25"]["progress"] == 10 and by["bluffs-25"]["goal"] == 25
    assert by["pots-1"]["earned"] and by["pots-10"]["earned"] and not by["pots-100"]["earned"]
    assert not by["showdown-1"]["earned"]


def meta_row(c, stake, meta, net=100, showdown=1):
    c.execute("INSERT INTO trainer_hands(ts,stake,hand_no,position,hole,board,pot,net,showdown,actions,strength,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (2_000_000 + c.execute("SELECT COUNT(*) FROM trainer_hands").fetchone()[0], stake, 0, "BTN", "As Ah", "Ac Kd 2s 3h 4d", 10, net, showdown, "[]", "balanced", json.dumps(meta)))
    c.commit()


def test_all_in_wins_count_against_the_type_and_the_named_shark():
    from pokercalc import personas
    c, first = con(), KEYS[0]
    ivan, prof = personas.SHARKS[0], personas.SHARKS[1]                    # two of the sharks, whatever they are called
    whale = personas.POOLS["whale"][0][0]
    meta_row(c, first, {"hero_allin": True, "showdown": True, "hero_hand": "Flush, Ace high", "vs": [{"name": ivan, "type": "shark", "allin": True}]})
    meta_row(c, first, {"hero_allin": False, "showdown": True, "hero_hand": "Pair, Aces", "vs": [{"name": prof, "type": "shark", "allin": False}]})
    meta_row(c, first, {"hero_allin": True, "showdown": True, "hero_hand": None, "vs": [{"name": whale, "type": "whale", "allin": True}]}, net=-100)
    by = {b["id"]: b["earned"] for b in grind.summary(c)["badges"]}
    assert by["allin-1"] and by["allin-shark"] and not by["allin-whale"] and not by["allin-fish"]    # the whale hand was lost
    assert by[grind.shark_badge_id(ivan)] and not by[grind.shark_badge_id(prof)]                  # no all-in against the second one
    assert by["hand-flush"] and not by["hand-quads"] and by["showdown-1"]
    assert sum(1 for k in by if k.startswith("shark-")) == len(personas.SHARKS) >= 20


def test_a_real_hand_is_saved_with_who_you_played_and_whether_you_were_all_in(client):
    t = server.trainer_table()
    t.speed = "instant"
    t.start_hand()
    while not t.hand.over:
        t.hero_act(("raise", t.hand.legal(0)["max_raise_to"]) if t.hand.legal(0)["can_raise"] else "call")
    server.trainer_save()
    row = server.db().execute("SELECT meta, strength FROM trainer_hands").fetchone()
    m = json.loads(row["meta"])
    assert row["strength"] == server.grind_table().strength == "very_loose" and set(m) == {"hero_allin", "showdown", "hero_hand", "vs"}
    assert m["hero_allin"] is True                                     # we shoved every time we could
    assert m["showdown"] == bool(t.hand.result["showdown"]) and len(m["vs"]) == (len(t.hand.result["hands"]) - 1 if m["showdown"] else 0)
    for v in m["vs"]:
        assert v["name"] and v["type"] in stakes.PLAYER_TYPES


# ---------------------------------------------------------------- the lobby follows the ladder
def test_only_cleared_levels_and_the_current_one_can_be_sat_at(client):
    cfg = client.get("/api/trainer/config").get_json()
    assert [r["locked"] for r in cfg["stakes"]] == [False] + [True] * (len(KEYS) - 1)
    assert cfg["stakes"][1]["lock_text"] == "NL5 is locked: clear NL2 on the Grinding page first."
    r = client.post("/api/trainer/settings", json={"stake": "0.50/1"})
    assert r.status_code == 403 and "NL100 is locked" in r.get_json()["error"]
    assert client.get("/api/trainer/state").get_json()["stake"] == KEYS[0]              # still seated where you were
    assert client.post("/api/trainer/settings", json={"stake": "0.01/0.02"}).status_code == 200


def test_clearing_a_level_opens_the_next(client):
    first, rule = KEYS[0], grind.RULES[KEYS[0]]
    server.trainer_table().set_account(10 ** 6)                  # plenty of money, so only the ladder decides
    clear_first_level(server.db())
    cfg = client.get("/api/trainer/config").get_json()
    assert [r["locked"] for r in cfg["stakes"]][:3] == [False, False, True]
    assert client.post("/api/trainer/settings", json={"stake": KEYS[1]}).status_code == 200
    assert client.post("/api/trainer/settings", json={"stake": KEYS[2]}).status_code == 403


# ---------------------------------------------------------------- the account: fixed start, move up, move down, bust
def clear_first_level(c):
    """Enough winning hands at the first level to clear it (one transaction, so it is quick on a real file)."""
    bb = stakes.STAKES[KEYS[0]]["bb"]
    rows = []
    for i in range(grind.RULES[KEYS[0]]["hands"]):
        acts = [["preflop", 0, "raise" if i % 8 == 0 else "call", 3 * bb]] if i % 4 == 0 else []
        rows.append((1_000_000.0 + i, KEYS[0], i, "BTN", "As Ks", "", 0, 0.5 * bb, 0, json.dumps(acts), "balanced"))
    c.executemany("INSERT INTO trainer_hands(ts,stake,hand_no,position,hole,board,pot,net,showdown,actions,strength) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    rta(c, KEYS[0])


def test_the_account_starts_fixed_and_cannot_be_edited(client):
    st = client.get("/api/trainer/state").get_json()
    assert st["account"] == stakes.starter_balance() == 30 * 200 == 6000 and st["reloads"] == 0     # 30 buy-ins of NL2
    r = client.post("/api/trainer/settings", json={"account": 10 ** 9})
    assert r.status_code == 403 and client.get("/api/trainer/state").get_json()["account"] == 6000


def test_moving_up_needs_the_level_cleared_and_30_buy_ins(client):
    clear_first_level(server.db())
    rows = {r["key"]: r for r in client.get("/api/trainer/config").get_json()["stakes"]}
    assert rows[KEYS[1]]["locked"] and rows[KEYS[1]]["lock_text"] == "NL5 needs £150 in your account (30 buy-ins): you have £60."
    assert client.post("/api/trainer/settings", json={"stake": KEYS[1]}).status_code == 403
    server.trainer_table().set_account(15000)                    # 30 buy-ins of NL5 (a £5 buy-in is 500)
    assert client.post("/api/trainer/settings", json={"stake": KEYS[1]}).status_code == 200
    assert client.get("/api/trainer/state").get_json()["stake"] == KEYS[1]
    r = client.post("/api/trainer/settings", json={"stake": KEYS[2]})                   # NL10 is still locked on the ladder
    assert r.status_code == 403 and "clear NL5" in r.get_json()["error"]


def test_falling_below_20_buy_ins_moves_you_down_at_the_next_deal(client):
    clear_first_level(server.db())
    t = server.trainer_table()
    t.set_account(15000)
    client.post("/api/trainer/settings", json={"stake": KEYS[1]})
    t.set_account(9000)                                          # below 20 buy-ins of NL5 (10,000)
    r = client.post("/api/trainer/deal")
    d = r.get_json()
    assert r.status_code == 200 and d["stake"] == KEYS[0] and "Moved down from NL5 to NL2" in d["notice"]


def test_staying_needs_only_20_buy_ins(client):
    clear_first_level(server.db())
    t = server.trainer_table()
    t.set_account(15000)
    client.post("/api/trainer/settings", json={"stake": KEYS[1]})
    t.set_account(10000)                                         # exactly 20 buy-ins: you may stay
    d = client.post("/api/trainer/deal").get_json()
    assert d["stake"] == KEYS[1] and d.get("notice") is None


def test_going_bust_gives_a_free_starter_balance_at_level_one(client):
    t = server.trainer_table()
    t.cash_out()
    t.set_account(50)                                            # 0.50: below the NL2 minimum buy-in (0.80)
    d = client.post("/api/trainer/deal").get_json()
    assert "went bust" in d["notice"] and d["reloads"] == 1 and d["stake"] == KEYS[0]
    assert d["account"] == stakes.starter_balance() and d["hero_stack"] > 0


def test_the_minimum_buy_in_at_level_one_is_not_bust(client):
    t = server.trainer_table()
    t.cash_out()
    t.set_account(stakes.bust_line())                            # exactly 0.80: enough to buy in at NL2
    assert stakes.bust_line() == 80
    d = client.post("/api/trainer/deal").get_json()
    assert d.get("notice") is None and d["reloads"] == 0 and d["account"] == 80


def test_busted_in_the_middle_of_a_climb_goes_back_to_level_one(client):
    clear_first_level(server.db())
    t = server.trainer_table()
    t.set_account(15000)
    client.post("/api/trainer/settings", json={"stake": KEYS[1]})
    t.cash_out()
    t.set_account(30)
    d = client.post("/api/trainer/deal").get_json()
    assert d["stake"] == KEYS[0] and "went bust" in d["notice"] and d["account"] == 6000


# ---------------------------------------------------------------- the linked calculator and the page links
def test_only_the_trainer_and_the_calculator_open_in_new_tabs():
    import re
    from pathlib import Path
    for page in ("index.html", "trainer.html", "grind.html"):
        html = (Path(server.app.static_folder) / page).read_text()
        for href, tab in re.findall(r'<a [^>]*?href="([^"]+)"([^>]*)>', html):
            new = 'target="_blank"' in tab
            assert new == (href in ("/trainer", "/")), (page, href, tab)         # Grind and Analytics stay in the window


# ---------------------------------------------------------------- the manual calculator feeds the ladder
def played_level_one(c, **rta_kw):
    for i in range(grind.RULES[KEYS[0]]["hands"]):
        add(c, KEYS[0], 0.2, vpip=i % 4 == 0, pfr=i % 8 == 0)
    if rta_kw is not None:
        rta(c, KEYS[0], **rta_kw)


def test_clearing_needs_enough_calculator_decisions_and_following_them():
    first = KEYS[0]
    c = con()
    played_level_one(c, n=grind.RTA_MIN_DECISIONS - 1)                          # one decision short
    lv = grind.summary(c)["levels"][0]
    assert lv["status"] == "current" and lv["checks"]["rta_n"] is False and lv["checks"]["rta_follow"] is True
    assert lv["rta_decisions"] == 99 and lv["need_rta"] == 100
    c = con()
    played_level_one(c, n=200, follow=0.5)                                      # followed only half the time
    lv = grind.summary(c)["levels"][0]
    assert lv["status"] == "current" and lv["rta_follow"] == 50.0 and lv["checks"]["rta_follow"] is False
    c = con()
    played_level_one(c, n=200, follow=0.7)                                      # exactly 70%: enough
    assert grind.summary(c)["levels"][0]["status"] == "cleared"


def test_calculator_logs_count_only_at_the_matching_blinds_and_after_the_level_opens():
    first, second = KEYS[0], KEYS[1]
    c = con()
    played_level_one(c, n=100)
    g = grind.summary(c)
    assert g["levels"][0]["status"] == "cleared" and g["levels"][1]["status"] == "current"
    assert g["levels"][1]["rta_decisions"] == 0                                 # NL2 decisions do not count at NL5
    rta(c, second, n=30, ts=0.0)                                                # logged before NL5 opened: ignored
    assert grind.summary(c)["levels"][1]["rta_decisions"] == 0
    rta(c, second, n=30, ts=9_000_000.0)
    assert grind.summary(c)["levels"][1]["rta_decisions"] == 30
    assert grind.stake_for_bb(0.02) == "0.01/0.02" and grind.stake_for_bb(0.05) == "0.02/0.05" and grind.stake_for_bb(0.123) is None and grind.stake_for_bb(None) is None


def test_calculator_achievements():
    c = con()
    by = {b["id"]: b for b in grind.summary(c)["badges"]}
    assert not by["rta-1"]["earned"] and not by["disciplined"]["earned"]
    rta(c, KEYS[0], n=250, follow=0.9)
    g = grind.summary(c)
    by = {b["id"]: b for b in g["badges"]}
    assert g["rta"] == {"hands": 250, "decisions": 250, "followed": 225}
    assert by["rta-1"]["earned"] and by["rta-100"]["earned"] and not by["rta-500"]["earned"] and by["rta-500"]["progress"] == 250
    assert by["follow-50"]["earned"] and by["follow-250"]["progress"] == 225 and not by["follow-250"]["earned"]
    assert by["disciplined"]["earned"]
    c = con()
    rta(c, KEYS[0], n=250, follow=0.6)
    assert not {b["id"]: b for b in grind.summary(c)["badges"]}["disciplined"]["earned"]


def test_there_is_no_linked_calculator_any_more(client):
    assert client.get("/linked").status_code == 404 and client.get("/static/linked.js").status_code == 404


# ---------------------------------------------------------------- pop-ups and NEW tags
def test_new_badges_and_levels_pop_up_once_and_are_tagged_new_until_the_page_is_viewed():
    c = con()
    assert grind.check_new(c) == []                                   # the first look only records what you already have
    add(c, KEYS[0], 1.0, n=1)                                         # a first pot
    got = grind.check_new(c)
    assert {n["id"] for n in got} == {"sat-down", "pots-1"} and all("chip:" in n["title"] and n["chip_text"] == "$1" for n in got)
    assert grind.check_new(c) == []                                   # told only once
    assert set(grind.take_pending(c)) == {"sat-down", "pots-1"}       # waiting to be tagged NEW
    assert grind.take_pending(c) == []                                # and a refresh clears the tags


def test_clearing_a_level_pops_up_the_level_and_names_the_next_one():
    c = con()
    grind.check_new(c)
    for i in range(grind.RULES[KEYS[0]]["hands"]):
        add(c, KEYS[0], 0.2, vpip=i % 4 == 0, pfr=i % 8 == 0)
    rta(c, KEYS[0])
    level = [n for n in grind.check_new(c) if n["kind"] == "level"]
    assert len(level) == 1 and level[0]["title"] == "Level cleared: NL2"
    assert "NL5 is now open" in level[0]["text"] and "$150" in level[0]["text"]


def test_the_pages_get_the_pop_ups_and_the_grinding_page_tags_what_is_new(client):
    c = server.db()
    client.get("/api/grind")                                          # (record what exists)
    grind.check_new(c)
    add(c, KEYS[0], 1.0, n=1)
    client.post("/api/session/start", json=dict(balance=60, buy_in=2, bb=0.02))        # the RTA page's session, at NL2 blinds
    r = client.post("/api/hand", json=dict(hole=["As", "Ks"], board=[], pot=1, call=1, opps=1, equity=0.5, required=0.3,
                                           suggested="CALL", taken="CALL", result=None, decisions=[{"suggested": "CALL", "taken": "CALL"}]))
    ids = {n["id"] for n in r.get_json()["notices"]}
    assert {"pots-1", "rta-1"} <= ids                                 # the RTA page is told about the badges it just earned
    first = client.get("/api/grind").get_json()
    assert {b["id"] for b in first["badges"] if b["new"]} >= {"pots-1", "rta-1"}
    second = client.get("/api/grind").get_json()
    assert not any(b["new"] for b in second["badges"])                # a refresh clears the NEW tags


def test_a_finished_casino_hand_carries_its_pop_ups(client):
    t = server.trainer_table()
    client.get("/api/grind")
    grind.check_new(server.db())
    client.post("/api/trainer/settings", json={"speed": "instant"})
    got = []
    for _ in range(6):
        d = client.post("/api/trainer/deal").get_json()
        while d.get("legal"):
            d = client.post("/api/trainer/act", json={"action": "fold" if d["legal"]["to_call"] else "check"}).get_json()
            got += d.get("notices", [])
        if got:
            break
    assert any(n["id"] == "sat-down" for n in got)


# ---------------------------------------------------------------- resetting Grinding progress
def test_reset_starts_the_ladder_again_but_deletes_nothing_and_leaves_analytics_alone(client):
    from pokercalc import analytics
    c = server.db()
    server.trainer_table().set_account(15000)
    clear_first_level(c)
    rta(c, KEYS[0], n=120)
    c.execute("INSERT INTO sessions(start_ts,end_ts,balance_before,buy_in,bb,balance_after) VALUES(1,2,100,10,0.02,130)")
    c.commit()
    grind.check_new(c)
    before_an = json.dumps(analytics.summary(c), sort_keys=True, default=str)
    counts = lambda: tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("trainer_hands", "hands", "sessions"))
    n_before = counts()
    assert grind.summary(c)["cleared"] == 1 and any(b["earned"] for b in grind.summary(c)["badges"])
    r = client.post("/api/grind/reset")
    assert r.status_code == 200
    g = grind.summary(c)
    assert g["cleared"] == 0 and g["total_hands"] == 0 and g["levels"][0]["status"] == "current" and g["levels"][0]["hands"] == 0
    assert g["levels"][0]["rta_decisions"] == 0 and not any(b["earned"] for b in g["badges"])
    assert counts() == n_before                                                        # nothing was deleted
    assert json.dumps(analytics.summary(c), sort_keys=True, default=str) == before_an     # Analytics is exactly as it was
    st = client.get("/api/trainer/state").get_json()
    assert st["account"] == stakes.starter_balance() and st["stake"] == KEYS[0] and st["reloads"] == 0
    assert grind.take_pending(c) == [] and grind.check_new(c) == []                    # no pop-ups or NEW tags for the old progress


def test_progress_after_a_reset_counts_again_from_zero(client):
    c = server.db()
    clear_first_level(c)
    client.post("/api/grind/reset")
    import time
    bb = stakes.STAKES[KEYS[0]]["bb"]
    for i in range(3):                                                                # new hands, after the reset
        c.execute("INSERT INTO trainer_hands(ts,stake,hand_no,position,hole,board,pot,net,showdown,actions,strength) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (time.time() + 10 + i, KEYS[0], i, "BTN", "As Ks", "", 0, bb, 0, "[]", "balanced"))
    c.commit()
    g = grind.summary(c)
    assert g["total_hands"] == 3 and g["levels"][0]["hands"] == 3 and g["levels"][0]["status"] == "current"
    assert [n["id"] for n in grind.check_new(c)] and {b["id"] for b in g["badges"] if b["earned"]} >= {"sat-down", "pots-1"}


def test_reset_is_refused_in_the_middle_of_a_hand(client):
    client.post("/api/trainer/settings", json={"speed": "instant"})
    client.post("/api/trainer/deal")
    t = server.trainer_table()
    if t.hand.over:
        return
    r = client.post("/api/grind/reset")
    assert r.status_code == 400 and "Finish the current hand" in r.get_json()["error"]
    assert grind.reset_time(server.db()) == 0.0


# ---------------------------------------------------------------- Practice and Grinding modes
def play_one_hand(client):
    d = client.post("/api/trainer/deal").get_json()
    while d.get("legal"):
        d = client.post("/api/trainer/act", json={"action": "fold" if d["legal"]["to_call"] else "check"}).get_json()
    return d


def test_practice_lets_you_choose_anything_and_nothing_is_locked(client):
    client.post("/api/trainer/settings", json={"mode": "practice"})
    cfg = client.get("/api/trainer/config").get_json()
    assert cfg["mode"] == "practice" and not any(r["locked"] for r in cfg["stakes"]) and cfg["locks"] == {}
    st = client.post("/api/trainer/settings", json={"stake": "500/1000", "buy_in": 4_000_000, "strength": "hard", "speed": "fast", "timer_s": 0}).get_json()
    assert (st["stake"], st["buy_in"], st["strength"], st["speed"], st["timer_s"]) == ("500/1000", 4_000_000, "hard", "fast", 0)
    assert client.post("/api/trainer/settings", json={"timer_s": 45, "hero_stack": 3_000_000}).status_code == 200
    assert client.post("/api/trainer/settings", json={"stake": "0.01/0.02", "buy_in": 100}).status_code == 200


def test_practice_has_its_own_account_and_never_touches_the_grinding_one(client):
    before = client.get("/api/trainer/state").get_json()
    client.post("/api/trainer/settings", json={"mode": "practice"})
    prac = client.get("/api/trainer/state").get_json()
    assert prac["mode"] == "practice" and prac["account"] == server.PRACTICE_ACCOUNT
    client.post("/api/trainer/settings", json={"stake": "5/10"})
    client.post("/api/trainer/settings", json={"mode": "grind"})
    after = client.get("/api/trainer/state").get_json()
    assert after["mode"] == "grind" and after["account"] == before["account"] == 6000 and after["stake"] == before["stake"] == KEYS[0]


def test_practice_hands_are_saved_but_never_count_towards_grinding(client):
    server.practice_table().speed = "instant"
    client.post("/api/trainer/settings", json={"mode": "practice"})
    grind.check_new(server.db())
    for _ in range(3):
        d = play_one_hand(client)
        assert not d.get("notices")                                              # no pop-ups from Practice
    c = server.db()
    assert c.execute("SELECT COUNT(*), SUM(practice) FROM trainer_hands").fetchone()[:] == (3, 3)      # saved, and flagged
    g = grind.summary(c)
    assert g["total_hands"] == 0 and g["levels"][0]["hands"] == 0 and not any(b["earned"] for b in g["badges"])
    client.post("/api/trainer/settings", json={"mode": "grind"})
    server.grind_table().speed = "instant"
    play_one_hand(client)
    assert grind.summary(c)["total_hands"] == 1                                  # a Grinding hand does count
    assert c.execute("SELECT COUNT(*) FROM trainer_hands WHERE practice = 0").fetchone()[0] == 1


def test_grinding_fixes_the_settings_and_leaves_only_the_early_shot_clock_to_you(client):
    st = client.get("/api/trainer/state").get_json()
    assert st["mode"] == "grind" and st["locks"] == {"strength": True, "speed": True, "buy_in": True, "timer": False}      # NL2
    for body, word in (({"strength": "hard"}, "strength"), ({"speed": "instant"}, "speed"), ({"buy_in": 100}, "buy-in"), ({"hero_stack": 100}, "stack")):
        r = client.post("/api/trainer/settings", json=body)
        assert r.status_code == 403 and word in r.get_json()["error"].lower()
    assert client.post("/api/trainer/settings", json={"timer_s": 0}).status_code == 200          # NL2: the shot clock is yours
    assert client.post("/api/trainer/settings", json={"timer_s": 45}).get_json()["timer_s"] == 45
    assert client.post("/api/trainer/settings", json={"currency": "$"}).status_code == 200


def test_from_the_third_level_up_it_plays_like_online(client, monkeypatch):
    monkeypatch.setattr(server, "allowed_stakes", lambda t: set(KEYS))        # (pretend the levels above are unlocked)
    t = server.grind_table()
    t.set_account(10 ** 7)
    t.set_stake(KEYS[2])                                         # NL10
    t.set_speed("fast"), t.set_timer(30), t.set_strength("hard")
    assert client.get("/api/trainer/state").get_json()["locks"]["timer"] is True
    r = client.post("/api/trainer/settings", json={"timer_s": 0})
    assert r.status_code == 403 and "10 seconds preflop and 15 on each street" in r.get_json()["error"]
    d = client.post("/api/trainer/deal").get_json()                         # the table is set up for the level before the hand
    assert (d["speed"], d["online_clock"], d["strength"]) == ("normal", True, grind.RULES[KEYS[2]]["min_strength"])
    assert d["timer_s"] == 10 and d["online_clock_s"] == {"preflop": 10, "postflop": 15}                # the clock for a preflop decision
    assert "plays like an online table" in d["notice"]


def test_sitting_at_a_level_uses_the_levels_strength_and_a_full_buy_in(client):
    clear_first_level(server.db())
    server.grind_table().set_account(10 ** 6)
    r = client.post("/api/trainer/settings", json={"stake": KEYS[1], "buy_in": 100})          # a smaller buy-in is ignored
    d = r.get_json()
    assert r.status_code == 200 and d["buy_in"] == stakes.buy_in_range(KEYS[1])[1] and d["strength"] == grind.RULES[KEYS[1]]["min_strength"]
    assert client.post("/api/trainer/rebuy", json={"amount": 100}).status_code in (200, 400)          # the amount is not yours to pick


def test_the_mode_is_remembered_and_cannot_change_mid_hand(client):
    client.post("/api/trainer/settings", json={"mode": "practice"})
    assert server.store.kv_load(server.db(), "casino_mode") == "practice"
    server._mode = None                                          # a restart
    assert client.get("/api/trainer/state").get_json()["mode"] == "practice"
    client.post("/api/trainer/settings", json={"mode": "grind"})
    server.grind_table().speed = "instant"
    d = client.post("/api/trainer/deal").get_json()
    if d.get("legal"):
        r = client.post("/api/trainer/settings", json={"mode": "practice"})
        assert r.status_code == 400 and "finish the current hand" in r.get_json()["error"].lower()
        assert client.get("/api/trainer/state").get_json()["mode"] == "grind"


def test_rta_hands_logged_during_practice_count_in_analytics_but_not_for_grinding(client):
    from pokercalc import analytics
    client.post("/api/session/start", json=dict(balance=60, buy_in=2, bb=0.02))
    hand = dict(hole=["As", "Ks"], board=[], pot=1, call=1, opps=1, equity=0.5, required=0.3, suggested="CALL", taken="CALL",
                result=1, decisions=[{"street": 0, "suggested": "CALL", "taken": "CALL"}])
    client.post("/api/hand", json=hand)                                        # logged while Grinding
    client.post("/api/trainer/settings", json={"mode": "practice"})
    server.practice_table().speed = "instant"
    play_one_hand(client)                                                      # you really are playing Practice
    client.post("/api/hand", json=dict(hand, hole=["Qs", "Qd"]))               # logged while in Practice
    c = server.db()
    assert analytics.summary(c)["hands"] == 3                                  # Analytics sees both (and the Practice hand itself)
    g = grind.summary(c)
    assert g["rta"] == {"hands": 1, "decisions": 1, "followed": 1}             # Grinding only counts the first


def test_a_move_down_also_puts_the_lower_levels_settings_in_place(client):
    t = server.grind_table()
    t.set_account(10 ** 7)
    t.set_stake(KEYS[2])                                         # NL10 is locked on the ladder, so the deal moves you down
    t.set_speed("slow"), t.set_timer(0)
    d = client.post("/api/trainer/deal").get_json()
    assert d["stake"] == KEYS[0] and "Moved down" in d["notice"]
    assert d["speed"] == "normal" and d["strength"] == grind.RULES[KEYS[0]]["min_strength"]     # and the new level's settings are on


def test_the_online_clock_is_10_seconds_preflop_and_15_after_the_flop():
    now = [1000.0]
    for seed in range(1, 40):
        t = Table("0.01/0.02", seed=seed, bot_sims=5, speed="instant", clock=lambda: now[0])
        t.set_online_clock(True)
        st = t.start_hand()
        if not st.get("legal"):
            continue
        assert st["seconds_left"] == 10 and st["timer_s"] == 10                        # preflop
        st = t.hero_act("call" if st["legal"]["to_call"] else "check")
        if st.get("legal") and st["street"] != "preflop":
            assert st["seconds_left"] == 15 and st["timer_s"] == 15                    # after the flop
            now[0] += 15.5                                                             # the clock runs out
            assert t.check_clock() is True
            return
    raise AssertionError("never reached the flop with a decision to make")


def test_the_online_clock_is_saved_and_the_early_levels_keep_your_own_shot_clock(client):
    t = server.grind_table()
    t.set_online_clock(True)
    assert Table.from_dict(t.to_dict(), seed=1, bot_sims=5).online_clock is True
    d = client.post("/api/trainer/deal").get_json()                                   # NL2: the online clock is switched off again
    assert d["online_clock"] is False
    client.post("/api/trainer/settings", json={"timer_s": 30})
    assert client.get("/api/trainer/state").get_json()["timer_s"] == 30


def test_the_table_strength_each_level_locks_to_climbs_steadily_up_the_ladder():
    got = [grind.RULES[k]["min_strength"] for k in KEYS]
    assert list(stakes.STRENGTHS) == ["very_loose", "loose", "balanced", "tough", "hard"] == sorted(grind.STRENGTH_RANK, key=grind.STRENGTH_RANK.get)
    assert [got.count(x) for x in stakes.STRENGTHS] == [3, 3, 3, 3, 3]                  # five steps of three levels, not two jumps
    assert [grind.STRENGTH_RANK[x] for x in got] == sorted(grind.STRENGTH_RANK[x] for x in got)          # never easier than the level below
    assert got[0] == "very_loose" and got[-1] == "hard"
    sharks = [sum(stakes.table_mix(k)[0]) / 2 + stakes.STRENGTHS[got[i]]["shift"] for i, k in enumerate(KEYS)]
    clamped = [max(stakes.MIN_SHARK, min(5.0 - stakes.MIN_WEAK, x)) for x in sharks]
    assert clamped == sorted(clamped) and clamped[0] == stakes.MIN_SHARK and clamped[-1] == 5.0 - stakes.MIN_WEAK      # the sharks at the table only go up


# ---------------------------------------------------------------- every kind of player gets harder up the ladder
def mean_trait(style, skill, key, n=600):
    from pokercalc.bots import make_traits
    rng = random.Random(7)
    return sum(make_traits(style, rng, 1.0, skill)[key] for _ in range(n)) / n


def test_sharks_get_sharper_with_the_stakes():
    assert stakes.skill(KEYS[0]) == 0 and stakes.skill(KEYS[-1]) == 1 and [stakes.skill(k) for k in KEYS] == sorted(stakes.skill(k) for k in KEYS)
    assert mean_trait("reg", 1.0, "caution") < mean_trait("reg", 0.5, "caution") < mean_trait("reg", 0.0, "caution")     # fewer over-folds
    assert mean_trait("reg", 1.0, "bluff") > mean_trait("reg", 0.0, "bluff") and mean_trait("reg", 1.0, "stick") < mean_trait("reg", 0.0, "stick")
    assert mean_trait("reg", 0.0, "sims_mult") == 1.0 and mean_trait("reg", 1.0, "sims_mult") == 3.0                       # a more accurate read of the hand


def test_fish_stay_fish_but_make_fewer_mistakes_up_the_ladder():
    for style, key in (("station", "loose"), ("station", "pair_bias"), ("station", "size_tell"), ("whale", "loose"), ("whale", "pair_bias"), ("maniac", "bluff")):
        assert mean_trait(style, 1.0, key) < mean_trait(style, 0.0, key), (style, key)
    assert mean_trait("station", 1.0, "margin") > mean_trait("station", 0.0, "margin")                                    # calls closer to the right price
    assert mean_trait("station", 1.0, "loose") > mean_trait("reg", 1.0, "loose") + 0.4                                      # but still very loose compared with a shark
    assert mean_trait("station", 1.0, "sims_mult") == 1.0                                                                   # fish do not get a better read


def test_rec_players_tidy_up_too():
    assert mean_trait("tag", 1.0, "caution") < mean_trait("tag", 0.0, "caution")
    assert mean_trait("nit", 1.0, "size_tell") < mean_trait("nit", 0.0, "size_tell") and mean_trait("tag", 1.0, "sims_mult") == 2.0


def test_the_players_you_meet_are_built_for_the_level():
    from pokercalc.bots import Bot
    rng = random.Random(3)

    def sharks(stake, n=300):
        return [b for _ in range(n) for b in stakes.population(stake, rng, strength="balanced") if b["style"] == "reg"]
    low, top = sharks(KEYS[4]), sharks(KEYS[-1])
    avg = lambda bots, k: sum(b["traits"][k] for b in bots) / len(bots)
    assert avg(top, "caution") < avg(low, "caution") and avg(top, "sims_mult") > avg(low, "sims_mult")
    assert Bot("reg", seed=1, sims=200, traits=top[0]["traits"]).sims > 200                                    # and it really uses the extra trials
    assert Bot("reg", seed=1, sims=200).sims == 200


def test_the_toughest_tables_still_have_the_odd_weaker_player():
    rng = random.Random(9)
    n = 600
    weak = sum(sum(b["style"] != "reg" for b in stakes.population(KEYS[-1], rng, strength="hard")) for _ in range(n)) / n
    assert 0.15 < weak < 0.45


def test_a_players_level_is_locked_to_the_player():
    from pokercalc import personas
    assert len(personas.STYLE_OF) == sum(len(p) for p in personas.POOLS.values())        # no nickname is shared between kinds of player
    rng = random.Random(11)
    seen = {}
    for stake in KEYS:                                                                   # every level, every strength, many tables
        for strength in stakes.STRENGTHS:
            for _ in range(15):
                for b in stakes.population(stake, rng, strength=strength):
                    seen.setdefault(b["persona"]["name"], set()).add(stakes.player_type(b["style"]))
    assert len(seen) > 90 and all(len(types) == 1 for types in seen.values())             # the same person is always the same kind
    assert all(stakes.player_type(personas.STYLE_OF[n]) in types for n, types in seen.items())


def test_screen_names_do_not_give_away_how_anyone_plays():
    import re
    from pokercalc import personas
    hints = re.compile(r"shark|fish|whale|rock|\bnit\b|maniac|fold|call|raise|bluff|nuts|grind|solver|tight|loose|station|donk|aggro|stick|river|flop|allin|all_in|sharp|pro\b|reg\b|rec\b", re.I)
    names = [n for n, _ in (p for pool in personas.POOLS.values() for p in pool)]
    assert len(names) == len(set(names)) == 120 and not [n for n in names if hints.search(n)]
    assert not [b for pool in personas.POOLS.values() for _, b in pool if re.search(r"\b(he|she|him|her|his|hers)\b", b, re.I)]     # neutral wording
    assert len({personas.STYLE_OF[n] for n in names}) == 6                                                                  # all six kinds are there
    assert all(len(n) <= 24 for n in names)                                                                                 # they fit on a seat


# ---------------------------------------------------------------- badges are poker chips, like a video game's achievements
def badges(c):
    return {b["id"]: b for b in grind.summary(c)["badges"]}


def test_every_badge_is_a_chip_and_the_values_climb_from_one_dollar():
    c = con()
    g = grind.summary(c)
    chips = {b["chip"] for b in g["badges"]}
    assert chips == {1, 5, 25, 100, 500, 1000, 5000, 25000, 100000, 1000000}
    assert all(b["chip_text"] and b["rarity"] and b["id"] for b in g["badges"]) and len({b["id"] for b in g["badges"]}) == len(g["badges"]) > 100
    by = {b["id"]: b for b in g["badges"]}
    assert by["sat-down"]["chip"] == 1 and by["king-of-sharks"]["chip"] == 100000 and by["completionist"]["chip"] == 1000000
    assert by["completionist"]["rarity"] == "Solid gold" and by["king-of-sharks"]["rarity"] == "Mythic"
    lv = [b["chip"] for b in g["badges"] if b["kind"] == "level"]
    assert lv == sorted(lv) and lv[0] == 1 and lv[-1] == 100000 and len(lv) == len(KEYS)               # harder levels, bigger chips
    for series in (("pots-10", "pots-100", "pots-500", "pots-2500", "pots-10000"), ("bluffs-5", "bluffs-10", "bluffs-25", "bluffs-50", "bluffs-100", "bluffs-250")):
        vals = [by[i]["chip"] for i in series]
        assert vals == sorted(vals) and len(set(vals)) == len(vals)
    assert g["chips"] == 0 and g["chips_all"] == sum(b["chip"] for b in g["badges"])


def test_secret_badges_are_marked_and_have_a_hint():
    g = grind.summary(con())
    secret = [b for b in g["badges"] if b["secret"]]
    assert len(secret) == 10 and all(b["hint"] and not b["earned"] for b in secret)
    assert {b["id"] for b in secret} >= {"seven-deuce", "fold-aces", "bad-beat", "cooler", "stacked", "night-owl", "royal", "tour-bubble", "tour-nomad"}


def test_the_completionist_chip_needs_every_other_badge():
    c = con()
    by = badges(c)
    comp = by["completionist"]
    others = [b for b in by.values() if b["id"] != "completionist"]
    assert comp["goal"] == len(others) and comp["progress"] == 0 and not comp["earned"]
    add(c, KEYS[0], 1.0, n=1)
    by = badges(c)
    assert by["completionist"]["progress"] == sum(1 for b in by.values() if b["earned"] and b["id"] != "completionist") > 0
    assert not by["completionist"]["earned"]
    assert by["completionist"]["text"] == "Earn every other badge, secret ones included"


def hand_row(c, stake, hole, net_bb, board="", showdown=0, actions="[]", meta=None, ts=None, practice=0):
    import time
    ts = ts if ts is not None else time.mktime((2026, 11, 7, 14, 0, 0, 0, 0, -1))       # 2pm local, so it is not the night-owl hour
    bb = stakes.STAKES[stake]["bb"]
    n = c.execute("SELECT COUNT(*) FROM trainer_hands").fetchone()[0]
    c.execute("INSERT INTO trainer_hands(ts,stake,hand_no,position,hole,board,pot,net,showdown,actions,strength,meta,practice) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (ts + n, stake, n, "BTN", hole, board, 0, net_bb * bb, showdown, actions, "balanced", json.dumps(meta) if meta else None, practice))
    c.commit()


def test_secret_badges_unlock_on_their_moments():
    c, first = con(), KEYS[0]
    hand_row(c, first, "7h 2c", 5, showdown=1)                                                    # won with seven-deuce offsuit
    hand_row(c, first, "7h 2h", 5, showdown=1)                                                    # suited does not count
    hand_row(c, first, "Ah As", -1, actions=json.dumps([["preflop", 0, "fold", None]]))             # folded aces
    hand_row(c, first, "Kh Kd", -150, showdown=1, meta={"hero_hand": "Full house, Kings full of Twos", "vs": []})      # lost 150bb with a boat
    hand_row(c, first, "Qh Qd", -20, showdown=1, meta={"hero_hand": "Four of a kind, Queens", "vs": []})              # lost with quads
    by = badges(c)
    assert by["seven-deuce"]["earned"] and by["fold-aces"]["earned"] and by["bad-beat"]["earned"] and by["cooler"]["earned"] and by["stacked"]["earned"]
    assert by["seven-deuce"]["progress"] == 1                                                      # only the offsuit one counted
    assert not by["royal"]["earned"] and not by["night-owl"]["earned"]
    c2 = con()
    hand_row(c2, first, "Ah As", -1, actions=json.dumps([["preflop", 0, "call", None]]))             # called with them: not a fold
    hand_row(c2, first, "Ah Kd", -3, actions=json.dumps([["preflop", 0, "fold", None]]))             # folded something else
    assert not badges(c2)["fold-aces"]["earned"]


def test_night_owl_uses_your_local_clock():
    import time
    c = con()
    two_pm = time.mktime((2026, 11, 7, 14, 0, 0, 0, 0, -1))
    three_am = time.mktime((2026, 11, 8, 3, 0, 0, 0, 0, -1))
    hand_row(c, KEYS[0], "As Ks", 1, ts=two_pm)
    assert not badges(c)["night-owl"]["earned"]
    hand_row(c, KEYS[0], "As Ks", 1, ts=three_am)
    assert badges(c)["night-owl"]["earned"]


def test_showdown_streaks_and_marathons():
    c = con()
    for i in range(5):
        hand_row(c, KEYS[0], "As Ks", 2, showdown=1, ts=1_000_000 + i)
        hand_row(c, KEYS[0], "9s 8s", -1, ts=1_000_000 + i + 0.5)                                   # folded hands in between do not break it
    by = badges(c)
    assert by["sd-streak-3"]["earned"] and by["sd-streak-5"]["earned"] and not by["sd-streak-10"]["earned"] and not by["marathon"]["earned"]
    hand_row(c, KEYS[0], "As Ks", -2, showdown=1, ts=1_000_100)                                       # a lost showdown ends the run
    for i in range(5):
        hand_row(c, KEYS[0], "As Ks", 2, showdown=1, ts=1_000_200 + i)
    assert badges(c)["sd-streak-10"]["earned"] is False                                               # 5 then a loss then 5: best run is still 5
    c = con()
    for i in range(200):
        hand_row(c, KEYS[0], "As Ks", 0.5, ts=1_000_000 + i)                                          # 200 hands within one session
    by = badges(c)
    assert by["marathon"]["earned"] and not by["iron-man"]["earned"]


def test_the_strongest_sharks_are_the_hardest_badges():
    from pokercalc import personas
    c = con()
    shark = personas.SHARKS[0]
    win = {"hero_allin": True, "showdown": True, "hero_hand": "Pair, Aces", "vs": [{"name": shark, "type": "shark", "allin": True}]}
    hand_row(c, KEYS[3], "As Ah", 50, showdown=1, meta=win)                                           # a shark at NL25
    by = badges(c)
    assert by["allin-shark"]["earned"] and by[grind.shark_badge_id(shark)]["earned"] and not by["deep-water"]["earned"]
    hand_row(c, KEYS[9], "As Ah", 50, showdown=1, meta=win)                                           # a shark at NL2,000
    hand_row(c, KEYS[12], "As Ah", 50, showdown=1, meta=win)                                          # at NL20,000
    by = badges(c)
    assert by["deep-water"]["earned"] and by["nosebleed"]["earned"] and not by["king-of-sharks"]["earned"]
    assert by["king-of-sharks"]["chip"] == 100000 and by["king-of-sharks"]["rarity"] == "Mythic"
    hand_row(c, KEYS[14], "As Ah", 50, showdown=1, meta=win)                                          # at NL100,000: the strongest sharks
    assert badges(c)["king-of-sharks"]["earned"]
    c2 = con()                                                                                        # a loss, or no all-in, earns nothing
    hand_row(c2, KEYS[14], "As Ah", -50, showdown=1, meta=win)
    assert not badges(c2)["king-of-sharks"]["earned"]


def test_changing_the_badges_re_records_what_you_have_instead_of_announcing_it():
    c = con()
    grind.check_new(c)
    add(c, KEYS[0], 1.0, n=1)
    c.execute("UPDATE trainer_state SET value='1' WHERE key='grind_badge_version'")                  # saved by an older version of the badges
    c.commit()
    assert grind.check_new(c) == []                                                                   # no flood of pop-ups for old progress
    add(c, KEYS[0], 1.0, n=10)
    assert grind.check_new(c) != []                                                                   # but new progress is announced as usual


# ---------------------------------------------------------------- multi-table tournaments: locked like cash, paid from the same account
def to_tournament(client):
    return client.post("/api/trainer/settings", json={"mode": "tournament"})


def play_tourney_out(client, limit=400):
    """Play every hand by folding unless you can check (and calling small bets), until the tournament is over."""
    d = client.get("/api/trainer/state").get_json()
    for _ in range(limit):
        if d["tourney"] and d["tourney"]["finished"]:
            return d
        d = client.post("/api/trainer/deal").get_json()
        while d.get("legal"):
            L = d["legal"]
            d = client.post("/api/trainer/act", json={"action": "check" if L["can_check"] else "call" if L["to_call"] <= 3 * d["bb"] else "fold"}).get_json()
    return d


def test_the_tournament_levels_have_their_own_ladder_and_start_locked(client):
    to_tournament(client)
    cfg = client.get("/api/trainer/config").get_json()
    assert cfg["mode"] == "tournament" and [r["locked"] for r in cfg["stakes"]] == [False] + [True] * (len(KEYS) - 1)
    assert cfg["stakes"][0]["entry"] == "£2" and cfg["stakes"][0]["prize_pool"] == "£72"                      # 36 entries of £2
    assert cfg["stakes"][1]["lock_text"] == "NL5 tournaments are locked: at NL2 you need 2 in-the-money finishes, a profit and 8 tournaments played (0 in the money, 0 played, +£0 so far)."
    r = client.post("/api/tourney/register", json={"stake": KEYS[3]})
    assert r.status_code == 403 and "NL25 tournaments are locked" in r.get_json()["error"]
    assert client.get("/api/trainer/state").get_json()["tourney"] is None


def test_registering_takes_the_fee_from_the_grinding_account_and_fixes_the_settings(client):
    to_tournament(client)
    g = server.grind_table()
    before = g.bankroll + g.hero_stack
    r = client.post("/api/tourney/register", json={"stake": KEYS[0]})
    d = r.get_json()
    assert r.status_code == 200 and d["mode"] == "tournament" and d["chip_mode"] is True
    assert g.bankroll + g.hero_stack == before - 200 and g.hero_stack == 0                                     # you stood up from the cash table
    assert d["grind_account"] == before - 200
    tr = d["tourney"]
    assert (tr["size"], tr["left"], tr["tables"], tr["paid"], tr["buy_in"], tr["prize_pool"]) == (36, 36, 6, 5, 200, 36 * 200)
    assert d["strength"] == grind.RULES[KEYS[0]]["min_strength"] and d["speed"] == "normal" and d["online_clock"] is False
    for body in ({"strength": "hard"}, {"speed": "instant"}, {"stake": KEYS[1]}, {"buy_in": 100}):
        assert client.post("/api/trainer/settings", json=body).status_code == 403
    assert client.post("/api/trainer/settings", json={"timer_s": 0}).status_code == 200                       # NL2 keeps a free shot clock
    assert client.post("/api/tourney/register", json={"stake": KEYS[0]}).status_code == 400                    # already in one
    for path in ("rebuy", "cashout", "reset"):
        assert client.post(f"/api/trainer/{path}").status_code == 403


def cash_finish(place=3, stake=None, prize=3000, source="casino", buy_in=200):
    c = server.db()                                                      # (one connection: the insert and the commit must be on the same one)
    c.execute("INSERT INTO tournaments(ts,stake,place,size,buy_in,prize,hands,knockouts,level,final_table,heads_up,min_bb,table_moves,sharks,source,bb) "
              "VALUES(strftime('%s','now'),?,?,36,?,?,60,1,5,1,0,8,2,4,?,0.02)", (stake if stake is not None else KEYS[0], place, buy_in, prize, source))
    c.commit()


def fill_level(stake, cashes, played, prize=3000):
    """`cashes` in-the-money finishes (each paying `prize`) out of `played` tournaments at a level; the rest finish out of the money."""
    for i in range(played):
        cash_finish(place=3 if i < cashes else 20, stake=stake, prize=prize if i < cashes else 0)


def test_clearing_a_tournament_level_needs_the_cashes_a_profit_and_enough_played(client):
    to_tournament(client)
    assert server.db().execute("SELECT COUNT(*) FROM trainer_hands").fetchone()[0] == 0              # no cash hands at all
    fill_level(KEYS[0], 2, 2)                                                                          # two cashes but only two played
    st = client.get("/api/trainer/config").get_json()["stakes"]
    assert st[1]["locked"] is True and st[0]["tcashes"] == 2 and st[0]["tplayed"] == 2 and st[0]["tmin"] == 8
    fill_level(KEYS[0], 0, 6)                                                                          # eight played; two big cashes outweigh the six lost entries
    st = client.get("/api/trainer/config").get_json()["stakes"]
    assert st[1]["locked"] is False and st[1]["tstatus"] == "current" and st[2]["locked"] is True
    server.grind_table().set_account(1000)
    r = client.post("/api/tourney/register", json={"stake": KEYS[1]})
    assert r.status_code == 200 and r.get_json()["tourney"]["buy_in"] == 500                           # a £5 entry at NL5
    assert grind.summary(server.db())["levels"][0]["status"] == "current"                              # and the cash ladder has not moved


def test_a_loss_overall_keeps_a_level_locked_even_with_the_cashes_and_games(client):
    to_tournament(client)
    fill_level(KEYS[0], 2, 8, prize=300)                                                               # 2 cashes of 3.00 against eight 2.00 entries: -10.00
    st = client.get("/api/trainer/config").get_json()["stakes"]
    assert st[1]["locked"] is True and st[0]["tnet"] == "-£10" and "profit" in st[1]["lock_text"]
    fill_level(KEYS[0], 1, 1, prize=2000)                                                              # a 20.00 cash puts it back in profit
    assert client.get("/api/trainer/config").get_json()["stakes"][1]["locked"] is False


def test_missing_the_money_and_real_site_results_do_not_unlock_anything(client):
    to_tournament(client)
    fill_level(KEYS[0], 0, 8)
    cash_finish(place=1, stake="", prize=50000, source="rta")                                          # a real-site win (and it must not break the page)
    st = client.get("/api/trainer/config").get_json()["stakes"]
    assert st[1]["locked"] is True and st[0]["tcashes"] == 0
    assert client.get("/api/grind").status_code == 200


def test_the_tournament_ladder_needs_three_cashes_above_the_third_level(client):
    for k in KEYS[:3]:
        fill_level(k, 2, 8)
    tl = grind.summary(server.db())["tourney_levels"]
    assert [x["status"] for x in tl[:4]] == ["cleared", "cleared", "cleared", "current"] and tl[3]["need"] == 3
    fill_level(KEYS[3], 2, 8)
    assert grind.summary(server.db())["tourney_levels"][3]["status"] == "current"                      # two is not enough here
    fill_level(KEYS[3], 1, 1)
    assert grind.summary(server.db())["tourney_levels"][4]["status"] == "current"


def test_the_entry_must_fit_your_account_and_being_broke_gives_a_starter_balance(client):
    to_tournament(client)
    fill_level(KEYS[0], 2, 8)
    g = server.grind_table()
    g.cash_out()
    g.set_account(300)                                                                                  # £3: enough for NL2's £2 entry, not NL5's £5
    r = client.post("/api/tourney/register", json={"stake": KEYS[1]})
    assert r.status_code == 403 and "entry is £5" in r.get_json()["error"]
    g.set_account(150)                                                                                  # less than the lowest entry: a free starter balance
    r = client.post("/api/tourney/register", json={"stake": KEYS[0]})
    d = r.get_json()
    assert r.status_code == 200 and "free starter balance" in d["notice"] and d["grind_account"] == stakes.starter_balance() - 200


def test_the_prize_is_paid_into_the_same_account_and_the_tournament_is_saved_and_never_counts_for_cash(client):
    to_tournament(client)
    g = server.grind_table()
    client.post("/api/tourney/register", json={"stake": KEYS[0]})
    after_fee = g.bankroll + g.hero_stack
    grind.check_new(server.db())
    d = play_tourney_out(client)
    assert d["tourney"]["finished"] is True and d["revealed"] is True
    res = d["tourney"]["result"]
    assert g.bankroll + g.hero_stack == after_fee + res["prize"]                                               # the prize, if any, went into the account
    con = server.db()
    row = con.execute("SELECT * FROM tournaments").fetchone()
    assert row["place"] == res["place"] and row["size"] == 36 and row["prize"] == res["prize"] and con.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0] == 1
    rows = con.execute("SELECT practice, COUNT(*) FROM trainer_hands GROUP BY practice").fetchall()
    assert [(r[0]) for r in rows] == [2]                                                                      # saved as tournament hands ...
    assert grind.summary(con)["total_hands"] == 0 and grind.summary(con)["levels"][0]["hands"] == 0            # ... and never counted for the ladder
    by = {b["id"]: b for b in grind.summary(con)["badges"]}
    assert by["tour-1"]["earned"]
    assert client.post("/api/tourney/register", json={"stake": KEYS[0]}).status_code in (200, 403)             # a new one can be entered (money allowing)


def test_mid_tournament_you_can_switch_to_cash_between_hands_and_come_back(client):
    to_tournament(client)
    client.post("/api/tourney/register", json={"stake": KEYS[0]})
    client.post("/api/trainer/deal")
    t = server.tourney_table()
    while t.hand is not None and not t.hand.over:
        t.hero_act("check" if t.hand.legal(0)["can_check"] else "fold")
    left = client.get("/api/trainer/state").get_json()["tourney"]["left"]
    assert client.post("/api/trainer/settings", json={"mode": "grind"}).get_json()["mode"] == "grind"
    assert client.post("/api/trainer/settings", json={"mode": "tournament"}).get_json()["tourney"]["left"] == left   # exactly where you were


def tour_row(c, stake=None, place=20, size=36, prize=0, hands=60, ko=0, level=3, final=0, hu=0, min_bb=12.0, moves=0, sharks=10, ts=None):
    import time
    from pokercalc import store
    store.log_tournament(c, {"stake": stake or KEYS[0], "place": place, "size": size, "buy_in": 200, "prize": prize, "hands": hands, "knockouts": ko,
                             "level": level, "final_table": final, "heads_up": hu, "min_bb": min_bb, "table_moves": moves, "sharks": sharks})
    if ts is not None:
        c.execute("UPDATE tournaments SET ts=? WHERE id=(SELECT MAX(id) FROM tournaments)", (ts,))
        c.commit()


def test_tournament_badges_unlock_from_your_results():
    c = con()
    assert not any(b["earned"] for b in badges(c).values() if b["group"] == "Tournaments")
    tour_row(c, place=6, final=1)
    by = badges(c)
    assert by["tour-1"]["earned"] and by["tour-final"]["earned"] and by["tour-bubble"]["earned"] and not by["tour-cash"]["earned"]     # 6th of 36: the bubble
    tour_row(c, place=3, prize=900, final=1, hu=0, ko=3)
    by = badges(c)
    assert by["tour-cash"]["earned"] and by["tour-podium"]["earned"] and by["tour-ko-3"]["earned"] and not by["tour-ko-6"]["earned"] and not by["tour-win"]["earned"]
    tour_row(c, place=1, prize=3000, final=1, hu=1, ko=7, min_bb=3.0, sharks=18, moves=4, hands=170)
    by = badges(c)
    assert by["tour-win"]["earned"] and by["tour-hu"]["earned"] and by["tour-ko-6"]["earned"] and by["tour-short"]["earned"] and by["tour-sharks"]["earned"]
    assert by["tour-nomad"]["earned"] and by["tour-long"]["earned"] and not by["tour-back"]["earned"] and not by["tour-win-5"]["earned"]
    assert by["tour-win"]["chip"] == 500 and by["tour-boss"]["chip"] == 100000 and by["tour-bubble"]["secret"] and by["tour-nomad"]["secret"]


def test_winning_in_a_row_and_winning_at_the_top_levels_are_the_hard_ones():
    c = con()
    tour_row(c, place=1, prize=3000, hu=1, final=1, ts=1_000_100)
    tour_row(c, place=1, prize=3000, hu=1, final=1, ts=1_000_200)
    by = badges(c)
    assert by["tour-back"]["earned"] and not by["tour-deep"]["earned"]
    tour_row(c, stake=KEYS[9], place=1, prize=9000, hu=1, final=1, ts=1_000_300)                        # NL2,000
    tour_row(c, stake=KEYS[12], place=1, prize=9000, hu=1, final=1, ts=1_000_400)                       # NL20,000
    by = badges(c)
    assert by["tour-deep"]["earned"] and by["tour-nose"]["earned"] and not by["tour-boss"]["earned"] and by["tour-win-5"]["progress"] == 4
    tour_row(c, stake=KEYS[14], place=1, prize=9000, hu=1, final=1, ts=1_000_500)                       # NL100,000: the final boss
    by = badges(c)
    assert by["tour-boss"]["earned"] and by["tour-win-5"]["earned"] and not by["tour-win-10"]["earned"]
    c2 = con()
    tour_row(c2, place=1, ts=1_000_100)
    tour_row(c2, place=2, ts=1_000_200)
    tour_row(c2, place=1, ts=1_000_300)
    assert not badges(c2)["tour-back"]["earned"]                                                          # a loss in between breaks the run


def test_the_rta_used_on_its_own_is_not_flagged_as_practice_just_because_the_casino_page_was_left_there(client):
    from pokercalc import analytics
    client.post("/api/session/start", json=dict(balance=60, buy_in=2, bb=0.02))
    client.post("/api/trainer/settings", json={"mode": "practice"})            # the Casino page sits in Practice, but you are playing elsewhere
    hand = dict(hole=["As", "Ks"], board=[], pot=1, call=1, opps=1, equity=0.5, required=0.3, suggested="CALL", taken="CALL", result=1,
                decisions=[{"street": 0, "suggested": "CALL", "taken": "CALL"}])
    client.post("/api/hand", json=hand)
    c = server.db()
    row = c.execute("SELECT source, practice FROM hands").fetchone()
    assert (row["source"], row["practice"]) == ("rta", 0) and c.execute("SELECT COUNT(*) FROM trainer_hands").fetchone()[0] == 0
    assert grind.summary(c)["rta"]["decisions"] == 1 and analytics.summary(c)["hands"] == 1            # counts for Analytics and for the RTA badges
    assert not grind.summary(c)["levels"][0]["hands"]                                                  # but levels need Casino hands, so no ladder progress


def test_tournament_hands_count_for_card_badges_but_not_for_cash_ones():
    c, first = con(), KEYS[0]
    hand_row(c, first, "7h 2c", 5, showdown=1, practice=2)                                         # seven-deuce won in a tournament
    hand_row(c, first, "Qh Qd", -20, showdown=1, practice=2, meta={"hero_hand": "Four of a kind, Queens", "vs": []})   # a cooler in a tournament
    hand_row(c, first, "Kh Kd", -150, showdown=1, practice=2, meta={"hero_hand": "Pair", "vs": []})                     # a big loss: money-based, cash only
    by = badges(c)
    assert by["seven-deuce"]["earned"] and by["cooler"]["earned"] and by["bad-beat"]["earned"]
    assert not by["stacked"]["earned"]                                                             # money badges ignore tournament hands
    assert not by["pots-1"]["earned"]                                                              # so do pot counts and levels
    assert badges(con())["pots-1"]["progress"] == 0


def tbadges():
    return {b["id"]: b for b in grind.summary(server.db())["badges"]}


def test_tournament_ladder_badges_follow_the_ladder_and_profit(client):
    fill_level(KEYS[0], 2, 8)                                                                          # clears the first tournament level, in profit
    b = tbadges()
    assert b[f"tlevel-{KEYS[0]}"]["earned"] and not b[f"tlevel-{KEYS[1]}"]["earned"]
    assert b["tclimb-3"]["progress"] == 1 and not b["tclimb-3"]["earned"]
    assert not b["tprofit"]["earned"] and b["tplayed-50"]["progress"] == 8                            # profit needs 10 played
    fill_level(KEYS[0], 0, 2)
    assert tbadges()["tprofit"]["earned"]
    for k in KEYS[1:3]:
        fill_level(k, 2, 8)
    b = tbadges()
    assert b["tclimb-3"]["earned"] and b["tlevel-" + KEYS[2]]["earned"]


def test_tournament_streak_and_back_in_the_black_badges(client):
    for _ in range(3):
        cash_finish(place=2, prize=300)
    assert tbadges()["tstreak-3"]["earned"] and not tbadges()["tblack"]["earned"]
    fill_level(KEYS[1], 0, 3)                                                                          # behind at another level (-6.00) ...
    cash_finish(stake=KEYS[1], prize=3000)                                                             # ... then a big cash moves it into profit
    assert tbadges()["tblack"]["earned"]


def test_real_site_tournaments_do_not_count_for_the_ladder_badges(client):
    for _ in range(12):
        cash_finish(place=1, stake="", prize=50000, source="rta")
    b = tbadges()
    assert not b["tprofit"]["earned"] and not b["tstreak-3"]["earned"] and b["tplayed-50"]["progress"] == 0
