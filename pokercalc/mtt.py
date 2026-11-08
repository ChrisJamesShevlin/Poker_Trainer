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


def play_other_tables(f, rng, sb, bb):
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
        h = Hand(stacks, button=button, sb=sb, bb=bb, seed=rng.randrange(1 << 30), out=out)
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
