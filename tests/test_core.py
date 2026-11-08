import pytest
from pokercalc.equity import equity
from pokercalc.parser import parse_line, ParseError
from pokercalc.potodds import pot_odds
from pokercalc.ranges import top_percent, hole_class, ranking


def heads_up(h1, h2):
    # exact-ish: hero h1 vs fixed villain via pool trick is not supported, so use eval7 directly
    import eval7, random
    rng = random.Random(1)
    a = [eval7.Card(c) for c in h1]; b = [eval7.Card(c) for c in h2]
    deck = [eval7.Card(r + s) for r in "23456789TJQKA" for s in "cdhs"]
    deck = [c for c in deck if c not in a + b]
    t = 0.0
    n = 40000
    for _ in range(n):
        bd = rng.sample(deck, 5)
        x, y = eval7.evaluate(a + bd), eval7.evaluate(b + bd)
        t += 1 if x > y else 0.5 if x == y else 0
    return t / n


def test_aa_vs_kk():
    assert heads_up(["Ac", "Ad"], ["Kh", "Ks"]) == pytest.approx(0.82, abs=0.01)

def test_aks_vs_qq():
    assert heads_up(["Ah", "Kh"], ["Qc", "Qd"]) == pytest.approx(0.46, abs=0.015)

def test_equity_vs_random_aa():
    _, _, eq = equity(["Ah", "As"], [], 1, sims=20000, seed=1)
    assert eq == pytest.approx(0.852, abs=0.01)

def test_equity_known_flop():
    # nut flush draw + overcards style sanity: made nut flush on river = 1.0 vs anything lower
    _, _, eq = equity(["Ah", "Kh"], ["Qh", "Jh", "2h", "3c", "9d"], 1, sims=2000, seed=1)
    assert eq > 0.99

def test_pot_odds():
    req, _ = pot_odds(2600, 400)
    assert req == pytest.approx(400 / 3000)

def test_parse():
    q = parse_line("6d 5h | 5s 7h Qh | pot 2600 call 400 | 2 opp top25")
    assert q.hole == ["6d", "5h"] and q.board == ["5s", "7h", "Qh"]
    assert (q.pot, q.call, q.opponents, q.range_pct) == (2600, 400, 2, 25)
    with pytest.raises(ParseError):
        parse_line("6d 6d | | pot 1 | 1 opp")

def test_ranking():
    r = ranking()
    assert len(r) == 169 and r[0] == "AA" and r[-1] in ("72o", "32o", "82o")
    assert hole_class("Ah", "Ks") == "AKo"
    assert 20 <= len(top_percent(15)) <= 40
