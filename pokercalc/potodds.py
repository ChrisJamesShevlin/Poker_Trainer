"""Pot odds and break-even equity."""


def pot_odds(pot: float, call: float):
    """pot = chips in the middle BEFORE your call (includes the bet you face).
    Returns (required_equity, ratio_text)."""
    if call <= 0:
        return 0.0, "free to check"
    req = call / (pot + call)
    return req, f"{pot / call:.2f} : 1"


def verdict(eq: float, pot: float, call: float):
    req, _ = pot_odds(pot, call)
    if call <= 0:
        return "No bet to call"
    margin = eq - req
    if margin > 0.02:
        return f"Call is profitable (+{margin:.1%} over break-even)"
    if margin < -0.02:
        return f"Call is NOT profitable ({margin:.1%} under break-even)"
    return "Marginal - within noise of break-even"
