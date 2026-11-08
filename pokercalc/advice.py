"""Turns equity + pot odds into a simple check/call/raise/fold suggestion."""
import contextvars
from .potodds import pot_odds

# Equity (vs the opponent model you picked) above which we lean toward betting/raising for value.
STRONG = {1: 0.65, 2: 0.50, 3: 0.42}


def strong_threshold(opps):
    return STRONG.get(opps, 0.36)


def suggest(eq, pot, call, opps, stack=None):
    req, _ = pot_odds(pot, call)
    strong = strong_threshold(opps)
    if call <= 0:
        if eq >= strong:
            return "BET", f"equity {eq:.0%} >= {strong:.0%}: bet for value (~2/3 pot = {fmt_amount(pot*2/3)})"
        return "CHECK", f"equity {eq:.0%} is below the {strong:.0%} value-bet line"
    allin = stack is not None and call >= stack
    margin = eq - req
    if margin < -0.02:
        return "FOLD", f"equity {eq:.0%} < break-even {req:.0%}"
    if eq >= strong and not allin:
        return "RAISE", f"equity {eq:.0%} is strong ({strong:.0%}+ line) and beats break-even {req:.0%}"
    if margin <= 0.02:
        return "CALL", f"marginal: equity {eq:.0%} ~ break-even {req:.0%}"
    return "CALL", f"equity {eq:.0%} > break-even {req:.0%}"


# ---------------------------------------------------------------------------------------------
# Postflop advice v2: position, stack depth, bet sizing and draws. Rules of thumb, not a solver.
# ---------------------------------------------------------------------------------------------
# Acting later is worth equity: the "strong enough to bet/raise" line drops in position, rises out of it.
POSITION_EDGE = {"BTN": 0.03, "CO": 0.015, "HJ": 0.0, "UTG": 0.0, "SB": -0.03, "BB": -0.03}
IN_POSITION = {"BTN": "in position", "CO": "usually in position", "HJ": "in the middle", "UTG": "in the middle",
               "SB": "out of position", "BB": "out of position"}


# Tunable numbers of the post-flop rules (the defaults are what the app uses; tools/tests can change them to compare versions).
TUNE = {"strong_shift": 0.0,       # added to the equity needed to bet/raise for value
        "raise_margin": 0.05,      # raise for value when equity is this far above the value line
        "semi_outs": 6, "semi_opps": 2,      # a draw is worth a semi-bluff with this many outs, against this many opponents or fewer
        "combo_outs": 12,                    # semi-bluff RAISE needs this many outs
        "bluff_normal": True,                # also make the pure bluff against an ordinary opponent (not only against "folds")
        "bluff_size": 0.5, "value_frac": 0.33, "raise_x": 3.0}


# How the opponent plays: how often they give up a bet. Changes how much we bluff and how thin we value-bet.
OPP_TYPES = {"folds": "vs a player who folds a lot", "normal": "", "calls": "vs a player who calls a lot"}


class Size(str):
    """A sizing text that also carries the chip amount, so fold-equity maths can use it."""
    chips = None


def fold_math(pot, bet, eq=None):
    """Break-even fold frequency for a bet/raise of `bet` into a pot of `pot` (pot BEFORE the bet).
    Returns (pure_bluff_breakeven, breakeven_counting_your_equity_when_called). Rough: the equity is
    against the range you chose, not against only the hands that would call."""
    if not pot or not bet or bet <= 0:
        return None, None
    pure = bet / (pot + bet)
    with_eq = None
    if eq is not None:
        c = eq * (pot + 2 * bet) - bet          # your average result when called, counting what you put in
        with_eq = 0.0 if c >= 0 else -c / (pot - c)
    return pure, with_eq


# Small money mode: when the big blind is under 1 (dollar-sized games like $0.02/$0.05) amounts are shown with cents
# instead of being rounded to whole chips. Set per request by service.evaluate().
_SMALL = contextvars.ContextVar("small_units", default=False)


def set_small_units(on):
    return _SMALL.set(bool(on))


def reset_small_units(token):
    _SMALL.reset(token)


def fmt_amount(x):
    """An amount for the advice text: whole chips normally, up to cents in small money mode (0.07, 0.15, 5)."""
    if _SMALL.get():
        return f"{x:,.2f}".rstrip("0").rstrip(".")
    return f"{x:,.0f}"


def _chips(x):
    if _SMALL.get():
        r = round(x, 2)
        return int(r) if r == int(r) else r
    return int(round(x, -1)) if x >= 100 else int(round(x))


def _size(chips, pot, stack=None):
    """'~1,300 (50% of pot)' - capped at the stack."""
    if stack and chips >= stack:
        out = Size(f"all-in ({_chips(stack):,})")
        out.chips = stack
        return out
    out = Size(f"~{_chips(chips):,} ({chips / pot:.0%} of pot)" if pot else f"~{_chips(chips):,}")
    out.chips = chips
    return out


def bluff_kind(action, why):
    """"BLUFF" (nothing to show down), "SEMI-BLUFF" (a draw that can still win if called) or None, from the advice text."""
    if action not in ("BET", "RAISE"):
        return None
    w = (why or "").lower()
    return "SEMI-BLUFF" if w.startswith("semi-bluff") else "BLUFF" if w.startswith("bluff") else None


def suggest_postflop(eq, pot, call, opps, stack=None, position="BTN", board=(), hold=None, opp_type="normal"):
    """Like _postflop(), plus a note when the opponent type changed the advice."""
    action, why, sizing = _postflop(eq, pot, call, opps, stack, position, board, hold, opp_type)
    note = OPP_TYPES.get(opp_type, "")
    return action, (why + f" [{note}]" if note else why), sizing


def _postflop(eq, pot, call, opps, stack=None, position="BTN", board=(), hold=None, opp_type="normal"):
    """Returns (action, why, sizing). `hold` is describe.hold_summary(); board is the list of board cards."""
    hold = hold or {}
    street = len(board)
    draws = hold.get("draws") or []
    outs = hold.get("outs") or 0
    texture = " ".join(hold.get("texture") or [])
    wet = "flush" in texture or "straight" in texture
    edge = POSITION_EDGE.get(position, 0.0)
    where = IN_POSITION.get(position, "")
    strong = strong_threshold(opps) - edge - (0.03 if opp_type == "calls" else 0.0) + TUNE["strong_shift"]   # a caller pays off thinner value bets
    drawing = street < 5 and outs >= TUNE["semi_outs"] and bool(draws)
    combo = street < 5 and outs >= TUNE["combo_outs"]
    spr = stack / pot if stack and pot else None
    req, _ = pot_odds(pot, call)

    # ---- nobody has bet: we can check or bet
    if call <= 0:
        if eq >= strong + 0.15:
            frac = 0.75 if wet else 0.5
            return "BET", (f"Very strong ({eq:.0%}). Bet big." +
                           (" Draws are out there, so make them pay." if wet else "")), _size(pot * frac, pot, stack)
        if eq >= strong:
            frac = 0.5 if wet else TUNE["value_frac"]
            if drawing and hold.get("made") == "High Card":
                return "BET", (f"{' + '.join(draws)} ({outs} outs) and already ahead ({eq:.0%}). Bet."), _size(pot * (0.66 if wet else 0.5), pot, stack)
            return "BET", (f"{eq:.0%} equity: bet small for value so worse hands call"), _size(pot * frac, pot, stack)
        if drawing and opps <= TUNE["semi_opps"] and (edge >= 0 or eq >= 0.30) and (opp_type != "calls" or eq >= 0.30):
            frac = 0.66 if wet else 0.5
            what = " + ".join(draws)
            return "BET", (f"semi-bluff: {what} ({outs} outs). You win if they fold or you hit"), _size(pot * frac, pot, stack)
        if drawing and opps <= 2:
            return "CHECK", f"Draw ({outs} outs) but {where}: take the free card", None
        if (opp_type == "folds" or (TUNE["bluff_normal"] and opp_type == "normal")) and opps == 1 and edge >= 0 and not wet and hold.get("made") in (None, "High Card"):
            return "BET", ("bluff: no showdown value, dry board, in position, and they fold a lot. "
                           "Needs them to fold often enough, see the bluff maths"), _size(pot * TUNE["bluff_size"], pot, stack)
        return "CHECK", (f"{eq:.0%} equity is too low to bet: check"
                         f"{' (too many opponents to bluff)' if opps >= 3 and drawing else ''}"), None

    # ---- facing a bet
    allin = stack is not None and call >= stack
    margin = eq - req
    fold_line = {"folds": 0.01, "calls": -0.04}.get(opp_type, -0.02)   # tighter vs a player who rarely bluffs
    if margin < fold_line:
        deep = stack is not None and stack >= 6 * call
        if drawing and deep and street < 5 and margin >= -0.08:
            return "CALL", (f"Slightly short ({eq:.0%} vs {req:.0%}), but {outs} outs and deep stacks: implied odds"), None
        return "FOLD", f"{eq:.0%} equity, you need {req:.0%}: fold", None
    if not allin:
        raise_to = call * (TUNE["raise_x"] if edge >= 0 else TUNE["raise_x"] + 0.5)
        commits = stack is not None and raise_to >= 0.6 * stack
        size = _size(raise_to, pot, stack) if not commits else f"all-in ({_chips(stack):,})"
        if wet and hold.get("made") == "Pair" and eq >= strong + 0.05:
            return "CALL", (f"{eq:.0%} equity vs {req:.0%} needed. One pair on a straight/flush board: call, don't raise"), None
        if eq >= strong + TUNE["raise_margin"]:
            return "RAISE", (f"{eq:.0%} equity vs {req:.0%} needed: raise for value" +
                             (" (short stacks, get it in)" if spr is not None and spr < 3 else "")), size
        if combo and opps == 1 and opp_type != "calls":
            return "RAISE", (f"semi-bluff raise: {' + '.join(draws)} ({outs} outs)"), size
    note = ""
    if spr is not None and spr > 6 and hold.get("made") in ("Pair", "High Card") and call > 0.5 * pot:
        note = " Deep stacks, one pair, big bet: be careful."
    if margin <= 0.02:
        return "CALL", f"Close call: {eq:.0%} equity vs {req:.0%} needed.{note}", None
    return "CALL", f"{eq:.0%} equity vs {req:.0%} needed: call.{note}", None
