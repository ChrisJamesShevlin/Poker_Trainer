

def test_small_pairs_are_set_mining_calls_from_the_hijack_and_cutoff_when_deep():
    from pokercalc import preflop
    for pos in ("HJ", "CO"):
        g = preflop.advise(["3s", "3h"], pos, 100, True, "normal", 3.0, "UTG")
        assert g["action"] == "CALL" and "set-mine" in g["why"]
    short = preflop.advise(["3s", "3h"], "HJ", 25, True, "normal", 3.0, "UTG")
    assert short["action"] == "FOLD"                                          # not deep enough to set-mine
    sb = preflop.advise(["3s", "3h"], "SB", 100, True, "normal", 3.0, "UTG")
    assert sb["action"] != "CALL"                                             # out of position in the small blind, still not a call
