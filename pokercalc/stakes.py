"""Stakes, buy-ins and the kind of players you meet at each level (for the trainer), laid out like an online lobby.

Chips are CENTS. A stake is shown as its blinds ("$0.02/$0.05") with a buy-in range ("$2 - $5" = 40bb to 100bb, the
usual 6-max range). Three levels, two stakes each; the tiers are ours, a rough picture of who sits there:
- Micro:  mostly loose players who call too much, 1-2 strong regulars
- Medium: mostly regulars, the odd weak player
- High:   almost all strong, tight-aggressive players
"""
import random

# The stakes a large online poker site spreads, from its NL2 micros up to the biggest nosebleed games ($500/$1,000).
# key = "small blind/big blind" in dollars. `tough` (0 to 1) is how hard the table is: 0 = the softest micro game,
# 1 = the toughest high-stakes game. The players at each stake are drawn from it (see population), so every level plays
# a little harder than the one below it. Our own scale: a rough picture, not measured from real sites.
STAKES = {
    "0.01/0.02": dict(sb=1,      bb=2,      tier="micro", tough=0.00),       # NL2
    "0.02/0.05": dict(sb=2,      bb=5,      tier="micro", tough=0.06),       # NL5
    "0.05/0.10": dict(sb=5,      bb=10,     tier="micro", tough=0.14),       # NL10
    "0.10/0.25": dict(sb=10,     bb=25,     tier="micro", tough=0.26),       # NL25
    "0.25/0.50": dict(sb=25,     bb=50,     tier="mid",   tough=0.38),       # NL50
    "0.50/1":    dict(sb=50,     bb=100,    tier="mid",   tough=0.50),       # NL100
    "1/2":       dict(sb=100,    bb=200,    tier="mid",   tough=0.60),       # NL200
    "2/5":       dict(sb=200,    bb=500,    tier="high",  tough=0.70),       # NL500
    "5/10":      dict(sb=500,    bb=1000,   tier="high",  tough=0.80),       # NL1,000
    "10/20":     dict(sb=1000,   bb=2000,   tier="high",  tough=0.87),       # NL2,000
    "25/50":     dict(sb=2500,   bb=5000,   tier="high",  tough=0.92),       # NL5,000
    "50/100":    dict(sb=5000,   bb=10000,  tier="high",  tough=0.95),       # NL10,000
    "100/200":   dict(sb=10000,  bb=20000,  tier="high",  tough=0.97),       # NL20,000
    "200/400":   dict(sb=20000,  bb=40000,  tier="high",  tough=0.99),       # NL40,000
    "500/1000":  dict(sb=50000,  bb=100000, tier="high",  tough=1.00),       # NL100,000
}
MIN_BUY_IN_BB, MAX_BUY_IN_BB = 40, 100      # buy-in range, like the lobby ("$2 - $5")
MAX_STACK_BB = 300                          # you may be sitting on more than the max buy-in after winning
TIER_NAMES = {"micro": "Micro", "mid": "Medium", "high": "High"}
TIER_TEXT = {"micro": "Mostly recreational players and callers, with the odd strong regular",
             "mid": "A mix of recreational players and regulars, with a few weak players",
             "high": "Almost all strong, tight-aggressive players: little to exploit"}

# Who sits at the table. BASE_SHARKS = how many of the five seats are sharks at a "balanced" table of each stake (one number per
# stake, in ladder order). The other seats are drawn from the weights below (recreational styles: nit, tag; fish: station, maniac;
# whale), blended between the tiers by the stake's `tough`. loose_mult scales how often the loose styles play hands outside the chart.
# Rough picture, not measured data (public numbers on real player pools are thin): the low stakes are mostly recreational players
# and callers with the odd regular, whales are rare everywhere and gone at the top, and the sharks take over as the stakes rise.
TIERS = {
    "micro": dict(fish={"station": 0.17, "maniac": 0.09, "nit": 0.24, "tag": 0.46, "whale": 0.04}, loose_mult=1.25),
    "mid":   dict(fish={"station": 0.17, "maniac": 0.08, "nit": 0.20, "tag": 0.45, "whale": 0.02}, loose_mult=0.7),
    "high":  dict(fish={"station": 0.20, "maniac": 0.08, "nit": 0.15, "tag": 0.50, "whale": 0.0}, loose_mult=0.5),
}
BASE_SHARKS = [1.25, 1.3, 1.4, 1.5, 1.8, 2.3, 2.4, 2.9, 3.5, 3.6, 3.8, 3.95, 4.0, 4.05, 4.1]
assert len(BASE_SHARKS) == len(STAKES)


CURRENCIES = {"£": "Pound", "$": "Dollar", "€": "Euro"}      # just the symbol shown: nothing is converted


def money(chips, cur="$"):
    """£0.05, £1, £2,000: whole pounds without pence, like a lobby."""
    d = chips / 100
    return f"{cur}{d:,.0f}" if chips % 100 == 0 else f"{cur}{d:,.2f}"


def blinds_text(stake, cur="$"):
    st = STAKES[stake]
    return f"{money(st['sb'], cur)}/{money(st['bb'], cur)}"


def buy_in_range(stake):
    bb = STAKES[stake]["bb"]
    return MIN_BUY_IN_BB * bb, MAX_BUY_IN_BB * bb


def ladder(cur="$"):
    """What the lobby lists: one row per stake."""
    rows = []
    for key, st in STAKES.items():
        lo, hi = buy_in_range(key)
        rows.append({"key": key, "blinds": blinds_text(key, cur), "sb": st["sb"], "bb": st["bb"],
                     "buy_in_min": lo, "buy_in_max": hi, "buy_in": f"{money(lo, cur)} - {money(hi, cur)}",
                     "tier": st["tier"], "tier_name": TIER_NAMES[st["tier"]], "tier_text": TIER_TEXT[st["tier"]]})
    return rows


# ---- the account (bankroll). It is not editable: it starts at STARTER_BUY_INS buy-ins of the first level, and it grows or
# shrinks with your results. To sit at a higher level you need MOVE_UP_BUY_INS buy-ins of that level; if you fall below
# STAY_BUY_INS buy-ins of the level you are on, you are moved down. Below the first level's minimum buy-in you are busted
# and get a free starter balance again.
STARTER_BUY_INS = 30
MOVE_UP_BUY_INS = 30
STAY_BUY_INS = 20


def skill(stake):
    """How good the players at this stake are, 0 (NL2) to 1 (the top): a steady climb up the ladder, one step per level."""
    keys = list(STAKES)
    return keys.index(stake) / (len(keys) - 1)


def first_stake():
    return next(iter(STAKES))


def starter_balance():
    return STARTER_BUY_INS * buy_in_range(first_stake())[1]


def bankroll_need(stake, staying=False):
    """Chips you need in total to sit at this stake (a smaller cushion to stay than to move up)."""
    return (STAY_BUY_INS if staying else MOVE_UP_BUY_INS) * buy_in_range(stake)[1]


def bust_line():
    """Below this you cannot even buy in at the first level."""
    return buy_in_range(first_stake())[0]


def check_buy_in(stake, buy_in, cur="$"):
    lo, hi = buy_in_range(stake)
    if not lo <= buy_in <= hi:
        raise ValueError(f"buy-in must be between {money(lo, cur)} and {money(hi, cur)}")
    return int(buy_in)


# The kinds of player you meet, from easiest to hardest. Each bot style belongs to one type:
#   whale = a weak big spender: plays nearly every hand with money that matters little to them (the usual meaning)
#   fish  = weak players who lose money: calling stations and maniacs
#   rec   = recreational players: amateurs who play reasonably but with leaks (solid and tight players)
#   shark = the strongest players: tight, aggressive, hard to exploit
PLAYER_TYPES = {
    "whale": dict(label="Whale", text="Big spenders: play nearly every hand and never like folding"),
    "fish":  dict(label="Fish", text="Weak players: call too much or splash around"),
    "rec":   dict(label="Rec", text="Recreational players: decent, but they leak chips"),
    "shark": dict(label="Shark", text="The strongest players: tight, aggressive and hard to exploit"),
}
STYLE_TYPE = {"whale": "whale", "station": "fish", "maniac": "fish", "nit": "rec", "tag": "rec", "reg": "shark"}
STYLE_NAME = {"whale": "Whale", "station": "Calling station", "maniac": "Maniac", "nit": "Tight", "tag": "Solid", "reg": "Shark"}


def player_type(style):
    return STYLE_TYPE[style]


STRENGTHS = {           # from the softest table to the toughest; five steps so the Grinding ladder can climb gradually
    "very_loose": dict(label="Very loose", text="Lots of weak players: hardly any sharks, many callers, maniacs and whales",
                       shift=-1.0, loose_mult=1.2, skew={"station": 1.4, "maniac": 1.3, "whale": 1.6, "nit": 0.6, "tag": 0.7}),
    "loose":      dict(label="Loose", text="More weak players: fewer sharks, more callers, maniacs and whales",
                       shift=-0.5, loose_mult=1.1, skew={"station": 1.2, "maniac": 1.15, "whale": 1.3, "nit": 0.8, "tag": 0.85}),
    "balanced":   dict(label="Balanced", text="The usual mix for this stake", shift=0, loose_mult=1.0, skew={}),
    "tough":      dict(label="Tough", text="More sharks and fewer weak players than usual",
                       shift=0.5, loose_mult=0.9, skew={"station": 0.8, "maniac": 0.85, "whale": 0.7, "nit": 1.1, "tag": 1.15}),
    "hard":       dict(label="Hard", text="Mostly sharks: tougher and tighter",
                       shift=1.0, loose_mult=0.8, skew={"station": 0.6, "maniac": 0.7, "whale": 0.4, "nit": 1.2, "tag": 1.3}),
}


LEGACY_STRENGTH = {"regs": "hard"}       # the old name, still found in saved tables


def strength_options():
    return [{"key": k, "label": v["label"], "text": v["text"]} for k, v in STRENGTHS.items()]


MIN_WEAK = 0.3      # even the toughest table has the odd weaker player (about one table in three)
MIN_SHARK = 0.25    # and even the softest table has the odd regular (about one table in four)
_ANCHORS = (("micro", 0.0), ("mid", 0.5), ("high", 1.0))      # where each tier sits on the 0..1 `tough` scale


def table_mix(stake):
    """The usual mix at a stake: (regs range, fish weights, loose_mult), blended between the tiers by the stake's `tough`."""
    x = STAKES[stake]["tough"]
    for (a, xa), (b, xb) in zip(_ANCHORS, _ANCHORS[1:]):
        if x <= xb:
            f = (x - xa) / (xb - xa)
            ta, tb = TIERS[a], TIERS[b]
            lerp = lambda u, v: u + (v - u) * f
            base = BASE_SHARKS[list(STAKES).index(stake)]
            regs = (base, base)                                               # the usual number of sharks at this stake
            fish = {k: lerp(ta["fish"][k], tb["fish"][k]) for k in ta["fish"]}
            return regs, fish, lerp(ta["loose_mult"], tb["loose_mult"])


def expected_mix(stake, strength="balanced", n=5):
    """How many of each kind of player a table at this stake and strength has on average: {'shark', 'rec', 'fish', 'whale'}.
    (The same numbers population() and newcomer() draw from, so what a page shows is what is played.)"""
    sg = STRENGTHS[LEGACY_STRENGTH.get(strength, strength)]
    regs_range, fish, _ = table_mix(stake)
    sharks = max(MIN_SHARK, min(n - MIN_WEAK, sum(regs_range) / 2 + sg["shift"]))
    weights = {k: v * sg["skew"].get(k, 1.0) for k, v in fish.items()}
    total = sum(weights.values())
    share = {"rec": 0.0, "fish": 0.0, "whale": 0.0}
    for style, w in weights.items():
        share[player_type(style)] += w / total
    return {"shark": sharks, **{t: (n - sharks) * x for t, x in share.items()}}


def population(stake, rng=None, n=5, strength="balanced"):
    """Pick the n bots for a table at this stake: [{'style', 'loose_mult', 'traits'}, ...].

    The stake sets the usual mix (table_mix: higher stakes = more sharks and tighter play). `strength` shifts
    it: loose = about two fewer sharks and more weak players, hard = about two more sharks. Each bot gets its own
    traits (bots.make_traits) so players are individuals."""
    from .bots import make_traits
    from .personas import pick
    rng = rng or random.Random()
    strength = LEGACY_STRENGTH.get(strength, strength)
    sg = STRENGTHS[strength]
    regs_range, fish, loose_mult = table_mix(stake)
    avg = max(MIN_SHARK, min(n - MIN_WEAK, sum(regs_range) / 2 + sg["shift"]))       # average number of sharks at this table
    regs = int(avg) + (rng.random() < avg - int(avg))                      # 2.6 = two sharks, or three 60% of the time
    styles = list(fish)
    weights = [fish[k] * sg["skew"].get(k, 1.0) for k in styles]
    seats = ["reg"] * regs + rng.choices(styles, weights, k=n - regs)
    rng.shuffle(seats)
    mult = loose_mult * sg["loose_mult"]
    out, taken = [], set()
    for x in seats:
        persona = pick(x, rng, taken)
        taken.add(persona["name"])
        out.append({"style": x, "loose_mult": mult, "traits": make_traits(x, rng, mult, skill(stake)), "persona": persona})
    return out


def newcomer(stake, rng, strength="balanced", taken=()):
    """One new player to take a seat at this stake (someone who has just sat down), not using a name already at the table."""
    from .bots import make_traits
    from .personas import pick
    sg = STRENGTHS[LEGACY_STRENGTH.get(strength, strength)]
    regs_range, fish, loose_mult = table_mix(stake)
    sharks = max(MIN_SHARK, min(5 - MIN_WEAK, sum(regs_range) / 2 + sg["shift"]))
    if rng.random() < sharks / 5:
        style = "reg"
    else:
        styles = list(fish)
        style = rng.choices(styles, [fish[k] * sg["skew"].get(k, 1.0) for k in styles])[0]
    mult = loose_mult * sg["loose_mult"]
    return {"style": style, "loose_mult": mult, "traits": make_traits(style, rng, mult, skill(stake)), "persona": pick(style, rng, taken)}
