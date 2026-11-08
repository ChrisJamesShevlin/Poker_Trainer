"""A trainer table: you (seat 0) against five bots at a chosen stake, hand after hand.

Remembers between hands, and between sessions when saved with store.trainer_save():
- your ACCOUNT: money off the table (the bankroll) plus your stack on it. A buy-in, rebuy or top-up comes out of the
  account, so with 100 in the account and a buy-in of 5 you have 95 left to rebuy with; "cash out" puts your stack back;
- the stake, your buy-in, your stack (you can set it to any amount you can afford), and the currency symbol;
- the bots, each with its own stack (a bot below 20bb rebuys to the buy-in);
- the optional shot clock: if you don't act in `timer_s` seconds you check, or fold if there is a bet (or the standard online
  clock: 10 seconds preflop, 15 on each street after the flop);
- the game speed: bots take a moment to act, like an online table (some quick, some slow, snap folds), or not at all.

Drive it with: start_hand() -> state() -> hero_act(...) / check_clock() ... until state()["over"].
Every call that moves the game on also leaves STEPS (pop_frames()): a snapshot after each action with the time to wait
before showing it, so the page can play the hand back at a human pace. The shot clock starts when the steps end.
"""
import random
import time

from . import mtt, personas, stakes
from .bots import Bot, check_or_fold, make_traits, record_reads
from .game import Hand, IllegalAction, best_five, hand_name, likely_opponents

BOT_SIMS = 200
TIMER_CHOICES = [0, 10, 15, 20, 30, 45, 60]        # seconds; 0 = off (any value 0-120 is accepted)
BOT_REBUY_BELOW_BB = 20
REBUY_P = 0.55                  # a short stack tops up this often; otherwise they leave and someone new sits down
LEAVE_AHEAD_P = 0.04            # a player sitting on a big win sometimes cashes out and leaves (per hand)
AHEAD_MULT = 1.8
LEAVE_RANDOM_P = 0.004          # and now and then anyone just gets up (per hand)
# Tournaments (multi-table, see mtt.py): everyone starts with the same chips, the blinds go up on a clock, tables are balanced and broken.
START_CHIPS = 3000                  # (the old fixed structure: still used for a tournament saved before structures varied by stake)
BLIND_LEVELS = [(15, 30), (25, 50), (50, 100), (75, 150), (100, 200), (150, 300), (200, 400), (300, 600), (400, 800), (600, 1200), (1000, 2000), (1500, 3000)]
HANDS_PER_LEVEL = 12
ONLINE_PREFLOP_S, ONLINE_POSTFLOP_S = 10, 15         # the standard online shot clock: 10 seconds preflop, 15 on each street after the flop
DEFAULT_ACCOUNT = stakes.starter_balance()          # chips are cents: 30 buy-ins of the first level (60.00 at NL2)
SPEEDS = {"instant": 0.0, "fast": 0.5, "normal": 1.0, "slow": 1.7}      # multiplies the bots' think times
MIN_STEP_S = 0.22                                   # shortest pause between two actions (when not instant)
DEAL_PAUSE_S = 1.1                                  # cards being dealt at the start of a hand
STREET_PAUSE_S = 0.7                                # a new card on the board


class NeedsRebuy(Exception):
    """Your stack is below one big blind: add chips with rebuy() or set_hero_stack()."""


class Table:
    HERO = 0

    def __init__(self, stake="0.02/0.05", buy_in=None, timer_s=0, seed=None, clock=time.time, bot_sims=BOT_SIMS,
                 strength="balanced", account=DEFAULT_ACCOUNT, currency="£", speed="normal"):
        self.clock, self.bot_sims = clock, bot_sims
        self.strength = strength
        self.rng = random.Random(seed)
        self.hand = None
        self.deadline = None
        self.last = None                    # result summary of the hand just finished
        self.rebought = []                  # seats of bots that rebought at the start of this hand
        self.ante = 0                       # the ante per player (tournaments only)
        self.news = []                      # who topped up, left or sat down at the start of this hand
        self.tourney = None                 # the tournament in progress (or just finished), if this table is playing one
        self.reloads = 0                    # how many times you went bust and were given a fresh starter balance
        self.frames = []                    # steps of the hand so far that the page has not played yet
        self.hand_no, self.hero_net, self.button = 0, 0, 0
        self.timer_s = 0
        self.online_clock = False           # True: the standard online clock (10s preflop, 15s per street) instead of timer_s
        self.speed = "normal"
        self.set_speed(speed)
        self.set_currency(currency)
        self.bankroll, self.hero_stack, self.buy_in = int(account), 0, 0      # all of it starts off the table
        self._sit(stake, buy_in)
        self.set_timer(timer_s)

    # ------------------------------------------------------------------ account and seat
    def _money(self, chips):
        return stakes.money(chips, self.currency)

    def _sit(self, stake, buy_in=None, soft=False):
        """Take a seat at `stake`: whatever is on the table goes back to the account, then the buy-in comes out of it.
        soft=True shrinks a buy-in you cannot afford to what you have (as long as it is the minimum or more)."""
        if stake not in stakes.STAKES:
            raise ValueError(f"unknown stake {stake!r}")
        st = stakes.STAKES[stake]
        cash = self.bankroll + self.hero_stack
        lo, hi = stakes.buy_in_range(stake)
        if cash < lo:
            raise ValueError(f"Not enough in your account for {stakes.blinds_text(stake, self.currency)}: the minimum buy-in is "
                             f"{self._money(lo)} and you have {self._money(cash)}. Pick a lower stake or add to your account.")
        want = min(hi, cash) if buy_in is None else int(buy_in)
        if soft:
            want = min(want, cash)
        want = stakes.check_buy_in(stake, want, self.currency)
        if want > cash:
            raise ValueError(f"You only have {self._money(cash)} in your account.")
        self.stake, self.sb, self.bb, self.tier = stake, st["sb"], st["bb"], st["tier"]
        self.buy_in, self.hero_stack, self.bankroll = want, want, cash - want
        self._new_bots()

    def _new_bots(self):
        if self.strength not in stakes.STRENGTHS:
            raise ValueError(f"unknown table strength {self.strength!r}")
        self.revealed = False                       # new players: their level is a mystery until you cash out
        self.bots = [dict(b, stack=self.buy_in) for b in stakes.population(self.stake, self.rng, strength=self.strength)]

    def _between_hands(self):
        if self.hand is not None and not self.hand.over:
            raise IllegalAction("finish the current hand first")

    def set_stake(self, stake, buy_in=None):
        """New stake: new bots; your stack goes back to the account and the new buy-in comes out of it."""
        self._between_hands()
        self._sit(stake, buy_in)

    def set_buy_in(self, buy_in):
        self._between_hands()
        self.buy_in = stakes.check_buy_in(self.stake, buy_in, self.currency)

    def set_strength(self, strength):
        """Loose / balanced / hard: new players for the table (your stack stays)."""
        self._between_hands()
        old = self.strength
        self.strength = strength
        try:
            self._new_bots()
        except ValueError:
            self.strength = old
            raise

    def reset(self):
        """Sit down again: fresh bots, your stack back to the buy-in (from your account), your running total to zero."""
        self._between_hands()
        self._sit(self.stake, self.buy_in, soft=True)
        self.hero_net = 0

    def set_timer(self, seconds):
        seconds = int(seconds)
        if not 0 <= seconds <= 120:
            raise ValueError("timer must be 0 (off) to 120 seconds")
        self.timer_s = seconds

    def set_online_clock(self, on):
        self.online_clock = bool(on)

    def clock_seconds(self):
        """Seconds you get for the decision in front of you (0 = no shot clock)."""
        if self.online_clock:
            return ONLINE_PREFLOP_S if self.hand is None or self.hand.street == "preflop" else ONLINE_POSTFLOP_S
        return self.timer_s

    def set_speed(self, speed):
        if speed not in SPEEDS:
            raise ValueError(f"game speed must be one of {', '.join(SPEEDS)}")
        self.speed = speed

    def set_currency(self, currency):
        if currency not in stakes.CURRENCIES:
            raise ValueError(f"currency must be one of {' '.join(stakes.CURRENCIES)}")
        self.currency = currency

    def set_account(self, total):
        """Say how much you have in total (on the table and off it): a deposit or a withdrawal."""
        self._between_hands()
        total = int(total)
        if total < self.hero_stack:
            raise ValueError(f"That is less than the {self._money(self.hero_stack)} you have on the table.")
        self.bankroll = total - self.hero_stack

    def reload_starter(self):
        """Busted: a free starter balance, back at the first level."""
        self._between_hands()
        self.bankroll, self.hero_stack = stakes.starter_balance(), 0
        self.reloads += 1
        self._sit(stakes.first_stake())

    def reset_progress(self):
        """Grinding reset: the starter balance again, back at the first level, with the reload count and session total at zero."""
        self._between_hands()
        self.bankroll, self.hero_stack, self.reloads, self.hero_net = stakes.starter_balance(), 0, 0, 0
        self._sit(stakes.first_stake())

    def cash_out(self):
        """Stand up from the table: your stack goes back to your account, and you find out who you were playing against."""
        self._between_hands()
        self.bankroll += self.hero_stack
        self.hero_stack = 0
        self.revealed = True

    def _rejoin(self):
        """Sitting back down after cashing out means a new table: new players, and their level is hidden again."""
        if self.revealed:
            self._new_bots()

    def set_hero_stack(self, chips):
        """Say exactly how much you have on the table (from one big blind up). The difference moves to or from your
        account, so you cannot have more than your account holds."""
        self._between_hands()
        chips = int(chips)
        if not self.bb <= chips <= stakes.MAX_STACK_BB * self.bb:
            raise ValueError(f"stack must be between {self._money(self.bb)} and {self._money(stakes.MAX_STACK_BB * self.bb)}")
        delta = chips - self.hero_stack
        if self.revealed and self.hero_stack < self.bb:
            self._rejoin()
        if delta > self.bankroll:
            raise ValueError(f"You only have {self._money(self.bankroll)} off the table to add.")
        self.hero_stack, self.bankroll = chips, self.bankroll - delta

    def rebuy(self, amount=None):
        """Add chips from your account. With no amount, top up to the buy-in."""
        self._between_hands()
        if self.revealed and self.hero_stack < self.bb:
            self._rejoin()
        top = stakes.MAX_BUY_IN_BB * self.bb                  # like a site: you can top up to the maximum buy-in
        want = (self.buy_in - self.hero_stack) if amount is None else int(amount)
        want = min(want, top - self.hero_stack, self.bankroll)
        if want <= 0:
            if self.bankroll <= 0:
                raise ValueError("There is no money left in your account: use Set account to add more.")
            raise ValueError("nothing to add")
        lo = stakes.MIN_BUY_IN_BB * self.bb
        if self.hero_stack < self.bb and self.hero_stack + want < lo:      # sitting back down needs the minimum buy-in
            raise ValueError(f"You need at least {self._money(lo)} to sit down here and you have {self._money(self.bankroll)} "
                             "in your account. Pick a lower stake or add to your account.")
        self.hero_stack += want
        self.bankroll -= want

    # ------------------------------------------------------------------ playing
    def start_hand(self, seed=None):
        self._between_hands()
        if self.tourney is not None:
            return self._start_tourney_hand(seed)
        if self.hero_stack < self.bb:
            raise NeedsRebuy()
        self.rebought, self.news = [], []
        self._churn()
        return self._begin(seed)

    def _begin(self, seed, out=()):
        self.hand_no += 1
        seed = self.rng.randrange(1 << 30) if seed is None else seed
        stacks = [self.hero_stack] + [b["stack"] for b in self.bots]
        self.hand = Hand(stacks, button=self.button, sb=self.sb, bb=self.bb, seed=seed, out=out, ante=self.ante)
        self.last = None
        self._keys = {0: "hero", **{i + 1: b["persona"]["name"] for i, b in enumerate(self.bots) if not b.get("empty")}}   # a stable name for each seat
        self._bots = [Bot(b["style"], seed=seed * 7 + i, sims=self.bot_sims, loose_mult=b["loose_mult"], traits=b.get("traits"),
                          reads=b.setdefault("reads", {}), keys=self._keys) for i, b in enumerate(self.bots)]
        self.frames = [self._snap(0.0, "deal")]
        self._run_bots(opening=True)
        return self.state()

    # ------------------------------------------------------------------ players coming and going (cash tables)
    def _churn(self):
        """Before each hand: a short stack either tops up or gets up, and someone new sits in a left seat. A player who has won a
        lot sometimes leaves too, and now and then anyone does. Nobody is replaced in the middle of a hand."""
        for i, b in enumerate(self.bots):
            seat = i + 1
            if b["stack"] < BOT_REBUY_BELOW_BB * self.bb:
                if self.rng.random() < REBUY_P:
                    b["stack"] = self.buy_in
                    self.rebought.append(seat)
                    self.news.append({"seat": seat, "kind": "rebuy", "name": b["persona"]["name"]})
                else:
                    self._replace(i)
            elif self.rng.random() < (LEAVE_AHEAD_P if b["stack"] >= AHEAD_MULT * self.buy_in else LEAVE_RANDOM_P):
                self._replace(i)

    def _replace(self, i):
        old = self.bots[i]
        taken = {b["persona"]["name"] for j, b in enumerate(self.bots) if j != i}
        new = dict(stakes.newcomer(self.stake, self.rng, self.strength, taken), stack=self.buy_in)
        self.bots[i] = new
        self.news.append({"seat": i + 1, "kind": "swap", "name": old["persona"]["name"], "new": new["persona"]["name"]})

    # ------------------------------------------------------------------ tournaments (multi-table)
    @staticmethod
    def _empty_seat():
        return {"empty": True, "stack": 0, "style": "tag", "loose_mult": 1.0, "traits": {}, "persona": {"name": "", "blurb": ""}}

    def _seat_bots(self):
        """self.bots = the five seats beside you: the players at your table (the same dicts the field holds), empty where nobody sits."""
        t = self.tourney
        self.bots = [t["field"]["players"][str(pid)] if pid is not None else self._empty_seat() for pid in t["hero_seats"]]

    def start_tournament(self, stake, fee=None, size=mtt.FIELD_SIZE):
        """Register: a field of `size` players on six-handed tables, equal chips, blinds that go up, a prize pool from the entry fees."""
        self._between_hands()
        if stake not in stakes.STAKES:
            raise ValueError(f"unknown stake {stake!r}")
        fee = stakes.buy_in_range(stake)[1] if fee is None else int(fee)
        self.stake = stake
        self.tier = stakes.STAKES[stake]["tier"]
        sc = mtt.structure(stake)                                   # starting chips, blind levels, antes: they depend on the stake
        f = mtt.new_field(stake, self.strength, self.rng, size, sc["start_chips"])
        ids = mtt.hero_table_ids(f)
        seats = ids + [None] * (5 - len(ids))
        self.rng.shuffle(seats)
        self.revealed = False
        self.hero_stack, self.bankroll, self.buy_in = sc["start_chips"], 0, sc["start_chips"]
        self.sb, self.bb, self.ante = sc["levels"][0]
        self.button = self.rng.randrange(6)
        self.hand, self.last, self.hand_no, self.hero_net = None, None, 0, 0
        self.tourney = {"stake": stake, "level": 0, "hands_in_level": 0, "hands": 0, "knockouts": 0, "finished": False, "hero_place": None,
                        "buy_in": fee, "size": size, "paid": mtt.paid_places(size), "prizes": mtt.prize_list(size * fee, size),
                        "field": f, "hero_seats": seats, "busted_now": [], "pending": False, "final_table": False, "heads_up": False,
                        "table_moves": 0, "min_bb": sc["start_chips"] / sc["levels"][0][1], "elsewhere": 0,
                        "levels": sc["levels"], "hands_per_level": sc["hands_per_level"], "start_chips": sc["start_chips"], "ante_from": sc["ante_from"]}
        self._seat_bots()

    def _start_tourney_hand(self, seed):
        t = self.tourney
        if t["finished"]:
            raise IllegalAction("the tournament is over")
        self.rebought, self.news = [], []
        if t["pending"]:
            self._rebalance_and_reseat()
            t["pending"] = False
        if t["hands_in_level"] >= t["hands_per_level"] and t["level"] < len(t["levels"]) - 1:
            t["level"] += 1
            t["hands_in_level"] = 0
            self.news.append({"kind": "level", "blinds": list(t["levels"][t["level"]][:2]), "ante": t["levels"][t["level"]][2]})
        self.sb, self.bb, self.ante = t["levels"][t["level"]]
        out = {i + 1 for i, b in enumerate(self.bots) if b["stack"] <= 0}
        while self.button in out:
            self.button = (self.button + 1) % 6
        t["hands_in_level"] += 1
        t["hands"] += 1
        t["min_bb"] = min(t["min_bb"], self.hero_stack / self.bb)
        return self._begin(seed, out)

    def _rebalance_and_reseat(self):
        """Between rounds: the busted leave, tables are broken or evened out, and your table may get new faces (or you move)."""
        t = self.tourney
        f = t["field"]
        old = set(mtt.hero_table_ids(f))
        events = mtt.rebalance(f, self.rng)
        new = mtt.hero_table_ids(f)
        moved = any(e["kind"] == "break" and e["hero"] for e in events)
        name = lambda pid: f["players"][str(pid)]["persona"]["name"]
        if moved:
            seats = new + [None] * (5 - len(new))
            self.rng.shuffle(seats)
            t["table_moves"] += 1
            self.news.append({"kind": "moved", "table": f["hero_table"] + 1})
        else:
            seats = [pid if pid in new else None for pid in t["hero_seats"]]
            empty = [i for i, pid in enumerate(seats) if pid is None]
            self.rng.shuffle(empty)
            for pid in new:
                if pid not in seats:
                    seats[empty.pop()] = pid
                    self.news.append({"kind": "joined", "name": name(pid)})
        for pid in old - set(new):
            if f["players"][str(pid)]["place"] is None and not moved:
                self.news.append({"kind": "left", "name": name(pid)})
        t["hero_seats"] = seats
        self._seat_bots()

    def _tourney_after_hand(self, h):
        """Who went out (here and at the other tables), their places, your knockouts, and whether it is over for you."""
        t = self.tourney
        f = t["field"]
        f["players"]["0"]["stack"] = self.hero_stack
        pid_of = lambda seat: 0 if seat == 0 else t["hero_seats"][seat - 1]
        seated = [s for s in range(h.n) if s not in h.out]
        here = [s for s in seated if h.stack[s] <= 0]
        winners = set(h.result["winners"])
        knocked = {s for s in here if s != 0 and 0 in winners and not h.folded[s]}
        busted = [(pid_of(s), h.start_stack[s]) for s in here]
        elsewhere = mtt.play_other_tables(f, self.rng, self.sb, self.bb, self.ante)
        busted += elsewhere
        mtt.assign_places(f, busted)
        t["knockouts"] += len(knocked)
        t["elsewhere"] = len(elsewhere)
        t["busted_now"] = [{"seat": s, "place": f["players"][str(pid_of(s))]["place"], "by_you": s in knocked} for s in here]
        if 0 in [pid for pid, _ in busted]:
            t["finished"], t["hero_place"] = True, f["players"]["0"]["place"]
        elif f["left"] == 1:
            f["players"]["0"]["place"] = 1
            t["finished"], t["hero_place"] = True, 1
            t["final_table"] = t["heads_up"] = True           # winning means you reached both (even if the last two went out in the same round)
        else:
            t["final_table"] = t["final_table"] or f["left"] <= mtt.TABLE_SIZE
            t["heads_up"] = t["heads_up"] or f["left"] == 2
            t["pending"] = True
        if t["finished"]:
            self.revealed = True                      # it is over: now you find out who you were playing

    def tourney_result(self):
        """The finished tournament as one record (None until it is over)."""
        t = self.tourney
        if t is None or not t["finished"]:
            return None
        place = t["hero_place"]
        f = t["field"]
        return {"stake": t["stake"], "place": place, "size": t["size"], "buy_in": t["buy_in"],
                "prize": t["prizes"][place - 1] if place <= t["paid"] else 0, "hands": t["hands"], "knockouts": t["knockouts"],
                "level": t["level"] + 1, "final_table": t["final_table"], "heads_up": t["heads_up"], "min_bb": round(t["min_bb"], 2),
                "table_moves": t["table_moves"], "sharks": sum(1 for p in f["players"].values() if not p.get("hero") and stakes.player_type(p["style"]) == "shark")}

    def _tourney_state(self):
        t = self.tourney
        if t is None:
            return None
        f = t["field"]
        alive = sorted((p["stack"] for p in f["players"].values() if p["place"] is None), reverse=True)
        mine = f["players"]["0"]["stack"]
        left = f["left"]
        return {"stake": t["stake"], "level": t["level"] + 1, "levels": len(t["levels"]), "blinds": list(t["levels"][t["level"]][:2]), "ante": t["levels"][t["level"]][2],
                "start_chips": t["start_chips"], "ante_from": t["ante_from"],
                "hands_in_level": t["hands_in_level"], "hands_per_level": t["hands_per_level"],
                "next_level_in": None if t["level"] >= len(t["levels"]) - 1 else t["hands_per_level"] - t["hands_in_level"],
                "size": t["size"], "left": left, "paid": t["paid"], "bubble": (not t["finished"]) and left == t["paid"] + 1,
                "in_the_money": (not t["finished"]) and left <= t["paid"], "final_table": t["final_table"], "heads_up": t["heads_up"],
                "tables": len(f["tables"]), "table_no": f["hero_table"] + 1, "finished": t["finished"], "hero_place": t["hero_place"],
                "knockouts": t["knockouts"], "buy_in": t["buy_in"], "prize_pool": t["size"] * t["buy_in"], "prizes": t["prizes"],
                "chip_rank": (alive.index(mine) + 1) if mine in alive else None, "avg_stack": round(sum(alive) / len(alive)) if alive else 0,
                "busted_now": t["busted_now"], "elsewhere": t["elsewhere"], "hands": t["hands"], "result": self.tourney_result()}

    def _snap(self, delay, event=None):
        """The table as the page shows it at this moment, and how long to wait before showing it."""
        st = self.state()
        for k in ("legal", "for_tool", "seconds_left"):
            st.pop(k, None)
        out = {"delay": round(delay, 2), "state": st}
        if event:
            out["event"] = event
        return out

    def _run_bots(self, opening=False):
        h = self.hand
        factor = SPEEDS[self.speed]
        street, total, first = h.street, 0.0, opening
        while not h.over and h.to_act != self.HERO:
            s = h.to_act
            bot = self._bots[s - 1]
            act = bot.decide(h, s)
            delay = 0.0
            if factor:
                delay = bot.think_time(h, s, act) * factor
                if first:
                    delay += DEAL_PAUSE_S * factor
                if h.street != street:
                    delay += STREET_PAUSE_S * factor
                delay = max(MIN_STEP_S, delay)
            first, street = False, h.street
            h.act(s, act)
            self.frames.append(self._snap(delay))
            total += delay
        if h.over:
            self._finish()
            self.frames[-1]["state"] = self._snap(0)["state"]      # the last step carries the result
            self.deadline = None
        else:
            secs = self.clock_seconds()
            self.deadline = self.clock() + total + secs if secs else None

    def _finish(self):
        h = self.hand
        self.hero_stack = h.stack[self.HERO]
        for i, b in enumerate(self.bots):
            b["stack"] = h.stack[i + 1]
        self.hero_net += h.result["net"][self.HERO]
        for i, b in enumerate(self.bots):                       # the players who use reads learn from this hand
            if (b.get("traits") or {}).get("adapt") and not b.get("empty"):
                record_reads(b["reads"], self._keys, h, i + 1)
        self.button = (self.button + 1) % h.n
        if self.tourney is not None:
            self._tourney_after_hand(h)
        self.last = {"hand_no": self.hand_no, "net": h.result["net"][self.HERO], "pot": h.result["pot"],
                     "showdown": h.result["showdown"], "winners": h.result["winners"],
                     "won": {w: h.result["won"][w] for w in h.result["winners"]}}
        if h.result["showdown"]:                      # what each player showed down with, and the five cards that make it
            self.last["hands"] = {s: hand_name(c, h.board) for s, c in h.result["hands"].items()}
            self.last["five"] = {s: best_five(c, h.board) for s, c in h.result["hands"].items()}

    def hero_act(self, action):
        """Returns state(); 'timed_out' is set if the clock had already run out (then you check or fold instead)."""
        h = self.hand
        if h is None or h.over or h.to_act != self.HERO:
            raise IllegalAction("not your turn")
        timed_out = self.deadline is not None and self.clock() > self.deadline
        if timed_out:
            action = check_or_fold(h.legal(self.HERO))
        h.act(self.HERO, action)
        self.frames.append(self._snap(0.0, "hero"))
        self._run_bots()
        st = self.state()
        st["timed_out"] = timed_out
        return st

    def check_clock(self):
        """Call this regularly. If the shot clock has run out it plays check/fold for you; returns True if it did."""
        h = self.hand
        if h is None or h.over or h.to_act != self.HERO or self.deadline is None or self.clock() <= self.deadline:
            return False
        h.act(self.HERO, check_or_fold(h.legal(self.HERO)))
        self.frames.append(self._snap(0.0, "hero"))
        self._run_bots()
        return True

    def pop_frames(self):
        """The steps since the page last asked, oldest first; cleared once taken."""
        out, self.frames = self.frames, []
        return out

    # ------------------------------------------------------------------ what the page shows
    def state(self):
        h = self.hand
        if h is None:
            return {"started": False, **self._summary()}
        show = h.over and h.result["showdown"]
        last = {}                                   # each seat's latest action on this street
        opened = False
        for st, seat_, kind, amt in h.log:
            if st != h.street:
                continue
            if kind == "raise":
                kind = "raise" if (opened or h.street == "preflop") else "bet"
                opened = True
            last[seat_] = {"kind": kind, "amount": amt}
        seats = []
        for s in range(h.n):
            seats.append({"seat": s, "position": h.position(s), "stack": h.stack[s],
                          "out": self.tourney is not None and (s in h.out or (s != self.HERO and self.bots[s - 1]["stack"] <= 0)),
                          "place": (self.tourney["field"]["players"]["0" if s == self.HERO else str(self.tourney["hero_seats"][s - 1])]["place"]
                                    if self.tourney is not None and (s == self.HERO or self.tourney["hero_seats"][s - 1] is not None) else None),
                          "committed": h.committed[s], "folded": h.folded[s], "allin": h.allin[s],
                          "action": last.get(s),
                          "style": None if s == self.HERO else self.bots[s - 1]["style"] if self.revealed else None,
                          "empty": s != self.HERO and bool(self.bots[s - 1].get("empty")),
                          "name": None if s == self.HERO or self.bots[s - 1].get("empty") else self.bots[s - 1]["persona"]["name"],
                          "type": stakes.player_type(self.bots[s - 1]["style"]) if s != self.HERO and self.revealed and not self.bots[s - 1].get("empty") else None,
                          "blurb": self.bots[s - 1]["persona"]["blurb"] if s != self.HERO and self.revealed else None,
                          "hole": h.hole[s] if s == self.HERO or (show and not h.folded[s]) else None})
        out = {"started": True, "over": h.over, "street": h.street, "board": list(h.board), "pot": h.pot(),
               "button": h.button, "to_act": h.to_act, "seats": seats, "log": list(h.log), "last": self.last,
               "rebought": list(self.rebought), **self._summary()}
        if not h.over and h.to_act == self.HERO:
            out["legal"] = h.legal(self.HERO)
            out["for_tool"] = self._for_tool(out["legal"])
            out["seconds_left"] = None if self.deadline is None else max(0.0, self.deadline - self.clock())
        return out

    def _for_tool(self, legal):
        """The numbers the calculator asks for at this moment, so you can type them in (all in chips)."""
        h = self.hand
        pre = [e for e in h.log if e[0] == "preflop" and e[2] == "raise"]
        return {"position": h.position(self.HERO), "pot": h.pot() - legal["to_call"], "to_call": legal["to_call"],
                "opponents": likely_opponents(h, self.HERO), "in_hand": len(h.active()) - 1,
                "bettor": h.position(h.aggressor) if h.aggressor is not None else None, "bet_to": h.current_bet,
                "preflop_raiser": h.position(pre[-1][1]) if pre else None, "stack": h.stack[self.HERO]}

    def _summary(self):
        total = self.bankroll + self.hero_stack
        return {"stake": self.stake, "sb": self.sb, "bb": self.bb, "ante": self.ante, "tier": self.tier, "buy_in": self.buy_in,
                "hero_stack": self.hero_stack, "hero_net": self.hero_net, "hand_no": self.hand_no,
                "bankroll": self.bankroll, "account": total, "reloads": self.reloads, "currency": self.currency, "speed": self.speed,
                "account_buy_ins": round(total / self.buy_in, 1) if self.buy_in else None,
                "timer_s": self.clock_seconds(), "online_clock": self.online_clock, "strength": self.strength,
                "revealed": self.revealed, "chip_mode": self.tourney is not None, "tourney": self._tourney_state(),
                "news": list(self.news),
                "bots": [b["style"] for b in self.bots] if self.revealed else ["?"] * len(self.bots)}

    # ------------------------------------------------------------------ saving between sessions
    def to_dict(self):
        """Settings, stacks and bots (not a hand in progress: that hand is simply not saved)."""
        return {"stake": self.stake, "buy_in": self.buy_in, "timer_s": self.timer_s, "online_clock": self.online_clock, "hero_stack": self.hero_stack,
                "hero_net": self.hero_net, "hand_no": self.hand_no, "button": self.button, "strength": self.strength,
                "bankroll": self.bankroll, "reloads": self.reloads, "revealed": self.revealed, "tourney": self.tourney, "currency": self.currency,
                "speed": self.speed, "bots": self.bots}

    @classmethod
    def from_dict(cls, d, **kw):
        bankroll = d.get("bankroll", max(0, DEFAULT_ACCOUNT - d["hero_stack"]))      # tables saved before accounts existed
        for k, v in (("strength", stakes.LEGACY_STRENGTH.get(d.get("strength", "balanced"), d.get("strength", "balanced"))), ("currency", d.get("currency", "£")),
                     ("speed", d.get("speed", "normal"))):
            kw.setdefault(k, v)                              # saved values, unless the caller says otherwise
        t = cls(stake=d["stake"], buy_in=None if d.get("tourney") else d["buy_in"], timer_s=d["timer_s"], account=10 ** 12, **kw)
        t.reloads = d.get("reloads", 0)
        t.online_clock = d.get("online_clock", False)
        t.revealed = d.get("revealed", False)
        t.tourney = d.get("tourney")
        if t.tourney is not None:                                  # a tournament: its blinds come from its level
            t.buy_in = d["buy_in"]
            if "levels" not in t.tourney:                          # a tournament saved before structures varied by stake
                t.tourney.update(levels=[list(x) + [0] for x in BLIND_LEVELS], hands_per_level=HANDS_PER_LEVEL, start_chips=START_CHIPS, ante_from=99)
            t.sb, t.bb, t.ante = t.tourney["levels"][t.tourney["level"]]
        t.hero_stack, t.bankroll = d["hero_stack"], bankroll             # (built with a huge account so any saved state loads)
        t.hero_net, t.hand_no, t.button = d["hero_net"], d["hand_no"], d["button"]
        t.bots = [dict(b) for b in d["bots"]]
        taken = {b["persona"]["name"] for b in t.bots if b.get("persona")}
        for i, b in enumerate(t.bots):                       # tables saved before players had traits or nicknames
            if not b.get("traits"):
                b["traits"] = make_traits(b["style"], random.Random(i), b.get("loose_mult", 1.0))
            if not b.get("persona"):
                b["persona"] = personas.pick(b["style"], random.Random(i), taken)
                taken.add(b["persona"]["name"])
        if t.tourney is not None:
            t._seat_bots()                                         # in a tournament the seats beside you are the field's own players
        return t
