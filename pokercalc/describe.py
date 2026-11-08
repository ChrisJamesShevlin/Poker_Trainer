"""Plain-English hand description, outs, and the rule of 2 and 4."""
from collections import Counter

import eval7

from .equity import FULL_DECK
from .parser import RANKS

CATS = ["High Card", "Pair", "Two Pair", "Trips", "Straight", "Flush",
        "Full House", "Quads", "Straight Flush"]
NAMES = {"2": "deuce", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
         "8": "eight", "9": "nine", "T": "ten", "J": "jack", "Q": "queen", "K": "king", "A": "ace"}


def _cat(cards):
    return CATS.index(eval7.handtype(eval7.evaluate([eval7.Card(c) for c in cards])))


def _pair_text(hole, board):
    rb = sorted((RANKS.index(c[0]) for c in board), reverse=True)
    hr = [RANKS.index(c[0]) for c in hole]
    if hr[0] == hr[1]:
        if hr[0] > rb[0]:
            return "overpair"
        return "pocket pair below the top board card"
    mine = [r for r in hr if r in rb]
    if not mine:
        return "pair on the board only (you have no pair of your own)"
    r = mine[0]
    uniq = sorted(set(rb), reverse=True)
    pos = uniq.index(r)
    label = "top pair" if pos == 0 else "bottom pair" if r == uniq[-1] else "middle pair"
    kicker = NAMES[RANKS[max(h for h in hr if h != r)]]
    return f"{label} ({NAMES[RANKS[r]]}s, {kicker} kicker)"


def _straight_draw_ranks(hole, board):
    """Ranks that would complete a straight using at least one hole card."""
    have = {RANKS.index(c[0]) + 2 for c in hole + board}
    hole_r = {RANKS.index(c[0]) + 2 for c in hole}
    if 14 in have:
        have.add(1)
        if 14 in hole_r:
            hole_r.add(1)
    out = set()
    for r in range(2, 15):
        if r in have:
            continue
        s = have | {r} | ({1} if r == 14 else set())
        for low in range(1, 11):
            win = set(range(low, low + 5))
            if win <= s and r in win and win & hole_r:
                out.add(r)
    return out


def analyse(hole, board):
    """Returns dict: text, outs (int), outs_note, draws list."""
    if len(board) < 3:
        return {"text": "Preflop", "outs": 0, "draws": [], "made": None}
    cards = hole + board
    made = CATS[_cat(cards)]
    parts = []
    if made == "Pair":
        parts.append(_pair_text(hole, board))
    elif made == "High Card":
        parts.append("high card (no pair)")
    else:
        note = " (board plays)" if len(board) == 5 and _cat(board) == _cat(cards) else ""
        parts.append(made.lower() + note)
    draws = []
    if len(board) < 5 and made not in ("Straight", "Flush", "Full House", "Quads", "Straight Flush"):
        suits = Counter(c[1] for c in cards)
        for s, n in suits.items():
            mine = sum(1 for c in hole if c[1] == s)
            if n == 4 and mine:
                draws.append("flush draw")
            elif n == 3 and mine and len(board) == 3:
                draws.append("backdoor flush draw")
        sr = _straight_draw_ranks(hole, board)
        if len(sr) >= 2:
            draws.append("open-ended straight draw")
        elif len(sr) == 1:
            draws.append("gutshot straight draw")
    parts += draws
    text = ", ".join(parts)

    outs = 0
    if len(board) < 5:
        base = _cat(cards)
        dead = set(cards)
        for c in FULL_DECK:
            if c in dead:
                continue
            new = _cat(cards + [c])
            if new <= base or new < 2:
                continue  # only count real improvements (two pair or better)
            if new == 2 and c[0] not in (hole[0][0], hole[1][0]):
                continue  # two pair made only via the board's cards is weak
            if len(board) >= 4 and _cat(board + [c]) >= new:
                continue  # the board alone would give everyone this hand
            outs += 1
    return {"text": text, "outs": outs, "draws": draws, "made": made}


def rule_of_2_and_4(outs, board_len):
    """Returns (approx_equity_fraction, explanation) or None on river/preflop."""
    if board_len == 3:
        return min(outs * 4, 100) / 100, f"{outs} outs x 4 = ~{min(outs*4,100)}% (flop, two cards to come)"
    if board_len == 4:
        return min(outs * 2, 100) / 100, f"{outs} outs x 2 = ~{min(outs*2,100)}% (turn, one card to come)"
    return None


# ---------- plain-English "what do I hold" ----------
PLURAL = {"2": "Deuces", "3": "Threes", "4": "Fours", "5": "Fives", "6": "Sixes", "7": "Sevens",
          "8": "Eights", "9": "Nines", "T": "Tens", "J": "Jacks", "Q": "Queens", "K": "Kings", "A": "Aces"}
SINGLE = {k: v.capitalize() for k, v in NAMES.items()}


def _straight_high(ranks):
    """Highest straight top-rank (RANKS index) in a set of rank indexes, else None. Wheel = five high."""
    s = set(ranks)
    if 12 in s:
        s.add(-1)
    for top in range(12, 2, -1):
        if all(top - i in s for i in range(5)):
            return top
    return None


def made_hand_name(cards):
    """Name of the best 5-card hand in `cards`, e.g. 'Flush, Ace high'."""
    cat = CATS[_cat(cards)]
    rk = [RANKS.index(c[0]) for c in cards]
    cnt = Counter(rk)
    byc = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    name = lambda i: SINGLE[RANKS[i]]
    plural = lambda i: PLURAL[RANKS[i]]
    suits = Counter(c[1] for c in cards)
    if cat == "Straight Flush":
        fs = [s for s, n in suits.items() if n >= 5][0]
        top = _straight_high([RANKS.index(c[0]) for c in cards if c[1] == fs])
        return "Royal flush" if top == 12 else f"Straight flush, {name(top)} high"
    if cat == "Quads":
        return f"Four of a kind, {plural(byc[0][0])}"
    if cat == "Full House":
        trips = max(r for r, n in cnt.items() if n == 3)
        pair = max(r for r, n in cnt.items() if n >= 2 and r != trips)
        return f"Full house, {plural(trips)} full of {plural(pair)}"
    if cat == "Flush":
        fs = [s for s, n in suits.items() if n >= 5][0]
        top = max(RANKS.index(c[0]) for c in cards if c[1] == fs)
        return f"Flush, {name(top)} high"
    if cat == "Straight":
        top = _straight_high(rk)
        return f"Straight, {'Five' if top == 3 else name(top)} high"
    if cat == "Trips":
        return f"Three of a kind, {plural(byc[0][0])}"
    if cat == "Two Pair":
        pairs = sorted((r for r, n in cnt.items() if n == 2), reverse=True)[:2]
        return f"Two pair, {plural(pairs[0])} and {plural(pairs[1])}"
    if cat == "Pair":
        return f"Pair of {plural(byc[0][0])}"
    return f"High card, {name(max(rk))}"


def board_texture(board):
    notes = []
    suits = Counter(c[1] for c in board)
    s, n = suits.most_common(1)[0]
    sym = {"s": "spades", "h": "hearts", "d": "diamonds", "c": "clubs"}[s]
    if n >= 5:
        notes.append(f"5 {sym} on board - anyone can have a flush")
    elif n == 4:
        notes.append(f"4 {sym} on board - any {sym[:-1]} makes a flush")
    elif n == 3:
        notes.append(f"3 {sym} on board - flush possible")
    cnt = Counter(c[0] for c in board)
    top = cnt.most_common(1)[0][1]
    if top >= 3:
        notes.append("trips on board")
    elif top == 2:
        notes.append("paired board")
    rk = sorted({RANKS.index(c[0]) for c in board} | ({-1} if "A" in cnt else set()))
    for lo in range(-1, 9):
        if sum(1 for r in rk if lo <= r <= lo + 4) >= 3:
            notes.append("straight possible" if len(board) >= 3 else "")
            break
    return [x for x in notes if x]


def hold_summary(hole, board):
    """Everything for the 'You hold' panel."""
    if not board:
        return None
    cards = hole + board
    info = analyse(hole, board)
    title = made_hand_name(cards)
    if info["made"] == "Pair":
        title += " - " + _pair_text(hole, board).split(" (")[0]
    elif info["made"] == "Trips":
        if hole[0][0] == hole[1][0]:
            title += " (set)"
    if len(board) == 5 and _cat(board) == _cat(cards) and \
            eval7.evaluate([eval7.Card(c) for c in board]) == eval7.evaluate([eval7.Card(c) for c in cards]):
        title += " - the board plays"
    # rank against every possible two-card holding
    dead = set(cards)
    left = [c for c in FULL_DECK if c not in dead]
    me = eval7.evaluate([eval7.Card(c) for c in cards])
    bd = [eval7.Card(c) for c in board]
    beat = tie = total = 0
    best, best_cards = -1, None
    for i in range(len(left)):
        for j in range(i + 1, len(left)):
            sc = eval7.evaluate([eval7.Card(left[i]), eval7.Card(left[j])] + bd)
            total += 1
            beat += me > sc
            tie += me == sc
            if sc > best:
                best, best_cards = sc, [left[i], left[j]]
    ahead = (beat + tie / 2) / total
    if me >= best:
        nuts = "You have the best possible hand right now."
    else:
        nuts = f"Best possible hand right now: {made_hand_name(best_cards + board)}."
    return {"made": info["made"], "title": title, "draws": info["draws"], "outs": info["outs"],
            "texture": board_texture(board), "ahead": ahead, "nuts": nuts}
