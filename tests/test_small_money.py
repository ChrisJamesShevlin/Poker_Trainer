"""Dollar-sized games ($0.02/$0.05) show cents in the advice; whole-chip games look exactly as before."""
import re

from pokercalc import service


def calc(**kw):
    d = dict(hole=["As", "Ah"], board=[], pot=0.07, call=0, opps=2, position="BTN", stack=5, bb=0.05, range_mode="auto")
    d.update(kw)
    return service.evaluate(d, sims=800)


def test_preflop_sizing_shows_cents_not_zero():
    r = calc()
    assert r["action"] == "RAISE"
    assert re.search(r"~0\.\d+", r["sizing"]) and "~0 " not in r["sizing"], r["sizing"]
    assert r["stack_line"].startswith("Stack 5") and "(100bb)" in r["stack_line"]


def test_postflop_bet_and_bluff_maths_show_cents():
    r = calc(hole=["As", "Ah"], board=["Ad", "7h", "2c"], pot=0.22, call=0, opps=1)
    assert r["action"] in ("BET", "RAISE") and re.search(r"~0\.\d+", r["sizing"]), r["sizing"]
    assert r["fold_equity"] and "0." in r["fold_equity"] and "of 0 " not in r["fold_equity"], r["fold_equity"]


def test_whole_chip_games_are_unchanged():
    r = calc(pot=3, stack=200, bb=2)
    assert re.search(r"~\d+ ", r["sizing"]) and "." not in r["sizing"].split("(")[0], r["sizing"]
    assert r["stack_line"].startswith("Stack 200")


def test_the_mode_does_not_leak_between_calls():
    calc()                                              # a dollar-sized call first...
    r = calc(pot=3, stack=200, bb=2)                    # ...then a chip-sized one
    assert "." not in r["sizing"].split("(")[0]
