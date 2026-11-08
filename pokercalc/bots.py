"""Rule-based opponents for the hold'em trainer. Simple, a little exploitable, and each one an individual.

Preflop they reuse the app's charts (preflop.advise), then bend them with their own habits: limping, calling raises
too wide, refusing to fold to a 3-bet, 3-betting. Postflop they use their OWN logic (equity against a typical range
vs the pot odds, plus habits like continuation-betting and overvaluing a pair), not the app's advice engine, so a
simulation of the advice against them is not circular.

Styles:  whale (a weak big spender: plays nearly every hand, raises and bets big, never folds) | reg (strong, our Shark: tight-ish, aggressive, bluffs) | tag (decent) | nit (tight, folds a lot) |
         station (loose, passive, calls too much) | maniac (loose, aggressive)
Every bot gets its own traits: the style's numbers moved a little (make_traits), so two stations at one table
are not clones, and they stay the same for the whole session. "Strong" is relative: still rules, not a solver.
Check a style with:  python3 -m pokercalc.stats
"""
import random

from . import describe, preflop
from .equity import equity
from .ranges import top_percent

# Probabilities unless noted. tight: fold a marginal chart hand. loose: play a hand the chart folds, down to the
# top loose_pct % of hands.
# limp: turn a chart raise into a limp/flat call. stick: don't fold to a 3-bet (top 40%). aggro: raise instead of
# call / bluff-raise. cbet / barrel: bet the flop as the preflop raiser / keep betting later streets. bluff: bet with
# nothing. margin: extra equity needed to call (negative = pays off too often). shift: moves the "strong enough to
# bet" line. pair_bias / draw_bias: equity it wrongly adds for one pair / a draw. size_tell: how often it sizes its
# bets by hand strength (big with strong hands, small with weak ones). caution: extra equity wanted before calling a
# bet, growing on later streets (people fold to turn and river bets more than the pot odds say).
STYLES = {
    "reg":     dict(tight=0.00, loose=0.00, loose_pct=38, limp=0.00, stick=0.42, aggro=0.55, cbet=0.54, barrel=0.38, bluff=0.20,
                    margin=0.00, shift=-0.02, pair_bias=0.00, draw_bias=0.00, size_tell=0.0, caution=0.22, overbet=0.00, adapt=0.60, bluff_eq=0.33, semi_lo=0.30, raise_bluff=0.60),
    "tag":     dict(tight=0.00, loose=0.15, loose_pct=38, limp=0.03, stick=0.40, aggro=0.42, cbet=0.50, barrel=0.38, bluff=0.12,
                    margin=0.00, shift=0.00, pair_bias=0.00, draw_bias=0.00, size_tell=0.1, caution=0.20, overbet=0.00, adapt=0.00),
    "nit":     dict(tight=0.40, loose=0.00, loose_pct=38, limp=0.03, stick=0.12, aggro=0.00, cbet=0.45, barrel=0.25, bluff=0.05,
                    margin=0.05, shift=0.05, pair_bias=0.00, draw_bias=0.00, size_tell=0.2, caution=0.30, overbet=0.00, adapt=0.00),
    "station": dict(tight=0.00, loose=0.85, loose_pct=58, limp=0.30, stick=0.97, aggro=0.00, cbet=0.45, barrel=0.15, bluff=0.10,
                    margin=-0.07, shift=0.04, pair_bias=0.06, draw_bias=0.05, size_tell=0.6, caution=0.10, overbet=0.00, adapt=0.00),
    "whale":   dict(tight=0.00, loose=0.97, loose_pct=70, limp=0.20, stick=0.99, aggro=0.60, cbet=0.65, barrel=0.50, bluff=0.34,
                    margin=-0.12, shift=0.00, pair_bias=0.08, draw_bias=0.07, size_tell=0.5, caution=0.04, overbet=0.30, adapt=0.00),
    "maniac":  dict(tight=0.00, loose=0.95, loose_pct=55, limp=0.05, stick=0.97, aggro=1.00, cbet=0.80, barrel=0.68, bluff=0.42,
                    margin=-0.02, shift=-0.05, pair_bias=0.03, draw_bias=0.04, size_tell=0.2, caution=0.17, overbet=0.15, adapt=0.00),
}
_BIAS_KEYS = {"margin", "shift", "pair_bias", "draw_bias", "caution"}
_TEMPO = {"reg": 0.85, "tag": 0.95, "nit": 1.2, "station": 1.15, "maniac": 0.8, "whale": 1.0}     # >1 = slower
_FIXED_KEYS = {"loose_pct"}
_POOL = {}


# Bluff-selection knobs (only the shark sets them): bluff_eq = bluff-bet only with hands below this equity; semi_lo = bluff-raise only with
# hands at or above this equity (a draw, not air); raise_bluff = scales how often. Everyone else bluffs with any hand, as before.
for _p in STYLES.values():
    _p.setdefault("bluff_eq", 1.0)
    _p.setdefault("semi_lo", 0.0)
    _p.setdefault("raise_bluff", 1.0)

# How each style plays at the very top of the ladder (skill 1). At skill 0 a player has the values in STYLES; in between, every
# habit moves part of the way. Sharks lose their leaks and get sharper, rec players tidy up, and fish stay fish but make fewer
# mistakes (they call a little less wildly, stop overvaluing pairs and draws so much, and fold a little more often).
_ELITE = {
    "reg":     dict(caution=0.10, bluff=0.28, stick=0.30, cbet=0.60, barrel=0.42, aggro=0.60, margin=0.01, adapt=1.0),
    "tag":     dict(caution=0.12, margin=0.00, cbet=0.58, barrel=0.46, bluff=0.12, aggro=0.50, size_tell=0.0, stick=0.36),
    "nit":     dict(caution=0.20, margin=0.02, bluff=0.05, size_tell=0.05, barrel=0.32, cbet=0.50, aggro=0.12),
    "station": dict(loose=0.65, stick=0.85, margin=-0.03, pair_bias=0.03, draw_bias=0.02, size_tell=0.30, limp=0.20, aggro=0.10, caution=0.16),
    "maniac":  dict(loose=0.85, aggro=0.85, bluff=0.30, margin=-0.01, stick=0.85, size_tell=0.10, caution=0.20),
    "whale":   dict(loose=0.80, stick=0.90, margin=-0.07, pair_bias=0.04, draw_bias=0.03, limp=0.15, size_tell=0.30, caution=0.10,
                    aggro=0.55, bluff=0.22, overbet=0.15),
}
_STRONG = {"reg": 1.0, "tag": 0.5, "nit": 0.5}          # how much more accurate their equity estimates get at the top (more trials)


def make_traits(style, rng, loose_mult=1.0, skill=0.0):
    """One player's own numbers: the style's (moved towards its top-of-the-ladder version by `skill`, 0 to 1), each then moved by
    a random amount (about +-25%, or +-0.03 for the offsets). Better players are also more consistent with each other."""
    base = dict(STYLES[style])
    for k, v in _ELITE[style].items():
        base[k] = base[k] + (v - base[k]) * skill
    amp = 0.25 * (1 - 0.5 * skill) if style in _STRONG else 0.25
    t = {}
    for k, v in base.items():
        if k in _FIXED_KEYS:
            t[k] = round(v * rng.uniform(0.9, 1.1))
        elif k in _BIAS_KEYS:
            t[k] = round(v + rng.uniform(-0.03, 0.03) * (1 - 0.5 * skill if style in _STRONG else 1), 3) if v or k in ("margin", "shift") else v
        else:
            t[k] = min(0.97, round(v * rng.uniform(1 - amp, 1 + amp), 3)) if v else 0.0
    t["loose"] = min(0.97, t["loose"] * loose_mult)
    t["tempo"] = round(_TEMPO[style] * rng.uniform(0.7, 1.4), 2)                 # how fast this player acts
    t["sims_mult"] = round(1 + 2 * _STRONG.get(style, 0.0) * skill, 2)            # strong players estimate their equity more accurately
    return t


def _pool(pct):
    """The hand classes in the top pct% of all hands (what we assume the other player holds)."""
    if pct not in _POOL:
        _POOL[pct] = top_percent(pct)
    return _POOL[pct]


def clamp_raise(info, target):
    return ("raise", int(max(info["min_raise_to"], min(info["max_raise_to"], round(target)))))


def check_or_call(info):
    return "check" if info["can_check"] else "call"


def check_or_fold(info):
    return "check" if info["can_check"] else "fold"


_PREV = {"flop": "preflop", "turn": "flop", "river": "turn"}


READ_PRIOR_RATE, READ_PRIOR_N = 0.50, 6        # before you have seen a player, assume they fold to half of the bets they face


def record_reads(reads, keys, hand, me):
    """After a hand: update `reads` ({opponent key: [bets faced after the flop, times folded]}) with what the other players did.
    `keys` maps a seat to a stable name for that player."""
    bettor, answered = None, set()
    street = None
    for st, seat, kind, _ in hand.log:
        if st != street:
            street, bettor, answered = st, None, set()
        if st == "preflop":
            continue
        if kind == "raise":
            bettor, answered = seat, set()
        elif bettor is not None and seat != bettor and seat != me and seat not in answered and seat in keys:
            answered.add(seat)
            r = reads.setdefault(keys[seat], [0, 0])
            r[0] += 1
            r[1] += kind == "fold"


class Bot:
    def __init__(self, style="tag", seed=None, sims=300, loose_mult=1.0, traits=None, reads=None, keys=None):
        self.reads = reads if reads is not None else {}          # what this player has seen of each opponent (see record_reads)
        self.keys = keys or {}
        self.weak_bets = self.value_bets = 0
        self.bluffs = 0                                         # how many pure bluffs this player has made (for checking their frequency)
        self.style = style
        self.rng = random.Random(seed)
        self.p = dict(traits) if traits else make_traits(style, self.rng, loose_mult)
        self.sims = int(sims * self.p.get("sims_mult", 1.0))
        self.trng = random.Random(None if seed is None else seed * 31 + 7)       # timing has its own random stream

    def _read_mult(self, hand, seat):
        """How much to bluff this time, from what has been seen of the players still in the hand: more against players who fold,
        much less (but never none) against players who do not. Only players with `adapt` use it."""
        a = self.p.get("adapt", 0.0)
        if not a:
            return 1.0
        everyone_folds = 1.0
        for s in hand.active():
            if s == seat:
                continue
            faced, folded = self.reads.get(self.keys.get(s), (0, 0))
            everyone_folds *= (folded + READ_PRIOR_RATE * READ_PRIOR_N) / (faced + READ_PRIOR_N)
        raw = max(0.2, min(1.6, (everyone_folds - 0.20) / 0.30))      # 0.5 = as usual, 0.26 or less = a fifth, 0.68 or more = 1.6 times
        return 1 + a * (raw - 1)

    def decide(self, hand, seat):
        info = hand.legal(seat)
        return self._pre(hand, seat, info) if hand.street == "preflop" else self._post(hand, seat, info)

    def think_time(self, hand, seat, action):
        """Seconds this player takes before acting (before the game-speed setting is applied). Snap folds are common,
        raises and calls facing a big bet take longer, later streets are slower, and now and then someone tanks."""
        r, tempo = self.trng, self.p.get("tempo", 1.0)
        kind = action if isinstance(action, str) else action[0]
        info = hand.legal(seat)
        if kind == "fold":
            base = r.uniform(0.25, 0.7) if r.random() < 0.6 else r.uniform(0.8, 1.9)
        elif kind == "check":
            base = r.uniform(0.3, 0.9)
        elif kind == "call":
            base = r.uniform(0.7, 1.8)
        else:
            base = r.uniform(1.0, 2.6)
        if info["to_call"] > 0.5 * hand.pot() and kind != "fold":
            base += r.uniform(0.3, 1.2)                                        # a big bet to think about
        if hand.street != "preflop":
            base *= 1.15
        if kind != "fold" and r.random() < 0.05:
            base += r.uniform(2.0, 4.0)                                        # the odd long tank
        return min(6.0, base * tempo)

    # ------------------------------------------------------------------ preflop
    def _pre(self, hand, seat, info):
        rng, p, bb = self.rng, self.p, hand.bb
        pos = hand.position(seat)
        facing = hand.current_bet > bb
        blind_seats = {hand.sb_seat, hand.bb_seat}
        n_limpers = sum(1 for s in hand.active() if s not in blind_seats and hand.committed[s] >= bb)
        limpers = (not facing) and n_limpers > 0
        stack_bb = hand.start_stack[seat] / bb
        raise_bb = hand.current_bet / bb if facing else None
        tier = "normal"
        if facing:
            tier = ("shove" if raise_bb >= 20 or hand.current_bet >= 0.4 * hand.start_stack[seat]
                    else "big" if raise_bb > 4.5 else "normal")
        raiser = hand.position(hand.aggressor) if facing and hand.aggressor is not None else None
        opps = len(hand.active()) - 1
        g = preflop.advise(hand.hole[seat], pos, stack_bb, facing, tier, raise_bb, raiser, limpers, opps)
        act, pct = g["action"], g["pct"]
        cheap = info["to_call"] <= 8 * bb
        if act in ("RAISE", "CALL") and pct > 8 and rng.random() < p["tight"]:
            act = "FOLD"
        elif act == "RAISE" and not (facing and tier == "shove") and rng.random() < p["limp"]:
            act = "CALL"                                   # limp / flat call instead of raising
        elif act == "FOLD" and pct <= p["loose_pct"] and cheap and rng.random() < p["loose"]:
            act = "CALL"
        elif act == "FOLD" and facing and tier == "big" and pct <= 40 and rng.random() < p["stick"]:
            act = "CALL"                                   # won't fold to a 3-bet
        if act == "CALL" and not (facing and tier == "shove") and rng.random() < p["aggro"] * (0.35 if facing else 0.5):
            act = "RAISE"                                  # 3-bet, or raise instead of limping
        if act == "FOLD":
            return check_or_fold(info)
        if act in ("CHECK", "CALL") or not info["can_raise"]:
            return check_or_call(info)
        if not facing:
            return clamp_raise(info, ((3.0 if pos in ("UTG", "SB") else 2.5) + n_limpers) * bb)
        if tier == "shove":
            return clamp_raise(info, info["max_raise_to"])
        oop = pos in ("SB", "BB")
        return clamp_raise(info, hand.current_bet * (2.2 if tier == "big" else 4.0 if oop else 3.0))   # 3-bet / 4-bet

    # ------------------------------------------------------------------ postflop
    def _post(self, hand, seat, info):
        rng, p = self.rng, self.p
        opps = len(hand.active()) - 1
        pot, to_call = hand.pot(), info["to_call"]
        req = to_call / (pot + to_call) if to_call else 0.0
        if to_call:
            ratio = to_call / max(1, pot - to_call)                     # the bet as a share of the pot before it
            rng_pct = (45 if ratio < 0.4 else 33 if ratio < 0.8 else 24) - (0 if hand.street == "flop" else 5)
        else:
            rng_pct = 40
        eq = equity(hand.hole[seat], hand.board, opps, sims=self.sims, seed=rng.randrange(1 << 30),
                    range_classes=_pool(rng_pct))[2]
        pot_raise = info["current_bet"] + pot + to_call                  # a pot-sized raise
        if p["pair_bias"] or p["draw_bias"]:
            d = describe.analyse(hand.hole[seat], hand.board)
            if d["made"] == "Pair":
                eq += p["pair_bias"]
            if any("backdoor" not in x for x in d["draws"]):
                eq += p["draw_bias"]
        strong = {1: 0.62, 2: 0.47, 3: 0.40}.get(opps, 0.34) + p["shift"]
        if to_call == 0:
            if not info["can_raise"]:
                return "check"
            prev = _PREV[hand.street]
            was_aggressor = (hand.pf_aggressor == seat) if hand.street == "flop" else \
                any(st == prev and s == seat and k == "raise" for st, s, k, _ in hand.log)
            multiway = 1.0 if opps == 1 else 0.6 if opps == 2 else 0.4
            if eq >= strong:
                bet = rng.random() < 0.85 + 0.15 * p["aggro"]
            elif was_aggressor:
                bet = rng.random() < min(0.95, (p["cbet"] if hand.street == "flop" else p["barrel"]) * multiway * self._read_mult(hand, seat))
            else:
                bet = opps == 1 and eq < p.get("bluff_eq", 1.0) and rng.random() < min(0.9, p["bluff"] * self._read_mult(hand, seat))
                self.bluffs += bool(bet)
            if not bet:
                return "check"
            if eq >= strong:
                self.value_bets += 1
            elif was_aggressor:
                self.weak_bets += 1
            return clamp_raise(info, pot * self._size(eq, strong))
        need = req + p["margin"] + p["caution"] * {"flop": 0.75, "turn": 1.0, "river": 1.3}[hand.street]
        if eq < need:
            if (info["can_raise"] and eq >= p.get("semi_lo", 0.0)
                    and rng.random() < p["aggro"] * p["bluff"] * 0.5 * p.get("raise_bluff", 1.0) * self._read_mult(hand, seat)):
                self.bluffs += 1
                return clamp_raise(info, pot_raise * 0.8)                        # a bluff-raise
            return "fold"
        if eq >= strong + 0.04 and info["can_raise"] and rng.random() < 0.4 + p["aggro"]:
            return clamp_raise(info, pot_raise * 0.8)
        return "call"

    def _size(self, eq, strong):
        """Fraction of the pot to bet. Players with a size tell bet big when strong and small when weak."""
        if self.rng.random() < self.p["overbet"]:
            return self.rng.choice((1.0, 1.25, 1.5))                          # a big spender: more than the pot
        if self.rng.random() < self.p["size_tell"]:
            return 1.0 if eq >= strong + 0.10 else 0.33
        return self.rng.choice((0.5, 0.66, 0.75))
