"""A shared practice table: you (the host) and a friend each at your own seat, the other seats bots. No hand tracking, no accounts, no clock.

The host's computer runs the table. Every player sees it from their own seat: your cards are at the bottom, the other human's cards are hidden
until a showdown. To keep the page unchanged, `state(viewer)` turns the whole table round so that the viewer is always seat 0.

Seats are numbered 0-5 from the host's side: 0 is the host, 1 is the friend (a bot until the friend sits down), 2-5 are bots.
"""
import random

from . import stakes
from .bots import Bot, record_reads
from .game import Hand, IllegalAction, best_five, hand_name, likely_opponents, service_input
from .table import BOT_REBUY_BELOW_BB, BOT_SIMS, DEAL_PAUSE_S, MIN_STEP_S, SPEEDS, STREET_PAUSE_S

SEATS = 6
HOST, GUEST = 0, 1


def clean_name(name, fallback):
    """A nickname: printable characters only, at most 20 long, never empty."""
    name = "".join(ch for ch in str(name or "") if ch.isprintable()).strip()[:20].strip()
    return name or fallback


class SharedTable:
    def __init__(self, stake="0.01/0.02", buy_in=None, strength="balanced", seed=None, bot_sims=BOT_SIMS, currency="£", speed="normal", scores=None):
        self.scores = scores                  # a mp_scores.Scores that keeps the running total between the two players (None in the tests)
        self.rng = random.Random(seed)
        self.bot_sims = bot_sims
        self.currency = currency
        self.strength = stakes.LEGACY_STRENGTH.get(strength, strength)
        self.hand = None
        self.last = None                      # the result of the hand just finished (absolute seat numbers)
        self.hand_no = 0
        self.button = self.rng.randrange(SEATS)
        self.net = [0, 0]                     # each human's result in this sitting (chips)
        self.news = []                        # who topped up since the last hand
        self.guest = None                     # {"name": ..., "stack": ...} once the friend has sat down
        self.guest_away = False               # the friend left (or was removed) in the middle of a hand: their seat folds out, then goes to a bot
        self.version = 0                      # goes up whenever anything changes, so the pages only redraw when they must
        self.host_name = "Host"
        self.speed = speed if speed in SPEEDS else "normal"   # how long the bots take to act (the host sets it)
        self.frames = {HOST: [], GUEST: []}   # steps of the hand each player's page has not played yet
        self.stake = None
        self._sit(stake, buy_in)

    # ------------------------------------------------------------------ the table's settings (the host's)
    def _between_hands(self):
        if self.hand is not None and not self.hand.over:
            raise IllegalAction("finish the current hand first")

    def _sit(self, stake, buy_in=None):
        if stake not in stakes.STAKES:
            raise ValueError(f"unknown stake {stake!r}")
        st = stakes.STAKES[stake]
        lo, hi = stakes.buy_in_range(stake)
        self.stake, self.sb, self.bb, self.tier = stake, st["sb"], st["bb"], st["tier"]
        self.buy_in = stakes.check_buy_in(stake, hi if buy_in is None else int(buy_in), self.currency)
        self.host_stack = self.buy_in
        if self.guest:
            self.guest["stack"] = self.buy_in
        self.net = [0, 0]
        self._new_bots()

    def _new_bots(self):
        if self.strength not in stakes.STRENGTHS:
            raise ValueError(f"unknown table strength {self.strength!r}")
        self.bots = [dict(b, stack=self.buy_in) for b in stakes.population(self.stake, self.rng, strength=self.strength)]
        self.version += 1

    def set_speed(self, speed):
        if speed not in SPEEDS:
            raise ValueError(f"game speed must be one of {', '.join(SPEEDS)}")
        self.speed = speed
        self.version += 1

    def _snap(self, delay):
        """The table as each player's page shows it at this moment, and how long to wait before showing it."""
        for v in (HOST, GUEST):
            st = self.state(v)
            for k in ("legal", "for_tool", "seconds_left"):
                st.pop(k, None)
            self.frames[v].append({"delay": round(delay, 2), "state": st})

    def pop_frames(self, viewer):
        out, self.frames[viewer] = self.frames[viewer], []
        return out

    def set_stake(self, stake, buy_in=None):
        self._between_hands()
        self._sit(stake, buy_in)

    def set_buy_in(self, buy_in):
        """A new buy-in for everyone: the stacks go back to it."""
        self._between_hands()
        self.buy_in = stakes.check_buy_in(self.stake, int(buy_in), self.currency)
        self.host_stack = self.buy_in
        if self.guest:
            self.guest["stack"] = self.buy_in
        for b in self.bots:
            b["stack"] = self.buy_in
        self.version += 1

    def set_strength(self, strength):
        self._between_hands()
        strength = stakes.LEGACY_STRENGTH.get(strength, strength)
        if strength not in stakes.STRENGTHS:
            raise ValueError(f"unknown table strength {strength!r}")
        self.strength = strength
        self._new_bots()

    def set_currency(self, currency):
        if currency not in stakes.CURRENCIES:
            raise ValueError(f"currency must be one of {' '.join(stakes.CURRENCIES)}")
        self.currency = currency
        self.version += 1

    # ------------------------------------------------------------------ people
    def set_host_name(self, name):
        """The host's nickname (it can change at any time: it is only a label)."""
        self.host_name = clean_name(name, "Host")
        self.version += 1

    def join(self, name):
        """The friend sits down in seat 1 (their stack is the buy-in). Between hands only."""
        self._between_hands()
        name = clean_name(name, "Guest")
        self.guest = {"name": name, "stack": self.buy_in}
        self.net[GUEST] = 0
        self.version += 1
        return GUEST

    def leave(self):
        """The friend gets up: seat 1 goes back to a bot. Between hands only."""
        self._between_hands()
        self._remove_guest()

    def _remove_guest(self):
        self.guest, self.guest_away = None, False
        self.bots[0]["stack"] = self.buy_in
        self.version += 1

    def request_leave(self):
        """The friend leaves, or the host removes them. Between hands they go at once; in the middle of a hand their seat folds out and they go
        when it ends (so a closed browser never freezes the table)."""
        if self.guest is None:
            return
        if self.hand is None or self.hand.over:
            self._remove_guest()
            return
        self.guest_away = True
        self._run_bots()
        self.version += 1

    def is_human(self, seat):
        return seat == HOST or (seat == GUEST and self.guest is not None)

    def _stack_of(self, seat):
        return self.host_stack if seat == HOST else self.guest["stack"] if (seat == GUEST and self.guest) else self.bots[seat - 1]["stack"]

    def _name_of(self, seat):
        if seat == HOST:
            return self.host_name
        if seat == GUEST and self.guest:
            return self.guest["name"]
        return self.bots[seat - 1]["persona"]["name"]

    # ------------------------------------------------------------------ playing
    def start_hand(self, seed=None):
        self._between_hands()
        self.news = []
        for seat in (HOST, GUEST):                                    # a human out of chips is topped up (there is no account)
            if self.is_human(seat) and self._stack_of(seat) < self.bb:
                self._set_stack(seat, self.buy_in)
                self.news.append({"seat": seat, "kind": "topup", "name": self._name_of(seat)})
                if self.scores is not None:
                    self.scores.topup(self._name_of(seat))
        for i, b in enumerate(self.bots):
            if i + 1 == GUEST and self.guest:
                continue                                              # (that seat is the friend's now)
            if b["stack"] < BOT_REBUY_BELOW_BB * self.bb:
                b["stack"] = self.buy_in
        self.hand_no += 1
        seed = self.rng.randrange(1 << 30) if seed is None else seed
        stacks = [self._stack_of(s) for s in range(SEATS)]
        self.hand = Hand(stacks, button=self.button, sb=self.sb, bb=self.bb, seed=seed)
        self.last = None
        self.frames = {HOST: [], GUEST: []}
        self._snap(0.0)
        self._keys = {s: ("host" if s == HOST else "guest" if (s == GUEST and self.guest) else self.bots[s - 1]["persona"]["name"]) for s in range(SEATS)}
        self._bots = {}
        for s in range(1, SEATS):
            if not self.is_human(s):
                b = self.bots[s - 1]
                self._bots[s] = Bot(b["style"], seed=seed * 7 + s, sims=self.bot_sims, loose_mult=b["loose_mult"], traits=b.get("traits"),
                                    reads=b.setdefault("reads", {}), keys=self._keys)
        self._run_bots(opening=True)
        self.version += 1

    def _set_stack(self, seat, chips):
        if seat == HOST:
            self.host_stack = chips
        elif seat == GUEST and self.guest:
            self.guest["stack"] = chips
        else:
            self.bots[seat - 1]["stack"] = chips

    def act(self, seat, action):
        """A human plays their seat. It must be their turn."""
        h = self.hand
        if h is None or h.over or h.to_act != seat or not self.is_human(seat):
            raise IllegalAction("not your turn")
        h.act(seat, action)
        self._snap(0.0)                                                 # a step for the human's own action too (its chips fly)
        self._run_bots()
        self.version += 1

    def _run_bots(self, opening=False):
        h = self.hand
        factor = SPEEDS[self.speed]
        street, first = h.street, opening
        while not h.over and (not self.is_human(h.to_act) or (h.to_act == GUEST and self.guest_away)):
            s = h.to_act
            delay = 0.0
            if self.is_human(s):                                        # the friend has gone: fold (or check) their seat
                h.act(s, "check" if h.legal(s)["can_check"] else "fold")
            else:
                bot = self._bots[s]
                act = bot.decide(h, s)
                if factor:                                              # the same think times as the local Casino
                    delay = bot.think_time(h, s, act) * factor
                    if first:
                        delay += DEAL_PAUSE_S * factor
                    if h.street != street:
                        delay += STREET_PAUSE_S * factor
                    delay = max(MIN_STEP_S, delay)
                h.act(s, act)
            first, street = False, h.street
            self._snap(delay)
        if h.over:
            self._finish()
            for v in (HOST, GUEST):                                     # the last step carries the result
                if self.frames[v]:
                    st = self.state(v)
                    for k in ("legal", "for_tool", "seconds_left"):
                        st.pop(k, None)
                    self.frames[v][-1]["state"] = st

    def _finish(self):
        h = self.hand
        for s in range(SEATS):
            self._set_stack(s, h.stack[s])
        for s in (HOST, GUEST):
            if self.is_human(s):
                self.net[s] += h.result["net"][s]
                if self.scores is not None:
                    self.scores.add(self._name_of(s), h.result["net"][s], won=s in h.result["winners"], pot=h.result["pot"],
                                    showdown=h.result["showdown"])
        for i, b in enumerate(self.bots):                              # bots that use reads learn from the hand
            if (b.get("traits") or {}).get("adapt") and not self.is_human(i + 1):
                record_reads(b["reads"], self._keys, h, i + 1)
        self.button = (self.button + 1) % h.n
        self.last = {"hand_no": self.hand_no, "net": list(h.result["net"]), "pot": h.result["pot"], "showdown": h.result["showdown"],
                     "winners": list(h.result["winners"]), "won": {w: h.result["won"][w] for w in h.result["winners"]}}
        if h.result["showdown"]:
            self.last["hands"] = {s: hand_name(c, h.board) for s, c in h.result["hands"].items()}
            self.last["five"] = {s: best_five(c, h.board) for s, c in h.result["hands"].items()}
        if self.guest_away:
            self._remove_guest()

    # ------------------------------------------------------------------ what each player's page shows
    def advice_input(self, viewer):
        """The spot as the calculator wants it, from the viewer's seat."""
        h = self.hand
        if h is None or h.over or h.to_act != viewer:
            raise IllegalAction("it is not your turn")
        return service_input(h, viewer)

    def state(self, viewer=HOST):
        """The table as `viewer` sees it, turned round so the viewer is seat 0 (the page always draws seat 0 at the bottom)."""
        rot = lambda s: None if s is None else (s - viewer) % SEATS
        h = self.hand
        out = self._summary(viewer)
        if h is None:
            return {"started": False, **out}
        show = h.over and h.result["showdown"]
        last, opened = {}, False
        for st, seat_, kind, amt in h.log:
            if st != h.street:
                continue
            if kind == "raise":
                kind = "raise" if (opened or h.street == "preflop") else "bet"
                opened = True
            last[seat_] = {"kind": kind, "amount": amt}
        seats = []
        for i in range(SEATS):
            s = (i + viewer) % SEATS
            seats.append({"seat": i, "position": h.position(s), "stack": h.stack[s], "out": False, "place": None, "committed": h.committed[s],
                          "folded": h.folded[s], "allin": h.allin[s], "action": last.get(s), "style": None, "empty": False,
                          "name": None if i == 0 else self._name_of(s), "human": self.is_human(s), "type": None, "blurb": None,
                          "hole": h.hole[s] if (s == viewer or (show and not h.folded[s])) else None})
        out.update({"started": True, "over": h.over, "street": h.street, "board": list(h.board), "pot": h.pot(), "button": rot(h.button),
                    "to_act": rot(h.to_act), "seats": seats, "log": [[st, rot(sd), kind, amt] for st, sd, kind, amt in h.log], "rebought": []})
        if self.last:
            L = self.last
            out["last"] = {"hand_no": L["hand_no"], "net": L["net"][viewer], "pot": L["pot"], "showdown": L["showdown"],
                           "winners": [rot(w) for w in L["winners"]], "won": {rot(w): a for w, a in L["won"].items()}}
            if L["showdown"]:
                out["last"]["hands"] = {rot(s): v for s, v in L["hands"].items()}
                out["last"]["five"] = {rot(s): v for s, v in L["five"].items()}
        else:
            out["last"] = None
        if not h.over and h.to_act == viewer:
            legal = h.legal(viewer)
            pre = [e for e in h.log if e[0] == "preflop" and e[2] == "raise"]
            out["legal"] = legal
            out["for_tool"] = {"position": h.position(viewer), "pot": h.pot() - legal["to_call"], "to_call": legal["to_call"],
                               "opponents": likely_opponents(h, viewer), "in_hand": len(h.active()) - 1,
                               "bettor": h.position(h.aggressor) if h.aggressor is not None else None, "bet_to": h.current_bet,
                               "preflop_raiser": h.position(pre[-1][1]) if pre else None, "stack": h.stack[viewer]}
            out["seconds_left"] = None
        return out

    def _summary(self, viewer):
        mine = self._stack_of(viewer)
        return {"stake": self.stake, "sb": self.sb, "bb": self.bb, "ante": 0, "tier": self.tier, "buy_in": self.buy_in,
                "hero_stack": mine, "hero_net": self.net[viewer], "hand_no": self.hand_no, "bankroll": 0, "account": mine, "reloads": 0,
                "currency": self.currency, "speed": self.speed, "account_buy_ins": round(mine / self.buy_in, 1) if self.buy_in else None,
                "timer_s": 0, "online_clock": False, "strength": self.strength, "revealed": False, "chip_mode": False, "tourney": None,
                "news": [dict(n, seat=(n["seat"] - viewer) % SEATS) for n in self.news], "bots": ["?"] * 5,
                "scores": self.scores.board() if self.scores is not None else [], "version": self.version, "guest": self.guest["name"] if self.guest else None, "host_name": self.host_name}
