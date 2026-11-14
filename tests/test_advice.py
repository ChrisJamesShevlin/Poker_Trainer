

def test_a_player_who_calls_a_lot_never_makes_you_call_looser_than_normal():
    """A river call with 30% equity when 33% is needed loses money; 'calls a lot' used to allow it (a 4% shortfall)."""
    from pokercalc import service
    spot = {"hole": ["Kd", "Jd"], "board": ["Jc", "4d", "Ah", "Td", "7h"], "pot": 2.54, "call": 1.27, "opps": 1, "position": "SB",
            "stack": 1.37, "bb": 0.02, "range_mode": 15}
    for t in ("normal", "calls", "folds"):
        assert service.evaluate(dict(spot, opp_type=t), sims=8000)["action"] == "FOLD", t
