"""The Grind ladder: clear each no-limit level in the trainer, in order, and collect badges on the way up.

Everything is worked out from the trainer's saved hands (store.trainer_hands), so there is nothing extra to save. Hands are
replayed in the order they were played. Only hands at the level you are currently on count, and only if the table was at
least as tough as that level asks for (the harder levels will not count hands played at loose tables). A level is cleared
at the first check where ALL of these hold (checks happen every CHECK_EVERY hands once you have the minimum):
  - enough hands at the level,
  - a win rate at or above the level's target (in big blinds per 100 hands),
  - you play like a winner: VPIP in a healthy range and PFR high enough (so you cannot pass by playing every hand),
  - you used the calculator (the manual, RTA-style page) and followed it: enough decisions logged in its sessions at this
    level's blinds, and a high enough share of them matching what it said.

The targets below are our own starting numbers, not measured from real players: edit RULES to make it stricter or easier.
A win rate over a few thousand hands is still mostly luck, so each level also shows the margin of error. Beating bots is
necessary practice, not proof that you beat real people.
"""
import json
import math
import time

from . import mtt, personas, stakes, store

CHECK_EVERY = 250                  # a level is only checked every this many hands, so one lucky run can't pass it
SESSION_GAP_S = 30 * 60            # a pause longer than this starts a new session
STRENGTH_RANK = {"very_loose": 0, "loose": 1, "balanced": 2, "tough": 3, "hard": 4}
VPIP_RANGE = (14.0, 35.0)          # percent of hands you put money in preflop, as a winner would
MIN_PFR = 10.0                     # percent of hands you raise preflop
RTA_MIN_DECISIONS = 100            # decisions logged in the calculator at this level before it can be cleared
RTA_MIN_FOLLOW = 70.0              # percent of those decisions that matched what the calculator said

# (hands, bb/100 target) for each level, in ladder order. Targets shrink as the games get tougher, like real win rates.
_HANDS = [1000 + 500 * i for i in range(15)]
_TARGET = [10, 9.5, 9, 8, 7, 6, 5, 4.5, 4, 3.5, 3, 2.5, 2.5, 2, 2]
_MIN_STRENGTH = ["very_loose"] * 3 + ["loose"] * 3 + ["balanced"] * 3 + ["tough"] * 3 + ["hard"] * 3     # each level's table strength (and the least tough one that counts)
BADGE_NAMES = ["Puddle", "Pond", "Creek", "River", "Lake", "Bay", "Reef", "Deep Water", "Open Sea", "Trench", "Abyss",
               "Leviathan", "Apex", "Kraken", "Poseidon"]
RULES = {}
for _i, _key in enumerate(stakes.STAKES):
    RULES[_key] = dict(hands=_HANDS[_i], target=_TARGET[_i], min_strength=_MIN_STRENGTH[_i], badge=BADGE_NAMES[_i])


def shark_badge_id(name):
    return "shark-" + name.lower().replace(" ", "-").replace(".", "")


# ---- badges are poker chips. The chip value is how hard the badge is, and the chip stack is your "gamerscore".
BADGE_VERSION = 3              # bump when badges are renamed or re-valued: the first look afterwards records what you have without pop-ups
CHIP_TEXT = {1: "$1", 5: "$5", 25: "$25", 100: "$100", 500: "$500", 1000: "$1K", 5000: "$5K", 25000: "$25K", 100000: "$100K", 1000000: "100%"}
RARITY = {1: "Common", 5: "Common", 25: "Uncommon", 100: "Rare", 500: "Epic", 1000: "Epic", 5000: "Legendary", 25000: "Legendary",
          100000: "Mythic", 1000000: "Solid gold"}
LEVEL_CHIPS = [1, 5, 5, 25, 25, 100, 100, 500, 500, 1000, 1000, 5000, 5000, 25000, 100000]      # clearing a level, NL2 up to NL100,000
GROUP_ORDER = ["Getting started", "Levels", "Pots", "Showdowns", "Bluffs", "All-ins", "Beat the sharks", "Big hands", "Endurance", "Tournaments", "RTA", "Secret"]


def _is_72_offsuit(hole):
    return len(hole) == 2 and {hole[0][0], hole[1][0]} == {"7", "2"} and hole[0][1] != hole[1][1]


def _hero_folded_preflop(actions):
    try:
        log = json.loads(actions) if actions else []
    except (ValueError, TypeError):
        return False
    return any(a[0] == "preflop" and a[1] == 0 and a[2] == "fold" for a in log)


def level_name(stake):
    return f"NL{stakes.STAKES[stake]['bb']:,}"


def _vpip_pfr(actions):
    """(voluntarily put money in preflop, raised preflop) for the hero (seat 0) from a saved action log."""
    try:
        log = json.loads(actions) if actions else []
    except (ValueError, TypeError):
        return False, False
    pre = [a for a in log if a[0] == "preflop" and a[1] == 0]
    return any(a[2] in ("call", "raise") for a in pre), any(a[2] == "raise" for a in pre)


def table_mix_text(stake):
    """What a typical table looks like at this level, as played (the level's own table strength): average number of each kind of player."""
    m = stakes.expected_mix(stake, RULES[stake]["min_strength"])
    return {t: int(m[t] * 10 + 0.5) / 10 for t in ("shark", "rec", "fish", "whale")}       # one decimal, halves rounded up (0.25 shows as 0.3)


# ---- reading a saved hand: bluffs and who you played against
def _cards(text):
    return text.split() if text else []


def is_bluff(hole, board, actions, showdown, net):
    """A bluff: you bet or raised after the flop, everyone folded (no showdown), and you held nothing of your own
    (no pair or better using a hole card). Flush and straight draws that missed count too."""
    if showdown or net <= 0:
        return False
    hole, board = _cards(hole), _cards(board)
    if len(board) < 3 or len(hole) != 2:
        return False
    try:
        log = json.loads(actions) if actions else []
    except (ValueError, TypeError):
        return False
    if not any(a[0] != "preflop" and a[1] == 0 and a[2] == "raise" for a in log):
        return False
    from .game import _CARDS
    import eval7
    kind = eval7.handtype(eval7.evaluate([_CARDS[c] for c in hole + board]))
    if kind == "High Card":
        return True
    if kind == "Pair":
        ranks = [c[0] for c in hole + board]
        paired = next(r for r in set(ranks) if ranks.count(r) == 2)
        return paired not in (hole[0][0], hole[1][0])              # the pair is on the board, not yours
    return False


def hand_meta(t):
    """What the achievements need to know about the hand that just finished (saved with it)."""
    h, res = t.hand, t.hand.result
    shown = res.get("hands", {}) if res["showdown"] else {}
    vs = [{"name": t.bots[s - 1]["persona"]["name"], "type": stakes.player_type(t.bots[s - 1]["style"]),
           "allin": bool(h.allin[s])} for s in sorted(shown) if s != 0]
    hero_hand = t.last.get("hands", {}).get(0) if 0 in shown else None
    return {"hero_allin": bool(h.allin[0]), "showdown": bool(res["showdown"]), "hero_hand": hero_hand, "vs": vs}


def _meta(raw):
    try:
        return json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}


def _stats(acc):
    n = acc["n"]
    if not n:
        return dict(hands=0, bb100=None, margin=None, vpip=None, pfr=None)
    mean = acc["sum"] / n
    var = max(0.0, acc["sq"] / n - mean * mean)
    se = math.sqrt(var / n)
    return dict(hands=n, bb100=round(100 * mean, 2), margin=round(196 * se, 1),     # 1.96 standard errors, in bb/100
                vpip=round(100 * acc["vpip"] / n, 1), pfr=round(100 * acc["pfr"] / n, 1))


def stake_for_bb(bb):
    """The trainer stake whose big blind is `bb` (in money, as typed into the calculator's session), or None."""
    return next((k for k, v in stakes.STAKES.items() if abs(v["bb"] - bb * 100) < 0.01), None) if bb else None


def reset_time(con):
    """When Grinding progress was last reset (0 = never). Hands saved before this are kept but no longer count."""
    return store.kv_load(con, "grind_reset_ts", 0.0)


def _rta_rows(con):
    """What the calculator logged: {stake: [(ts, decisions, followed), ...]} from its sessions and hands."""
    out = {}
    for r in con.execute("SELECT h.ts AS ts, s.bb AS bb, h.decisions AS d FROM hands h JOIN sessions s ON s.id = h.session_id "
                         "WHERE h.decisions IS NOT NULL AND h.ts > ? AND COALESCE(h.practice, 0) = 0 AND COALESCE(h.source, 'rta') != 'casino' ORDER BY h.ts", (reset_time(con),)):
        key = stake_for_bb(r["bb"])
        try:
            ds = json.loads(r["d"]) or []
        except (ValueError, TypeError):
            continue
        if key and ds:
            out.setdefault(key, []).append((r["ts"], len(ds), sum(1 for d in ds if d.get("suggested") == d.get("taken"))))
    return out


def _rta_stats(rows, start, end=float("inf")):
    sel = [x for x in rows if start <= x[0] <= end]
    n, f = sum(x[1] for x in sel), sum(x[2] for x in sel)
    return dict(rta_hands=len(sel), rta_decisions=n, rta_follow=round(100 * f / n, 1) if n else None)


def _new():
    return dict(n=0, sum=0.0, sq=0.0, vpip=0, pfr=0)


def _passes(st, rule):
    checks = {
        "hands": st["hands"] >= rule["hands"],
        "winrate": st["bb100"] is not None and st["bb100"] >= rule["target"],
        "vpip": st["vpip"] is not None and VPIP_RANGE[0] <= st["vpip"] <= VPIP_RANGE[1],
        "pfr": st["pfr"] is not None and st["pfr"] >= MIN_PFR,
        "rta_n": st.get("rta_decisions", 0) >= RTA_MIN_DECISIONS,
        "rta_follow": st.get("rta_follow") is not None and st["rta_follow"] >= RTA_MIN_FOLLOW,
    }
    return checks, all(checks.values())


def summary(con, cur="$"):
    keys = list(stakes.STAKES)
    rows = con.execute("SELECT ts, stake, net, actions, strength, pot, hole, board, showdown, meta, COALESCE(practice, 0) AS practice FROM trainer_hands WHERE ts > ? AND COALESCE(practice, 0) IN (0, 2) ORDER BY ts, id", (reset_time(con),)).fetchall()
    lvl = 0                                       # index of the level you are on
    rta = _rta_rows(con)                          # what the calculator logged, per stake
    tours = [dict(r) for r in con.execute("SELECT * FROM tournaments WHERE ts > ? ORDER BY ts, id", (reset_time(con),))]
    start = [0.0] * (len(keys) + 1)               # when each level became yours (RTA decisions before that do not count)
    acc = [_new() for _ in keys]
    cleared = {}                                  # stake -> what you had when you cleared it
    off_ladder = too_easy = 0
    total = 0
    sessions, last_ts, sess_net = [], None, 0.0
    biggest = 0.0
    won = showdowns_won = bluffs = allin_wins = 0
    allin_by_type, allin_by_name, showy = {}, {}, {}
    shark_idx, sd_run, best_sd_run = set(), 0, 0              # levels where you beat a shark all-in; showdowns won in a row
    seven_deuce = folded_aces = bad_beats = coolers = stacked = night = 0
    sess_n = max_sess_n = 0
    for r in rows:
        key = r["stake"]
        if key not in stakes.STAKES:
            continue
        bb = stakes.STAKES[key]["bb"]
        x = r["net"] / bb
        tour = r["practice"] == 2             # a tournament hand: it counts for the badges about the cards and how you played the hand, not for levels, sessions or money
        if not tour:
            total += 1
            biggest = max(biggest, x)
        m = _meta(r["meta"])
        hole = (r["hole"] or "").split()
        name = (m.get("hero_hand") or "").lower()
        if not tour and x <= -100:
            stacked += 1
        if not tour and time.localtime(r["ts"]).tm_hour in (2, 3, 4, 5):
            night += 1
        if len(hole) == 2 and hole[0][0] == "A" and hole[1][0] == "A" and _hero_folded_preflop(r["actions"]):
            folded_aces += 1
        if r["showdown"]:
            sd_run = sd_run + 1 if x > 0 else 0
            best_sd_run = max(best_sd_run, sd_run)
            if x < 0 and name.startswith(("full house", "four of a kind", "straight flush", "royal flush")):
                bad_beats += 1
                coolers += name.startswith(("four of a kind", "straight flush", "royal flush"))
        if x > 0:
            won += not tour
            showdowns_won += bool(r["showdown"])
            bluffs += is_bluff(r["hole"], r["board"], r["actions"], r["showdown"], x)
            seven_deuce += bool(r["showdown"]) and _is_72_offsuit(hole)
            if m.get("hero_allin") and m.get("showdown"):
                allin_wins += 1
                for v in ([] if tour else m.get("vs", [])):
                    allin_by_type[v["type"]] = allin_by_type.get(v["type"], 0) + 1
                    allin_by_name[v["name"]] = allin_by_name.get(v["name"], 0) + 1
                    if v["type"] == "shark":
                        shark_idx.add(keys.index(key))
            for k, word in (("flush", "flush"), ("full-house", "full house"), ("quads", "four of a kind"), ("straight-flush", "straight flush"), ("royal", "royal flush")):
                if r["showdown"] and name.startswith(word):
                    showy[k] = showy.get(k, 0) + 1
        if tour:
            continue
        if last_ts is not None and r["ts"] - last_ts > SESSION_GAP_S:
            sessions.append(sess_net)
            sess_net = 0.0
            max_sess_n, sess_n = max(max_sess_n, sess_n), 0
        sess_net += x
        sess_n += 1
        last_ts = r["ts"]
        if lvl >= len(keys) or key != keys[lvl]:
            off_ladder += 1
            continue
        rule = RULES[key]
        if STRENGTH_RANK.get(r["strength"] or "balanced", 2) < STRENGTH_RANK[rule["min_strength"]]:
            too_easy += 1
            continue
        a = acc[lvl]
        v, p = _vpip_pfr(r["actions"])
        a["n"] += 1
        a["sum"] += x
        a["sq"] += x * x
        a["vpip"] += v
        a["pfr"] += p
        if a["n"] >= rule["hands"] and a["n"] % CHECK_EVERY == 0:
            st = dict(_stats(a), **_rta_stats(rta.get(key, []), start[lvl], r["ts"]))
            if _passes(st, rule)[1]:
                cleared[key] = dict(st, ts=r["ts"])
                lvl += 1
                start[lvl] = r["ts"]
    if last_ts is not None:
        sessions.append(sess_net)
    max_sess_n = max(max_sess_n, sess_n)

    levels = []
    for i, key in enumerate(keys):
        rule = RULES[key]
        status = "cleared" if key in cleared else "current" if i == lvl else "locked"
        st = cleared.get(key) or dict(_stats(acc[i]), **(_rta_stats(rta.get(key, []), start[i]) if status == "current" else _rta_stats([], 0)))
        checks = _passes(st, rule)[0]
        levels.append(dict(
            key=key, name=level_name(key), blinds=stakes.blinds_text(key, cur), tier=stakes.STAKES[key]["tier"],
            status=status, badge=rule["badge"], need_hands=rule["hands"], target=rule["target"],
            min_strength=rule["min_strength"], mix=table_mix_text(key),
            hands=st["hands"], bb100=st["bb100"], margin=st["margin"], vpip=st["vpip"], pfr=st["pfr"],
            rta_decisions=st["rta_decisions"], rta_follow=st["rta_follow"], need_rta=RTA_MIN_DECISIONS, need_follow=RTA_MIN_FOLLOW,
            chip=LEVEL_CHIPS[i], chip_text=CHIP_TEXT[LEVEL_CHIPS[i]],
            checks=checks if status != "locked" else {},
            bankroll=stakes.money(30 * stakes.buy_in_range(key)[1], cur)))      # 30 buy-ins, the usual cushion

    n_cleared = len(cleared)
    streak = best = 0
    for sn in sessions:
        streak = streak + 1 if sn > 0 else 0
        best = max(best, streak)
    hard_cleared = any(RULES[k]["min_strength"] == "hard" for k in cleared)
    ahead = any(c["bb100"] is not None and c["bb100"] >= 2 * RULES[k]["target"] for k, c in cleared.items())
    perfect = any((c.get("rta_follow") or 0) >= 95 and c.get("rta_decisions", 0) >= 500 for c in cleared.values())
    rta_all = [x for rows in rta.values() for x in rows]
    rta_n, rta_f = sum(x[1] for x in rta_all), sum(x[2] for x in rta_all)
    sharks_beaten = len([n for n in allin_by_name if personas.STYLE_OF.get(n) == "reg"])
    top = max(shark_idx) if shark_idx else -1

    badges = []
    # ---- tournaments: from the results saved when each one ended
    won_t = [r for r in tours if r["place"] == 1]
    wins_row = best_row = 0
    for r in tours:
        wins_row = wins_row + 1 if r["place"] == 1 else 0
        best_row = max(best_row, wins_row)
    win_idx = max((keys.index(r["stake"]) for r in won_t), default=-1)

    def bd(group, id_, name, text, chip, have, goal=1, secret=False, hint="", kind="achievement"):
        badges.append(dict(id=id_, name=name, text=text, kind=kind, group=group, chip=chip, chip_text=CHIP_TEXT[chip], rarity=RARITY[chip],
                           secret=secret, hint=hint, earned=have >= goal, progress=min(have, goal), goal=goal))
    plural = lambda n, w: f"{n:,} {w}{'' if n == 1 else 's'}"

    G = "Getting started"
    bd(G, "sat-down", "Sat down", "Play your first hand", 1, total)
    bd(G, "pots-1", "First pot", "Win your first pot", 1, won)
    bd(G, "showdown-1", "First showdown win", "Win your first showdown", 1, showdowns_won)
    bd(G, "bluffs-1", "First bluff", "Win a pot with a bluff: bet after the flop, everyone folds, and you had nothing", 1, bluffs)
    bd(G, "rta-1", "First logged hand", "Log a hand in the RTA", 1, len(rta_all))
    bd(G, "allin-1", "All in and won", "Go all-in and win at showdown", 1, allin_wins)

    for i, k in enumerate(keys):
        bd("Levels", f"level-{k}", RULES[k]["badge"], f"Clear {level_name(k)}", LEVEL_CHIPS[i], 1 if k in cleared else 0, kind="level")
    bd("Levels", "climber", "Climber", "Clear 3 levels", 25, n_cleared, 3)
    bd("Levels", "halfway", "Halfway there", "Clear 8 levels", 100, n_cleared, 8)
    bd("Levels", "hard-mode", "Hard mode", "Clear a level that needs hard tables", 500, 1 if hard_cleared else 0)
    bd("Levels", "ahead", "Ahead of the curve", "Clear a level with a win rate at least double its target", 100, 1 if ahead else 0)
    bd("Levels", "summit", "Summit", "Clear every level up to NL100,000", 25000, n_cleared, len(keys))

    for n, name, chip in ((10, "Ten pots", 5), (100, "Hundred pots", 25), (500, "Pot machine", 100), (2500, "Pot baron", 500), (10000, "Pot tycoon", 1000)):
        bd("Pots", f"pots-{n}", name, f"Win {plural(n, 'pot')}", chip, won, n)
    for n, name, chip in ((10, "Showdown regular", 5), (50, "Showdown master", 25), (250, "Showdown veteran", 100), (1000, "Showdown legend", 500)):
        bd("Showdowns", f"showdown-{n}", name, f"Win {plural(n, 'showdown')}", chip, showdowns_won, n)
    bd("Showdowns", "sd-streak-3", "Hat trick", "Win 3 showdowns in a row", 25, best_sd_run, 3)
    bd("Showdowns", "sd-streak-5", "On a roll", "Win 5 showdowns in a row", 100, best_sd_run, 5)
    for n, name, chip in ((5, "Five bluffs", 5), (10, "Ten bluffs", 25), (25, "Serial bluffer", 100), (50, "Bluff boss", 500),
                          (100, "Con artist", 1000), (250, "Master of deception", 5000)):
        bd("Bluffs", f"bluffs-{n}", name, f"Win {plural(n, 'pot')} with a bluff", chip, bluffs, n)

    bd("All-ins", "allin-10", "Shover", "Go all-in and win at showdown 10 times", 25, allin_wins, 10)
    bd("All-ins", "allin-50", "All-in artist", "Go all-in and win at showdown 50 times", 100, allin_wins, 50)
    bd("All-ins", "allin-200", "Fearless", "Go all-in and win at showdown 200 times", 500, allin_wins, 200)
    for t, name, chip in (("whale", "Whale hunter", 5), ("fish", "Fish fryer", 5), ("rec", "Rec wrecker", 25), ("shark", "Shark slayer", 100)):
        bd("All-ins", f"allin-{t}", name, f"Go all-in against a {stakes.PLAYER_TYPES[t]['label']} and win at showdown", chip, allin_by_type.get(t, 0))

    for name in personas.SHARKS:
        bd("Beat the sharks", shark_badge_id(name), name, f"Go all-in against {name} (a shark) and win at showdown", 25, allin_by_name.get(name, 0))
    bd("Beat the sharks", "sharks-5", "Shark hunter", "Beat 5 different sharks all-in at showdown", 100, sharks_beaten, 5)
    bd("Beat the sharks", "sharks-10", "Shark tamer", "Beat 10 different sharks all-in at showdown", 500, sharks_beaten, 10)
    bd("Beat the sharks", "sharks-20", "Top of the food chain", "Beat every one of the 20 sharks all-in at showdown", 5000, sharks_beaten, len(personas.SHARKS))
    bd("Beat the sharks", "deep-water", "Deep water", f"Beat a shark all-in at showdown at {level_name(keys[9])} or higher", 1000, 1 if top >= 9 else 0)
    bd("Beat the sharks", "nosebleed", "Nosebleed", f"Beat a shark all-in at showdown at {level_name(keys[12])} or higher", 5000, 1 if top >= 12 else 0)
    bd("Beat the sharks", "king-of-sharks", "King of the sharks",
       f"Beat a shark all-in at showdown at {level_name(keys[14])}, where the strongest sharks of all play", 100000, 1 if top >= 14 else 0)

    for k, name, text, chip in (("flush", "Flush!", "Win a showdown with a flush", 25), ("full-house", "Full house", "Win a showdown with a full house", 100),
                                ("quads", "Quads", "Win a showdown with four of a kind", 500), ("straight-flush", "Straight flush", "Win a showdown with a straight flush", 5000)):
        bd("Big hands", f"hand-{k}", name, text, chip, showy.get(k, 0) + (showy.get("royal", 0) if k == "straight-flush" else 0))
    for n, name, chip in ((100, "Century", 25), (200, "Double century", 100), (500, "Monster pot", 500), (1000, "Whale of a pot", 5000)):
        bd("Big hands", f"big-{n}", name, f"Win a single hand worth {n:,} big blinds or more", chip, 1 if biggest >= n else 0)

    for n, name, chip in ((1000, "1,000 hands", 5), (5000, "5,000 hands", 25), (10000, "10,000 hands", 100), (25000, "25,000 hands", 500),
                          (50000, "50,000 hands", 1000), (100000, "100,000 hands", 5000)):
        bd("Endurance", f"hands-{n // 1000}k", name, f"Play {n:,} hands in the Casino while Grinding", chip, total, n)
    bd("Endurance", "hot-streak", "Hot streak", "Win 3 sessions in a row", 25, best, 3)
    bd("Endurance", "hot-streak-5", "On fire", "Win 5 sessions in a row", 100, best, 5)
    bd("Endurance", "hot-streak-10", "Unstoppable", "Win 10 sessions in a row", 500, best, 10)
    bd("Endurance", "marathon", "Marathon", "Play 200 hands in one session", 25, max_sess_n, 200)
    bd("Endurance", "iron-man", "Iron man", "Play 500 hands in one session", 100, max_sess_n, 500)

    for n, name, chip in ((100, "100 decisions", 5), (500, "500 decisions", 25), (2000, "2,000 decisions", 100), (10000, "10,000 decisions", 500)):
        bd("RTA", f"rta-{n}", name, f"Log {n:,} decisions in the RTA", chip, rta_n, n)
    for n, name, chip in ((50, "By the book", 5), (250, "Book smart", 25), (1000, "Textbook", 100), (5000, "Walking manual", 500)):
        bd("RTA", f"follow-{n}", name, f"Follow the RTA's advice {n:,} times", chip, rta_f, n)
    bd("RTA", "disciplined", "Disciplined", "Follow the advice at least 80% of the time over 200+ decisions", 100, 1 if rta_n >= 200 and rta_f / rta_n >= 0.8 else 0)
    bd("RTA", "perfectionist", "Perfectionist", "Clear a level following the advice 95% of the time over 500+ decisions", 500, 1 if perfect else 0)

    T = "Tournaments"
    nt = len(tours)
    bd(T, "tour-1", "Registered", "Play a multi-table tournament", 1, nt)
    bd(T, "tour-final", "Final table", "Reach the final table of a tournament", 5, sum(1 for r in tours if r["final_table"]), 1)
    bd(T, "tour-cash", "In the money", "Finish in a paid place", 25, sum(1 for r in tours if r["prize"] > 0), 1)
    bd(T, "tour-hu", "Heads-up", "Reach heads-up play for the title", 100, sum(1 for r in tours if r["heads_up"]), 1)
    bd(T, "tour-podium", "On the podium", "Finish in the top three", 100, sum(1 for r in tours if r["place"] <= 3), 1)
    bd(T, "tour-win", "Champion", "Win a multi-table tournament", 500, len(won_t), 1)
    bd(T, "tour-win-5", "Serial winner", "Win 5 tournaments", 1000, len(won_t), 5)
    bd(T, "tour-win-10", "Tournament legend", "Win 10 tournaments", 5000, len(won_t), 10)
    bd(T, "tour-ko-3", "Bounty hunter", "Knock out 3 players in one tournament", 25, max((r["knockouts"] for r in tours), default=0), 3)
    bd(T, "tour-ko-6", "Terminator", "Knock out 6 players in one tournament", 500, max((r["knockouts"] for r in tours), default=0), 6)
    bd(T, "tour-short", "Short stack hero", "Win a tournament after dropping below 5 big blinds", 500,
       sum(1 for r in won_t if r["min_bb"] < 5), 1)
    bd(T, "tour-sharks", "Shark tank", "Win a tournament with 15 or more sharks in the field", 100,
       sum(1 for r in won_t if r["sharks"] >= 15), 1)
    bd(T, "tour-long", "Long haul", "Finish in the money after 150 hands or more", 100,
       sum(1 for r in tours if r["prize"] > 0 and r["hands"] >= 150), 1)
    bd(T, "tour-back", "Back to back", "Win two tournaments in a row", 1000, best_row, 2)
    bd(T, "tour-deep", "Deep stakes champion", f"Win a tournament at {level_name(keys[9])} or higher", 5000, 1 if win_idx >= 9 else 0)
    bd(T, "tour-nose", "Nosebleed champion", f"Win a tournament at {level_name(keys[12])} or higher", 25000, 1 if win_idx >= 12 else 0)
    bd(T, "tour-boss", "The final boss", f"Win a tournament at {level_name(keys[14])}, against a field of the strongest sharks", 100000, 1 if win_idx >= 14 else 0)

    S = "Secret"
    bd(S, "tour-bubble", "Bubble boy", "Finish one place short of the money", 5, sum(1 for r in tours if r["place"] == mtt.paid_places(r["size"]) + 1),
       1, secret=True, hint="So close, and yet so far.")
    bd(S, "tour-nomad", "Nomad", "Be moved to a different table 4 times in one tournament", 25, max((r["table_moves"] for r in tours), default=0), 4,
       secret=True, hint="Some players never stay put.")
    bd(S, "seven-deuce", "Seven-deuce", "Win a showdown with 7-2 offsuit, the worst starting hand", 25, seven_deuce, secret=True, hint="Some hands should never win. Prove them wrong.")
    bd(S, "fold-aces", "Why though?", "Fold pocket aces before the flop", 5, folded_aces, secret=True, hint="Folding the best hand has a price.")
    bd(S, "bad-beat", "Bad beat", "Lose a showdown with a full house or better", 25, bad_beats, secret=True, hint="Even a full house can lose.")
    bd(S, "cooler", "Cooler", "Lose a showdown with four of a kind or better", 100, coolers, secret=True, hint="Four of a kind is not always enough.")
    bd(S, "stacked", "Stacked", "Lose 100 big blinds or more in a single hand", 5, stacked, secret=True, hint="It happens to everyone.")
    bd(S, "night-owl", "Night owl", "Play a hand between 2am and 5am", 1, night, secret=True, hint="Poker never sleeps.")
    bd(S, "sd-streak-10", "Untouchable", "Win 10 showdowns in a row", 1000, best_sd_run, 10, secret=True, hint="Showdown after showdown after showdown.")
    bd(S, "royal", "Royalty", "Win a showdown with a royal flush", 25000, showy.get("royal", 0), secret=True, hint="The best hand there is.")

    others = [b for b in badges]
    done = sum(1 for b in others if b["earned"])
    badges.append(dict(id="completionist", name="100% Completionist", kind="completionist", group="Completionist", chip=1000000, chip_text="100%",
                       rarity=RARITY[1000000], secret=False, hint="", earned=done == len(others), progress=done, goal=len(others),
                       text="Earn every other badge, secret ones included"))
    chips = sum(b["chip"] for b in badges if b["earned"])
    chips_all = sum(b["chip"] for b in badges)

    return dict(levels=levels, rta=dict(hands=len(rta_all), decisions=rta_n, followed=rta_f), badges=badges, chips=chips, chips_all=chips_all,
                group_order=GROUP_ORDER + ["Completionist"], total_hands=total, sessions=len(sessions), cleared=n_cleared,
                off_ladder=off_ladder, too_easy=too_easy, check_every=CHECK_EVERY,
                vpip_range=list(VPIP_RANGE), min_pfr=MIN_PFR)


# ---- what is new since you last looked: pop-ups when it happens, and a NEW tag on the Grinding page until it is viewed
def check_new(con, cur="$"):
    """Call after anything that can earn a badge. Returns the pop-ups to show now (newly earned badges and cleared levels);
    they are also remembered as pending, so the Grinding page can tag them NEW. The very first call only records what
    you already have, so nothing pops up for old progress."""
    g = summary(con, cur)
    earned = [b["id"] for b in g["badges"] if b["earned"]]
    known = store.kv_load(con, "grind_known")
    if known is None or store.kv_load(con, "grind_badge_version") != BADGE_VERSION:
        store.kv_save(con, "grind_known", earned)                     # first look, or the badges were changed: record, do not announce
        store.kv_save(con, "grind_badge_version", BADGE_VERSION)
        store.kv_save(con, "grind_pending", [])
        return []
    fresh = [b for b in g["badges"] if b["earned"] and b["id"] not in known]
    if not fresh:
        return []
    store.kv_save(con, "grind_known", earned)
    store.kv_save(con, "grind_pending", store.kv_load(con, "grind_pending", []) + [b["id"] for b in fresh])
    out = []
    for b in fresh:
        if b["kind"] == "level":
            nxt = next((lv for lv in g["levels"] if lv["status"] == "current"), None)
            text = (f"{nxt['name']} is now open. To sit there you need {nxt['bankroll']} in your account (30 buy-ins)."
                    if nxt and not all(lv["status"] == "cleared" for lv in g["levels"]) else "You have cleared every level!")
            out.append(dict(id=b["id"], kind="level", title=f"Level cleared: {b['text'].replace('Clear ', '')}", text=text, badge=b["name"],
                            chip=b["chip"], chip_text=b["chip_text"], rarity=b["rarity"]))
        else:
            title = "100% COMPLETE: solid gold chip!" if b["kind"] == "completionist" else f"{b['chip_text']} chip: {b['name']}"
            out.append(dict(id=b["id"], kind=b["kind"], title=title, text=f"{b['rarity']}. {b['text']}", badge=b["name"],
                            chip=b["chip"], chip_text=b["chip_text"], rarity=b["rarity"]))
    return out


def take_pending(con):
    """The ids earned since the Grinding page was last opened (and forget them, so a refresh clears the NEW tags)."""
    pending = store.kv_load(con, "grind_pending", [])
    if pending:
        store.kv_save(con, "grind_pending", [])
    return pending


def reset(con):
    """Start Grinding again from NL2: levels, badges and counts. Nothing is deleted: hands and sessions stay exactly as they
    are (so Analytics is untouched); they just stop counting towards the ladder."""
    import time
    store.kv_save(con, "grind_reset_ts", time.time())
    store.kv_save(con, "grind_known", [])
    store.kv_save(con, "grind_pending", [])
    store.kv_save(con, "grind_badge_version", BADGE_VERSION)
