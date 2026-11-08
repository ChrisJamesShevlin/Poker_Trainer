"""A 6-max No-Limit Hold'em hand, played to the end. Play money only; no rake.

Chips are whole numbers (default blinds 1/2, stacks 200 = 100bb). Seats are 0..n-1; the button sits on `button`,
the small blind is the next seat, then the big blind, then UTG, HJ, CO (positions below).

    h = Hand(stacks=[200]*6, button=0, seed=1)
    while not h.over:
        seat = h.to_act
        legal = h.legal(seat)             # {'to_call', 'can_check', 'can_raise', 'min_raise_to', 'max_raise_to', ...}
        h.act(seat, "call")               # "fold" | "check" | "call" | ("raise", total_bet_this_street)
    h.result                              # who won what; chips are conserved

No-limit rule: you may raise to any amount from the minimum up to all-in. The smallest raise is the size of the last
raise (at least one big blind). An all-in for less than a full raise is allowed but does not let players who already
acted raise again.
"""
import itertools
import random

import eval7

from .equity import FULL_DECK

_CARDS = {c: eval7.Card(c) for c in FULL_DECK}

POSITIONS = ["BTN", "SB", "BB", "UTG", "HJ", "CO"]     # seat offset from the button, 6-max


_NAMES_BY_COUNT = {2: ["SB", "BB"], 3: ["BTN", "SB", "BB"], 4: ["BTN", "SB", "BB", "CO"], 5: ["BTN", "SB", "BB", "UTG", "CO"],
                   6: POSITIONS}          # the position names when only that many players are still in (tournament endgames)


def position_name(seat, button, n=6):
    if n == 6:
        return POSITIONS[(seat - button) % n]
    return ["BTN", "SB", "BB"][(seat - button) % n] if (seat - button) % n < 3 else "MID"


class IllegalAction(ValueError):
    pass


class Hand:
    def __init__(self, stacks, button=0, sb=1, bb=2, seed=None, holes=None, board=None, out=(), ante=0):
        n = len(stacks)
        if n < 3:
            raise ValueError("need at least 3 seats")
        self.out = set(out)                    # seats with no chips left (busted in a tournament): they are not dealt in
        if n - len(self.out) < 2:
            raise ValueError("need at least 2 players with chips")
        self.n, self.sb, self.bb, self.button = n, sb, bb, button
        self.ante = ante                       # a tournament ante: every player still in puts this in before the blinds (it is not part of any bet)
        if holes is None:
            deck = FULL_DECK[:]
            random.Random(seed).shuffle(deck)
            holes = [deck[2 * i:2 * i + 2] for i in range(n)]
            board = deck[2 * n:2 * n + 5]
        self.hole = [list(h) for h in holes]
        self.full_board = list(board)
        self.start_stack = list(stacks)
        self.stack = list(stacks)
        self.committed = [0] * n       # chips put in on the current street
        self.total = [0] * n           # chips put in over the whole hand
        self.folded = [i in self.out for i in range(n)]
        self.allin = [False] * n
        self.board = []
        self.street = "preflop"
        self.over = False
        self.result = None
        self.log = []                  # (street, seat, action, amount_to)
        self.current_bet = 0
        self.last_raise = bb
        self.acted = set()
        self.no_raise = set()
        self.aggressor = None          # seat of the last bettor/raiser this street
        self.pf_aggressor = None       # seat of the last preflop raiser (the one who may continuation-bet)
        self.raises_preflop = 0
        self.to_act = None
        if n - len(self.out) == 2:                 # heads-up: the button is the small blind and acts first before the flop
            sbs = button
            bbs = self._seat_after(button)
        else:
            sbs = self._seat_after(button)
            bbs = self._seat_after(sbs)
        self.sb_seat, self.bb_seat = sbs, bbs
        if ante:
            for s in range(n):
                if s not in self.out:
                    self._ante(s, ante)
        self._post(sbs, sb)
        self._post(bbs, bb)
        self.current_bet = bb
        self.to_act = self._next(bbs)
        if self.to_act is None:
            self._advance()

    # ------------------------------------------------------------------ helpers
    def pot(self):
        return sum(self.total)

    def _seat_after(self, seat):
        """The next seat round the table that still has chips."""
        for i in range(1, self.n + 1):
            s = (seat + i) % self.n
            if s not in self.out:
                return s

    def position(self, seat):
        if not self.out:
            return position_name(seat, self.button, self.n)
        if seat in self.out:
            return "OUT"
        order = [s for s in ((self.button + i) % self.n for i in range(self.n)) if s not in self.out]
        return _NAMES_BY_COUNT[len(order)][order.index(seat)]

    def active(self):
        """Seats still in the hand."""
        return [s for s in range(self.n) if not self.folded[s]]

    def _ante(self, seat, amount):
        """Dead money: it goes in the pot but does not count towards what you have bet this street, so nobody has to call it."""
        amt = min(amount, self.stack[seat])
        self.stack[seat] -= amt
        self.total[seat] += amt
        if self.stack[seat] == 0:
            self.allin[seat] = True

    def _post(self, seat, amount):
        amt = min(amount, self.stack[seat])
        self.stack[seat] -= amt
        self.committed[seat] += amt
        self.total[seat] += amt
        if self.stack[seat] == 0:
            self.allin[seat] = True

    def _needs_action(self, s):
        return (not self.folded[s] and not self.allin[s]
                and (s not in self.acted or self.committed[s] < self.current_bet))

    def _next(self, after):
        for i in range(1, self.n + 1):
            s = (after + i) % self.n
            if self._needs_action(s):
                return s
        return None

    # ------------------------------------------------------------------ legal actions
    def legal(self, seat):
        if self.over or seat != self.to_act:
            raise IllegalAction("not this seat's turn")
        to_call = max(0, self.current_bet - self.committed[seat])
        stack = self.stack[seat]
        pot = self.pot()
        all_in_to = self.committed[seat] + stack
        max_to = all_in_to                                            # no limit: up to everything you have
        min_to = self.current_bet + self.last_raise if self.current_bet else self.bb
        can_raise = stack > to_call and seat not in self.no_raise
        return {"to_call": min(to_call, stack), "can_check": to_call == 0,
                "can_raise": can_raise, "min_raise_to": min(min_to, all_in_to),
                "max_raise_to": min(max_to, all_in_to), "all_in_to": all_in_to, "pot": pot,
                "stack": stack, "current_bet": self.current_bet}

    # ------------------------------------------------------------------ acting
    def act(self, seat, action):
        L = self.legal(seat)
        kind, amount = (action if isinstance(action, tuple) else (action, None))
        if kind == "fold":
            if L["can_check"]:
                raise IllegalAction("cannot fold when you can check")
            self.folded[seat] = True
            self.acted.add(seat)
        elif kind == "check":
            if not L["can_check"]:
                raise IllegalAction("cannot check facing a bet")
            self.acted.add(seat)
        elif kind == "call":
            if L["can_check"]:
                raise IllegalAction("nothing to call")
            self._post(seat, L["to_call"])
            self.acted.add(seat)
        elif kind == "raise":
            if not L["can_raise"]:
                raise IllegalAction("cannot raise")
            if amount is None or not (L["min_raise_to"] <= amount <= L["max_raise_to"]):
                raise IllegalAction(f"raise to {amount} outside {L['min_raise_to']}..{L['max_raise_to']}")
            increment = amount - self.current_bet
            full = increment >= self.last_raise
            self._post(seat, amount - self.committed[seat])
            if full:
                self.last_raise = max(increment, self.bb)
                self.no_raise = set()
                self.acted = {seat}
            else:                                              # short all-in: others must respond but cannot re-raise
                self.no_raise |= set(self.acted)
                self.acted.add(seat)
            self.current_bet = amount
            self.aggressor = seat
            if self.street == "preflop":
                self.raises_preflop += 1
        else:
            raise IllegalAction(f"unknown action {kind!r}")
        self.log.append((self.street, seat, kind, amount if kind == "raise" else None))
        self._after_action(seat)

    def _after_action(self, seat):
        live = self.active()
        if len(live) == 1:
            self._finish_uncontested(live[0])
            return
        nxt = self._next(seat)
        if nxt is not None:
            self.to_act = nxt
            return
        self._advance()

    def _advance(self):
        """The betting round is over: deal the next street, or run out the board, or showdown."""
        while True:
            live = self.active()
            if self.street == "river":
                self._showdown()
                return
            if self.street == "preflop":
                self.pf_aggressor = self.aggressor
            self.street = {"preflop": "flop", "flop": "turn", "turn": "river"}[self.street]
            self.board = self.full_board[:{"flop": 3, "turn": 4, "river": 5}[self.street]]
            self.committed = [0] * self.n
            self.current_bet, self.last_raise = 0, self.bb
            self.acted, self.no_raise, self.aggressor = set(), set(), None
            can_bet = [s for s in live if not self.allin[s]]
            if len(can_bet) >= 2:
                self.to_act = self._next(self.button)
                return
            # nobody (or only one player) can bet any more: deal the rest of the board

    # ------------------------------------------------------------------ results
    def _finish_uncontested(self, winner):
        pot = self.pot()
        net = [-t for t in self.total]
        net[winner] += pot
        self.stack = [self.start_stack[i] + net[i] for i in range(self.n)]
        self.over, self.to_act = True, None
        self.result = {"winners": [winner], "net": net, "pot": pot, "showdown": False, "board": list(self.board),
                       "won": [pot if i == winner else 0 for i in range(self.n)]}

    def _showdown(self):
        self.board = list(self.full_board)
        bd = [_CARDS[c] for c in self.board]
        contenders = self.active()
        value = {s: eval7.evaluate([_CARDS[c] for c in self.hole[s]] + bd) for s in contenders}
        levels = sorted({self.total[s] for s in contenders})
        won = [0] * self.n
        prev, allocated = 0, 0
        layers = []
        for lvl in levels:
            amount = sum(min(t, lvl) - min(t, prev) for t in self.total)
            eligible = [s for s in contenders if self.total[s] >= lvl]
            layers.append([amount, eligible])
            allocated += amount
            prev = lvl
        if allocated < self.pot():                                # chips above every contender's level (rare)
            layers[-1][0] += self.pot() - allocated
        winners_all = set()
        for amount, eligible in layers:
            best = max(value[s] for s in eligible)
            winners = [s for s in eligible if value[s] == best]
            winners_all.update(winners)
            share, extra = divmod(amount, len(winners))
            order = sorted(winners, key=lambda s: (s - self.button - 1) % self.n)    # odd chip: first seat left of the button
            for i, s in enumerate(order):
                won[s] += share + (1 if i < extra else 0)
        net = [won[i] - self.total[i] for i in range(self.n)]
        self.stack = [self.start_stack[i] + net[i] for i in range(self.n)]
        self.over, self.to_act = True, None
        self.result = {"winners": sorted(winners_all), "net": net, "pot": self.pot(), "showdown": True,
                       "board": list(self.board), "hands": {s: list(self.hole[s]) for s in contenders}, "won": won}


def likely_opponents(hand, seat=0):
    """How many opponents will probably see the flop with you, for the calculator. After the flop it is simply everyone still in. Before
    it, the players still to act have not decided yet: counting all of them makes a set-mining hand look hopeless, so only the players who
    have already called or raised count in full, plus about a quarter of those yet to act (at least one opponent)."""
    active = [s for s in hand.active() if s != seat]
    if hand.street != "preflop":
        return len(active)
    acted = {s for st, s, kind, _ in hand.log if st == "preflop"}
    in_money = {s for st, s, kind, _ in hand.log if st == "preflop" and kind in ("call", "raise") and s != seat and not hand.folded[s]}
    behind = [s for s in active if s not in acted]
    return max(1, min(len(active), len(in_money) + int(round(len(behind) * 0.25))))


def service_input(hand, seat, opp_type="normal"):
    """The spot as the calculator's service.evaluate() expects it: what the tool would be shown at this moment."""
    info = hand.legal(seat)
    return dict(hole=hand.hole[seat], board=hand.board, pot=hand.pot(), call=info["to_call"],
                opps=likely_opponents(hand, seat), position=hand.position(seat), stack=hand.stack[seat], bb=hand.bb,
                range_mode="auto", opp_type=opp_type,
                raiser=hand.position(hand.aggressor) if hand.aggressor is not None else None)


def best_five(hole, board):
    """The five cards that make a player's best hand (best five of the seven)."""
    cards = list(hole) + list(board)
    best, best_v = None, -1
    for combo in itertools.combinations(cards, 5):
        v = eval7.evaluate([_CARDS[c] for c in combo])
        if v > best_v:
            best, best_v = list(combo), v
    return best


def hand_name(hole, board):
    """'Two pair, Tens and Eights', 'Flush, Ace high' ..."""
    from . import describe
    return describe.made_hand_name(best_five(hole, board))
