"""A multi-table tournament (MTT) field: many players spread over 6-max tables that play at the same time.

How it works, like a real MTT:
- Everyone starts with the same chips and the blinds go up on a clock (every few hands).
- Every table plays a hand each round. Only the hand at YOUR table is played by you; the other tables are played by the
  bots, quickly, so chips and knockouts are real everywhere.
- Players who lose all their chips are out, in the order they bust (more chips at the start of the hand = a better place when
  several go out together).
- Tables are kept balanced (no table more than one player bigger than another). When the players left fit on fewer tables, the
  smallest table is broken and its players take the empty seats elsewhere. Breaking can be YOUR table, so your opponents change.
  When six or fewer remain they all sit at the final table.
- The top ~14% are paid, top-heavy, and the last place before the money is the bubble.

The field is plain data (dicts, lists, numbers) so it can be saved with the table.
"""
import math

from . import personas, stakes
from .bots import Bot
from .game import Hand

TABLE_SIZE = 6
FIELD_SIZE = 36                     # six tables of six
BG_SIMS = 12                        # equity trials for the bots at the tables you are not at (they only need to be quick)
PAID_SHARE = 0.14                   # about the top 14% are paid
_WEIGHTS = {1: [1.0], 2: [0.65, 0.35], 3: [0.50, 0.30, 0.20], 4: [0.45, 0.27, 0.17, 0.11], 5: [0.40, 0.25, 0.17, 0.11, 0.07],
            6: [0.36, 0.23, 0.16, 0.11, 0.08, 0.06]}


# How a tournament is built at each stake (hold'em's 15 levels; other ladders pick the nearest row): (starting chips, first big blind,
# hands per level, first level with antes). Deeper stacks, slower levels and earlier antes as the stakes rise, like real online MTTs: the
# low-entry ones start with 3,000 chips (100 big blinds) and the big ones with 200 big blinds. Our own picture, not copied from a site.
_STRUCTURES = [(3000, 30, 10, 8), (3000, 30, 10, 8), (5000, 50, 10, 7), (5000, 50, 11, 7), (10000, 100, 12, 6), (10000, 100, 12, 6),
               (15000, 100, 12, 5), (20000, 100, 13, 5), (20000, 100, 13, 4), (30000, 200, 14, 4), (30000, 200, 14, 4),
               (40000, 200, 15, 3), (50000, 250, 15, 3), (50000, 250, 15, 3), (60000, 300, 15, 3)]
_BLIND_STEPS = [1, 1.5, 2, 2.5, 3.5, 4.5, 6, 8, 10, 13, 17, 22, 30, 40, 55, 75]           # the big blind at each level, in first big blinds


_NICE_BLINDS = sorted({a * 10 ** k for a in (20, 25, 30, 40, 50, 60, 80) for k in range(0, 4)} | {120 * 10 ** k for k in range(0, 3)}
                      | {160 * 10 ** k for k in range(0, 3)} | {240 * 10 ** k for k in range(0, 3)})       # round, even big blinds
_NICE_BLINDS = [b for b in _NICE_BLINDS if b <= 300000]


def _next_blind(target, prev):
    """The round big blind nearest to `target` that is above the previous level's."""
    return min((b for b in _NICE_BLINDS if b > prev), key=lambda b: abs(math.log(b / target)))


def structure(stake):
    """The tournament at this stake: starting chips, blind levels [[small blind, big blind, ante], ...] and hands per level."""
    keys = list(stakes.STAKES)
    i = int(round(keys.index(stake) / (len(keys) - 1) * (len(_STRUCTURES) - 1)))
    chips, first_bb, per, ante_from = _STRUCTURES[i]
    levels, bb = [], 0
    for n, step in enumerate(_BLIND_STEPS):
        bb = first_bb if n == 0 else _next_blind(first_bb * step, bb)
        ante = bb / 8.0
        ante = int(round(ante / 5.0) * 5) if ante >= 10 else int(round(ante))
        levels.append([bb // 2, bb, max(1, ante) if n + 1 >= ante_from else 0])
    return {"start_chips": chips, "hands_per_level": per, "ante_from": ante_from, "levels": levels}


# ---- the tournament ladder: its own progression, separate from the cash levels. You unlock the next level by finishing in the money at
# the one you are on (twice at the three lowest, three times above), so a player who only plays tournaments can climb. Only tournaments
# played in the Casino count (a result you typed in from a real site never unlocks anything).
def ladder_need(i, n):
    """How many in-the-money finishes clear level number i (none at the top level)."""
    return 0 if i >= n - 1 else 2 if i < 3 else 3


def ladder(rows, name_fn=None, cur="$"):
    """The ladder from your finished Casino tournaments (rows: stake, place, prize): one entry per stake with its status
    (cleared / current / locked), the in-the-money finishes so far and how many are needed."""
    keys = list(stakes.STAKES)
    n = len(keys)
    played, cashes, best = {}, {}, {}
    for r in rows:
        k = r["stake"]
        if k not in stakes.STAKES:
            continue
        played[k] = played.get(k, 0) + 1
        if r["prize"] > 0:
            cashes[k] = cashes.get(k, 0) + 1
        best[k] = min(best.get(k, 10 ** 9), r["place"])
    current = next((i for i, k in enumerate(keys[:-1]) if cashes.get(k, 0) < ladder_need(i, n)), n - 1)
    out = []
    for i, k in enumerate(keys):
        fee = stakes.buy_in_range(k)[1]
        sc = structure(k)
        out.append({"key": k, "name": name_fn(k) if name_fn else stakes.blinds_text(k, cur), "blinds": stakes.blinds_text(k, cur),
                    "status": "cleared" if i < current else "current" if i == current else "locked",
                    "need": ladder_need(i, n), "cashes": cashes.get(k, 0), "played": played.get(k, 0), "best": best.get(k),
                    "entry": stakes.money(fee, cur), "entry_raw": fee,
                    "chips": f"{sc['start_chips']:,} chips ({sc['start_chips'] // sc['levels'][0][1]}bb)"})
    return out


def ladder_locked_message(levels, key, account, cur="$"):
    """Why you cannot enter a tournament at this level (None if you can): not unlocked yet, or the entry is more than your account."""
    lv = next(x for x in levels if x["key"] == key)
    if lv["status"] == "locked":
        now = next(x for x in levels if x["status"] == "current")
        return (f"{lv['name']} tournaments are locked: finish in the money {now['need']} times at {now['name']} first "
                f"({now['cashes']} so far).")
    if account < lv["entry_raw"]:
        return f"The entry is {lv['entry']} and you have {stakes.money(account, cur)}."
    return None


def paid_places(size):
    return max(1, min(6, round(PAID_SHARE * size)))


def prize_list(pool, size):
    """Prize for 1st, 2nd, ... (top-heavy), adding up to the pool."""
    w = _WEIGHTS.get(paid_places(size)) or _WEIGHTS[6]
    prizes = [int(pool * x) for x in w]
    prizes[0] += pool - sum(prizes)                        # the odd chip goes to first
    return prizes


def new_field(stake, strength, rng, size=FIELD_SIZE, start_chips=3000):
    """A fresh field: you (player 0) plus size-1 bots drawn for this stake, in tables of six with you at table 0."""
    bots = []
    while len(bots) < size - 1:
        bots += stakes.population(stake, rng, n=5, strength=strength)
    bots = bots[:size - 1]
    names = set()
    for b in bots:                                         # nobody shares a name in the field
        if b["persona"]["name"] in names:
            b["persona"] = personas.pick(b["style"], rng, names)
        names.add(b["persona"]["name"])
    players = {"0": {"id": 0, "hero": True, "stack": start_chips, "place": None}}
    for i, b in enumerate(bots, 1):
        players[str(i)] = {"id": i, "stack": start_chips, "place": None, "style": b["style"], "loose_mult": b["loose_mult"],
                           "traits": b["traits"], "persona": b["persona"]}
    n_tables = math.ceil(size / TABLE_SIZE)
    tables = [[] for _ in range(n_tables)]
    for pid in range(size):
        tables[pid % n_tables].append(pid)
    return {"size": size, "left": size, "start_chips": start_chips, "players": players, "tables": tables,
            "buttons": [0] * n_tables, "hero_table": 0, "moves": 0}


def hero_table_ids(f):
    return [p for p in f["tables"][f["hero_table"]] if p != 0]


def _stack(f, pid):
    return f["players"][str(pid)]["stack"]


def play_other_tables(f, rng, sb, bb, ante=0):
    """One hand at every table except yours. Returns [(player id, chips at the start of the hand)] for everyone who went out."""
    busted = []
    for ti, ids in enumerate(f["tables"]):
        if ti == f["hero_table"] or len(ids) < 2:
            continue
        out = set(range(len(ids), TABLE_SIZE))
        button = f["buttons"][ti] % TABLE_SIZE
        while button in out:
            button = (button + 1) % TABLE_SIZE
        stacks = [_stack(f, pid) for pid in ids] + [0] * len(out)
        start = list(stacks)
        h = Hand(stacks, button=button, sb=sb, bb=bb, seed=rng.randrange(1 << 30), out=out, ante=ante)
        bots = [Bot(f["players"][str(pid)]["style"], seed=rng.randrange(1 << 30), sims=BG_SIMS,
                    loose_mult=f["players"][str(pid)]["loose_mult"], traits=f["players"][str(pid)]["traits"]) for pid in ids]
        while not h.over:
            s = h.to_act
            h.act(s, bots[s].decide(h, s))
        for seat, pid in enumerate(ids):
            f["players"][str(pid)]["stack"] = h.stack[seat]
            if h.stack[seat] <= 0:
                busted.append((pid, start[seat]))
        f["buttons"][ti] = (button + 1) % TABLE_SIZE
    return busted


def assign_places(f, busted):
    """Give everyone who went out this round a finishing place. `busted` = [(player id, chips at the start of the hand)]."""
    left = f["left"] - len(busted)
    for k, (pid, _) in enumerate(sorted(busted, key=lambda x: -x[1])):
        f["players"][str(pid)]["place"] = left + 1 + k
    f["left"] = left
    return left


def rebalance(f, rng):
    """Take out the busted, break tables that are no longer needed, and even out the rest. Returns what happened (for the news)."""
    events = []
    for t in f["tables"]:
        t[:] = [p for p in t if f["players"][str(p)]["place"] is None]
    needed = max(1, math.ceil(f["left"] / TABLE_SIZE))
    while len(f["tables"]) > needed:
        idx = min(range(len(f["tables"])), key=lambda i: (len(f["tables"][i]), -i))          # the smallest table goes
        broken = f["tables"].pop(idx)
        f["buttons"].pop(idx)
        events.append({"kind": "break", "hero": 0 in broken})
        for pid in broken:
            tgt = max(range(len(f["tables"])), key=lambda i: (TABLE_SIZE - len(f["tables"][i]), rng.random()))
            f["tables"][tgt].append(pid)
            events.append({"kind": "move", "id": pid, "table": tgt, "reason": "break"})
    while True:                                           # keep the tables within one player of each other
        sizes = [len(t) for t in f["tables"]]
        hi, lo = sizes.index(max(sizes)), sizes.index(min(sizes))
        if sizes[hi] - sizes[lo] < 2:
            break
        movable = [p for p in f["tables"][hi] if p != 0]
        if not movable:
            break
        pid = rng.choice(movable)
        f["tables"][hi].remove(pid)
        f["tables"][lo].append(pid)
        events.append({"kind": "move", "id": pid, "table": lo, "reason": "balance"})
    f["hero_table"] = next(i for i, t in enumerate(f["tables"]) if 0 in t)
    f["buttons"] = (f["buttons"] + [0] * len(f["tables"]))[:len(f["tables"])]
    return events
