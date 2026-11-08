"""Stats over logged sessions/hands. Deposits (top-ups) are never counted as winnings.

Money results add up your real winnings, but they are not comparable across stakes (a 10 loss at a 1 big blind
table is smaller than a 5 win at a 0.05 big blind table). So every money figure also comes in big blinds ("_bb"),
and summary(con, stake=...) can look at one stake only (stake = the big blind of the session).
"""
import json
import time

WIN_WINDOW = 20      # hands in the rolling win-rate window
CALL_WINDOW = 10     # calls in the rolling equity window
POSITIONS = ["UTG", "HJ", "CO", "BTN", "SB", "BB"]
STREETS = {0: "Preflop", 3: "Flop", 4: "Turn", 5: "River"}


def _rolling(vals, w, start=1):
    out = []
    for i in range(len(vals)):
        win = vals[max(0, i + 1 - w):i + 1]
        out.append(sum(win) / len(win) if i + 1 >= start else None)
    return out


def _label(ts):
    return time.strftime("%d %b", time.localtime(ts)) if ts else ""


def _pl(x):
    return x["balance_after"] - x["balance_before"] - (x["deposit_during"] or 0)


def _decisions(h):
    try:
        return json.loads(h["decisions"]) if h["decisions"] else []
    except (ValueError, TypeError):
        return []


def _voluntary(h):
    """Did you put money in before the flop? From your first preflop decision when it was recorded; for older hands, a hand
    that saw a flop counts, and one that ended before it counts if your last action was a call or raise."""
    pre = [d for d in _decisions(h) if d.get("street") == 0]
    if pre:
        return pre[-1].get("taken") in ("CALL", "RAISE", "BET") or any(d.get("taken") in ("CALL", "RAISE", "BET") for d in pre)
    if h["board"]:
        return True
    return h["taken"] in ("CALL", "RAISE", "BET")


MISMATCH_BB = 100      # a session whose balance result and hand results differ by more than this many big blinds is flagged


def session_mismatches(done, hands):
    """Sessions where the balances you typed and the hands you logged tell different stories (usually a typo in a balance)."""
    out = []
    for x in done:
        if not x["bb"]:
            continue
        hs = [h for h in hands if h["session_id"] == x["id"] and h["result"] is not None]
        if not hs:
            continue
        pl_bb, hands_bb = _pl(x) / x["bb"], sum(h["result"] for h in hs) / x["bb"]
        if abs(pl_bb - hands_bb) > MISMATCH_BB:
            out.append({"label": _label(x["end_ts"]), "session_bb": round(pl_bb, 1), "hands_bb": round(hands_bb, 1),
                        "balance_before": x["balance_before"], "balance_after": x["balance_after"], "buy_in": x["buy_in"]})
    return out


def summary(con, stake=None):
    s = {}
    sess = con.execute("SELECT * FROM sessions ORDER BY id").fetchall()
    hands = con.execute("SELECT h.*, s.bb AS bb FROM hands h LEFT JOIN sessions s ON s.id=h.session_id "
                        "ORDER BY h.ts").fetchall()
    by_stake = {}                                  # every stake you have played (before any filter)
    for x in sess:
        if x["bb"]:
            by_stake.setdefault(round(x["bb"], 6), {"bb": x["bb"], "sessions": 0, "hands": 0})["sessions"] += 1
    for h in hands:
        if h["bb"]:
            by_stake.setdefault(round(h["bb"], 6), {"bb": h["bb"], "sessions": 0, "hands": 0})["hands"] += 1
    s["stakes"] = sorted(by_stake.values(), key=lambda d: d["bb"])
    s["stake"] = stake
    if stake is not None:                          # one stake only
        near = lambda v: v is not None and abs(v - stake) < 1e-9
        sess = [x for x in sess if near(x["bb"])]
        hands = [h for h in hands if near(h["bb"])]
    done = [x for x in sess if x["balance_after"] is not None]
    s["sessions"] = len(sess)
    s["hands"] = len(hands)

    # ---- balance, with deposits separated from results
    s["session_labels"] = [_label(x["end_ts"]) for x in done]
    s["session_pl"] = [_pl(x) for x in done]
    s["session_pl_bb"] = [(_pl(x) / x["bb"]) if x["bb"] else None for x in done]
    s["mismatches"] = session_mismatches(done, hands)
    s["total_pl"] = sum(s["session_pl"])
    s["balance_labels"], s["balance_points"], s["balance_ex_deposits"], s["deposits"] = [], [], [], []
    s["total_deposits"] = 0.0
    s["winnings_labels"], s["winnings_chips"], s["winnings_bb"] = [], [], []
    if done:
        start = done[0]["balance_before"]
        s["balance_labels"] = ["Start"] + s["session_labels"]
        s["balance_points"] = [start] + [x["balance_after"] for x in done]
        run, ex = 0.0, [start]
        for i, x in enumerate(done):
            run += _pl(x)
            ex.append(start + run)
        s["balance_ex_deposits"] = ex
        # deposit between sessions = jump from last balance_after to this balance_before
        dep = [0.0]
        for i, x in enumerate(done):
            before = (x["deposit_before"] or 0) if i else 0
            dep.append(before + (x["deposit_during"] or 0))
        s["deposits"] = dep
        s["total_deposits"] = sum(dep)
        # total winnings: running sum of each session's own result, so which platform you played on never matters
        s["winnings_labels"] = ["Start"] + s["session_labels"]
        s["winnings_chips"] = [0.0]
        s["winnings_bb"] = [0.0]
        for x, pl in zip(done, s["session_pl"]):
            s["winnings_chips"].append(s["winnings_chips"][-1] + pl)
            s["winnings_bb"].append(s["winnings_bb"][-1] + (pl / x["bb"] if x["bb"] else 0))

    # ---- per-hand results and rolling win rate
    res = [h for h in hands if h["result"] is not None]
    cum, tot = [], 0.0
    for h in res:
        tot += h["result"]
        cum.append(tot)
    s["cum_results"] = cum
    cum_bb, tb = [], 0.0                           # the same running result in big blinds, one point per hand
    for h in res:
        if h["bb"]:                                # (a hand with no big blind recorded adds nothing but keeps its place)
            tb += h["result"] / h["bb"]
        cum_bb.append(tb)
    s["cum_results_bb"] = cum_bb
    s["hand_results_total"] = tot
    wins = [1.0 if h["result"] > 0 else 0.0 for h in res]
    s["win_rate"] = sum(wins) / len(wins) if wins else None
    s["win_rate_roll"] = [None if v is None else 100 * v for v in _rolling(wins, WIN_WINDOW, start=5)]
    bbs = [h["result"] / h["bb"] for h in res if h["bb"]]
    s["bb_per_100"] = 100 * sum(bbs) / len(bbs) if bbs else None

    # ---- preflop / decisions
    # VPIP = the share of hands you put money in voluntarily before the flop. Every saved hand is one preflop spot, including
    # the ones that reached the flop (those used to be left out, which made VPIP far too low).
    s["vpip"] = (sum(1 for h in hands if _voluntary(h)) / len(hands)) if hands else None
    calls = [h for h in hands if h["taken"] == "CALL" and h["call"] and h["equity"] is not None]
    s["call_margins"] = [h["equity"] - h["required"] for h in calls]
    s["call_equity"] = [h["equity"] for h in calls]
    s["call_required"] = [h["required"] for h in calls]
    s["call_labels"] = [f"{h['hole']} | {h['board'] or 'preflop'}" for h in calls]
    s["bad_calls"] = sum(1 for m in s["call_margins"] if m < -0.02)
    s["good_call_rate"] = (1 - s["bad_calls"] / len(calls)) if calls else None
    evs = [h["equity"] * (h["pot"] + h["call"]) - h["call"] for h in calls]
    s["ev_calls"] = sum(evs)
    s["ev_bad_calls"] = sum(e for e, m in zip(evs, s["call_margins"]) if m < -0.02)
    evs_bb = [(e / h["bb"]) if h["bb"] else 0.0 for e, h in zip(evs, calls)]
    s["ev_calls_bb"] = sum(evs_bb)
    s["ev_bad_calls_bb"] = sum(e for e, m in zip(evs_bb, s["call_margins"]) if m < -0.02)
    pct = lambda vals: [None if v is None else 100 * v for v in vals]
    s["call_equity_roll"] = pct(_rolling([h["equity"] for h in calls], CALL_WINDOW, start=3))
    s["call_required_roll"] = pct(_rolling([h["required"] for h in calls], CALL_WINDOW, start=3))
    # following the advice is judged per decision (one per button press); hands saved before decisions were
    # recorded only kept the last guess, so they are left out
    decs = {h["id"]: [d for d in _decisions(h) if d.get("suggested") is not None] for h in hands}      # (Casino-only hands have no advice to follow)
    flat = [d for ds in decs.values() for d in ds]
    s["decisions_total"] = len(flat)
    s["follow_rate"] = (sum(1 for d in flat if d["suggested"] == d["taken"]) / len(flat)) if flat else None
    cnt = {}
    for h in hands:
        if h["taken"]:
            cnt[h["taken"]] = cnt.get(h["taken"], 0) + 1
    s["action_counts"] = cnt

    # ---- per session
    per = []
    for x in done:
        hs = [h for h in res if h["session_id"] == x["id"]]
        b = [h["result"] / h["bb"] for h in hs if h["bb"]]
        per.append({"label": _label(x["end_ts"]), "pl": _pl(x),
                    "hands": sum(1 for h in hands if h["session_id"] == x["id"]),
                    "bb100": 100 * sum(b) / len(b) if b else None,
                    "win_rate": (sum(1 for h in hs if h["result"] > 0) / len(hs)) if hs else None})
    s["per_session"] = per

    # ---- where you win / lose
    blank = lambda: {"hands": 0, "net": 0.0, "hands_bb": 0, "net_bb": 0.0}
    pos = {p: blank() for p in POSITIONS}
    st = {n: blank() for n in STREETS.values()}

    def add(bucket, h):
        bucket["hands"] += 1
        bucket["net"] += h["result"]
        if h["bb"]:
            bucket["hands_bb"] += 1
            bucket["net_bb"] += h["result"] / h["bb"]
    for h in res:
        if h["position"] in pos:
            add(pos[h["position"]], h)
        n = len(h["board"].split()) if h["board"] else 0
        if n in STREETS:
            add(st[STREETS[n]], h)
    adv = {"Followed the suggestion": [], "Did something else": []}
    for h in res:
        ds = decs[h["id"]]
        if ds:   # a hand counts as followed only if every decision in it matched the suggestion
            adv["Followed the suggestion" if all(d["suggested"] == d["taken"] for d in ds) else "Did something else"].append(h)
    s["advice_split"] = []
    for k, hs in adv.items():
        if not hs:
            continue
        v = [h["result"] for h in hs]
        vb = [h["result"] / h["bb"] for h in hs if h["bb"]]
        s["advice_split"].append({"name": k, "hands": len(v), "net": sum(v), "avg": sum(v) / len(v),
                                  "hands_bb": len(vb), "net_bb": sum(vb), "avg_bb": (sum(vb) / len(vb)) if vb else None})
    s["by_position"] = [{"name": k, **v} for k, v in pos.items() if v["hands"]]
    s["by_street"] = [{"name": k, **v} for k, v in st.items() if v["hands"]]
    return s
