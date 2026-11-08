"""Glue: one evaluate() call used by the web UI."""
from . import advice, describe, preflop
from .equity import equity
from .oprange import resolve
from .parser import Query
from .potodds import pot_odds, verdict


def _wet(board):
    t = " ".join(describe.board_texture(board)) if len(board) >= 3 else ""
    return "flush" in t or "straight" in t


def _preflop_sizing(position, facing, tier, call, bb, stack, rinfo):
    """How much to raise TO. Open 2.5bb (3bb from the blinds/early seats); 3-bet ~3x in position, ~4x out of it."""
    if tier == "shove" or (stack and bb and stack / bb < 25):
        return f"all-in ({advice.fmt_amount(stack)})" if stack else "all-in"
    oop = position in ("SB", "BB")
    if not facing:
        mult = 3.0 if position in ("UTG", "SB") else 2.5
        return f"raise to ~{advice.fmt_amount(mult * bb)} ({mult:g}bb)" if bb else f"raise to ~{mult:g}x the big blind"
    mult = 4.0 if oop else 3.0
    if tier == "big":
        mult = 2.2   # 4-bet
    base = (call + (bb / 2 if position == "SB" else bb if position == "BB" else 0)) if bb else call
    to = base * mult
    if stack and to >= stack:
        return f"all-in ({advice.fmt_amount(stack)})"
    return f"raise to ~{advice.fmt_amount(to)} (about {mult:g}x their raise)"


def _evaluate(d, sims=20000):
    hole, board = d["hole"], d["board"]
    pot, call = float(d.get("pot") or 0), float(d.get("call") or 0)
    opps = int(d.get("opps") or 1)
    stack = float(d["stack"]) if d.get("stack") else None
    bb = float(d["bb"]) if d.get("bb") else None
    position = d.get("position") or "BTN"
    opp_type = d.get("opp_type") if d.get("opp_type") in advice.OPP_TYPES else "normal"

    # Preflop: the raise size is worked out from 'bet to call' alone
    rinfo = None if board else preflop.raise_info(call, bb, stack, position)
    facing = d.get("facing") is True
    limp = d.get("facing") == "limp" or bool(rinfo and bb and rinfo["tier"] == "limp")
    if not board and bb and not facing and not limp and (not rinfo or rinfo["tier"] in ("none", "limp")):
        limp = round((pot - 1.5 * bb) / bb) >= 1      # the blinds are 1.5bb; every extra bb in the pot is a limper
    if rinfo and (bb or rinfo["tier"] == "shove"):
        facing = rinfo["facing"]          # detected, so the toggle isn't needed
    tier = rinfo["tier"] if rinfo and facing else "normal"
    steps = {"big": 1, "shove": 2}.get(tier, 0)

    mode = d.get("range_mode", d.get("range_pct") or 0)
    classes, rlabel, rpct, pct = None, "Random hands", None, None
    if mode == "auto":
        classes, rlabel, rpct = resolve(pot, call, len(board), d.get("raiser") or None,
                                        d.get("table") or "normal", bool(d.get("multi_street")),
                                        extra_steps=steps, wet=_wet(board))
        rlabel = "Auto: " + rlabel
    elif mode:
        pct = int(mode)
        rlabel, rpct = f"Top {pct}% (you chose)", pct
    win, tie, eq = equity(hole, board, opps, pct, sims=sims, range_classes=classes)
    req, ratio = pot_odds(pot, call)
    out = {"equity": eq, "win": win, "tie": tie, "required": req, "ratio": ratio,
           "verdict": verdict(eq, pot, call) if call > 0 else None,
           "hold": None, "outs": None, "rule24": None, "preflop": None,
           "stack_line": None, "random_warning": mode == 0 or mode is None,
           "range_label": rlabel, "range_pct_used": rpct}
    if stack:
        out["stack_line"] = (f"Stack {advice.fmt_amount(stack)}" + (f" ({stack/bb:.0f}bb)" if bb else "") +
                             (f" - SPR {stack/pot:.1f}" if pot else "") +
                             (" - call puts you all-in" if call and call >= stack else ""))
    action, why = advice.suggest(eq, pot, call, opps, stack)
    sizing = None
    if board:
        info = describe.analyse(hole, board)
        out["hold"] = describe.hold_summary(hole, board)
        if info["outs"]:
            out["outs"] = info["outs"]
            r = describe.rule_of_2_and_4(info["outs"], len(board))
            out["rule24"] = r[1] if r else None
        action, why, sizing = advice.suggest_postflop(eq, pot, call, opps, stack, position, board, out["hold"], opp_type)
    else:
        g = preflop.advise(hole, position, stack / bb if stack and bb else None, facing, tier,
                           rinfo["raise_bb"] if rinfo else None, d.get("raiser") or None,
                           limpers=limp and not facing, opps=opps)
        out["preflop"] = {"text": g["text"], "why": g["why"]}
        if rinfo and rinfo["tier"] != "none":
            names = {"limp": "limped pot (no raise)", "normal": "normal-size raise",
                     "big": "big raise (likely a 3-bet)", "shove": "huge raise or shove"}
            out["preflop"]["size"] = (f"~{rinfo['raise_bb']:.1f}bb - " if rinfo["raise_bb"] else "") + names[rinfo["tier"]]
        out["hold"] = {"title": f"{g['class']} - {preflop.describe_class(g['class'])}",
                       "tier": f"{preflop.tier(g['pct'])} hand, rank {g['rank']}/169 (top {g['pct']:.0f}%)",
                       "draws": [], "texture": [], "nuts": None, "ahead": None, "outs": None}
        action, why = g["action"], g["why"]
        if action == "RAISE" and limp and not facing:
            sizing = (f"raise to ~{advice.fmt_amount(3.5 * bb)} (3.5bb) plus {advice.fmt_amount(bb)} (1bb) for every limper" if bb
                      else "raise to ~3.5x the big blind, plus 1 big blind for every limper")
        elif action == "RAISE":
            sizing = _preflop_sizing(position, facing, tier, call, bb, stack, rinfo)
        if call > 0 and action == "CALL" and eq < req - 0.02:
            out["preflop"]["note"] = f"Guide says call, but pot odds need {req:.0%} and you have {eq:.0%}."
    out["action"], out["why"], out["sizing"] = action, why, sizing
    out["bluff"] = advice.bluff_kind(action, why) if board else None
    out["fold_equity"] = None
    chips = getattr(sizing, "chips", None)
    if board and action in ("BET", "RAISE") and chips:
        pure, with_eq = advice.fold_math(pot, chips, eq)
        if pure is not None:
            out["fold_equity"] = (f"Your bet or raise of {advice.fmt_amount(chips)} into a pot of {advice.fmt_amount(pot)} needs them to fold {pure:.0%} of the time to profit as a pure bluff. "
                                  + (f"With your {eq:.0%} equity when called, it needs only {with_eq:.0%}. " if with_eq else
                                     f"With your {eq:.0%} equity when called, it profits even if they never fold. ")
                                  + "(Rough guide: your equity is against the whole range you picked.)")
    return out


def evaluate(d, sims=20000):
    """One calculation. Dollar-sized games (big blind under 1) show amounts with cents."""
    bb = float(d["bb"]) if d.get("bb") else None
    token = advice.set_small_units(bb is not None and bb < 1)
    try:
        return _evaluate(d, sims=sims)
    finally:
        advice.reset_small_units(token)
