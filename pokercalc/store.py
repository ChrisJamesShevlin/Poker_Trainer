"""SQLite storage for sessions and hands."""
import json
import os
import sqlite3
import time
from pathlib import Path

from . import stakes as _stakes

APP = "holdem"            # which app this copy is: hold'em and PLO keep separate data


def data_dir():
    """Where this app keeps its data when POKER_DATA_DIR is set: <that folder>/holdem/ (so the two apps never share one).
    Unset, the database stays in the project folder and backups go to backup/, as before."""
    base = os.environ.get("POKER_DATA_DIR")
    return Path(base).expanduser() / APP if base else None


def default_db():
    d = data_dir()
    return d / "poker.db" if d else Path(__file__).resolve().parent.parent / "poker.db"


DB = default_db()        # (the value when this module was imported; connect() looks it up fresh each time)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, start_ts REAL, end_ts REAL,
  balance_before REAL, buy_in REAL, bb REAL, balance_after REAL,
  deposit_before REAL DEFAULT 0, deposit_during REAL DEFAULT 0, source TEXT DEFAULT 'rta', format TEXT DEFAULT 'cash', start_chips REAL);
CREATE TABLE IF NOT EXISTS hands(
  id INTEGER PRIMARY KEY, session_id INTEGER, ts REAL,
  hole TEXT, board TEXT, pot REAL, call REAL, opps INTEGER, range_pct INTEGER,
  position TEXT, stack REAL, equity REAL, required REAL,
  suggested TEXT, taken TEXT, result REAL, decisions TEXT, practice INTEGER DEFAULT 0, source TEXT DEFAULT 'rta', trainer_hand_id INTEGER);
CREATE TABLE IF NOT EXISTS trainer_state(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tournaments(
  id INTEGER PRIMARY KEY, ts REAL, stake TEXT, place INTEGER, size INTEGER, buy_in REAL, prize REAL, hands INTEGER, knockouts INTEGER,
  level INTEGER, final_table INTEGER, heads_up INTEGER, min_bb REAL, table_moves INTEGER, sharks INTEGER,
  source TEXT DEFAULT 'casino', bb REAL);
CREATE TABLE IF NOT EXISTS trainer_hands(
  id INTEGER PRIMARY KEY, ts REAL, stake TEXT, hand_no INTEGER, position TEXT, hole TEXT, board TEXT,
  pot REAL, net REAL, showdown INTEGER, actions TEXT, strength TEXT, meta TEXT, practice INTEGER DEFAULT 0);
"""


def connect(path=None):
    if path is None:
        path = default_db()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
    for c in ("deposit_before", "deposit_during"):  # upgrade older databases
        if c not in cols:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {c} REAL DEFAULT 0")
    hcols = {r[1] for r in con.execute("PRAGMA table_info(hands)")}
    if "source" not in hcols:
        con.execute("ALTER TABLE hands ADD COLUMN source TEXT DEFAULT 'rta'")          # 'rta' typed in the RTA, 'casino' played in the Casino only, 'both' = one row for the same hand
    if "trainer_hand_id" not in hcols:
        con.execute("ALTER TABLE hands ADD COLUMN trainer_hand_id INTEGER")             # the Casino hand this row is (at most one row per Casino hand)
    if "source" not in {r[1] for r in con.execute("PRAGMA table_info(sessions)")}:
        con.execute("ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT 'rta'")         # 'casino' / 'practice' sessions are made by the Casino itself
    if "format" not in {r[1] for r in con.execute("PRAGMA table_info(sessions)")}:
        con.execute("ALTER TABLE sessions ADD COLUMN format TEXT DEFAULT 'cash'")        # 'tournament' = a tournament session you logged in the RTA (chips, not money)
    if "start_chips" not in {r[1] for r in con.execute("PRAGMA table_info(sessions)")}:
        con.execute("ALTER TABLE sessions ADD COLUMN start_chips REAL")
    tcols = {r[1] for r in con.execute("PRAGMA table_info(tournaments)")}
    if "source" not in tcols:
        con.execute("ALTER TABLE tournaments ADD COLUMN source TEXT DEFAULT 'casino'")   # 'casino' = played in the Casino, 'rta' = a real-site tournament you logged
    if "bb" not in tcols:
        con.execute("ALTER TABLE tournaments ADD COLUMN bb REAL")                         # the level's big blind in money (entry / 100), for the stake filter
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_hands_trainer ON hands(trainer_hand_id) WHERE trainer_hand_id IS NOT NULL")
    if "practice" not in {r[1] for r in con.execute("PRAGMA table_info(hands)")}:
        con.execute("ALTER TABLE hands ADD COLUMN practice INTEGER DEFAULT 0")    # 1 = logged while the Casino was in Practice: counts in Analytics, never towards Grinding
    if "decisions" not in {r[1] for r in con.execute("PRAGMA table_info(hands)")}:
        con.execute("ALTER TABLE hands ADD COLUMN decisions TEXT")   # one entry per button press: what the tool said vs what you did
    if "strength" not in {r[1] for r in con.execute("PRAGMA table_info(trainer_hands)")}:
        con.execute("ALTER TABLE trainer_hands ADD COLUMN strength TEXT")    # which table strength the hand was played at (NULL = before this was recorded)
    if "meta" not in {r[1] for r in con.execute("PRAGMA table_info(trainer_hands)")}:
        con.execute("ALTER TABLE trainer_hands ADD COLUMN meta TEXT")        # who you played against and whether you were all-in (for achievements)
    if "practice" not in {r[1] for r in con.execute("PRAGMA table_info(trainer_hands)")}:
        con.execute("ALTER TABLE trainer_hands ADD COLUMN practice INTEGER DEFAULT 0")   # 1 = a Practice hand, 2 = a tournament hand: neither counts towards Grinding
    con.commit()
    return con


def start_session(con, balance_before, buy_in, bb, deposit_before=0.0, fmt="cash", start_chips=None):
    """balance_before already includes any top-up; deposit_before says how much of it was a deposit.
    A tournament session (fmt="tournament") has an entry fee instead: buy_in = the entry, bb = entry / 100 (the level's big blind in money,
    the same scale the Casino uses for its tournament hands), and no balance."""
    cur = con.execute("INSERT INTO sessions(start_ts,balance_before,buy_in,bb,deposit_before,format,start_chips) VALUES(?,?,?,?,?,?,?)",
                      (time.time(), balance_before, buy_in, bb, deposit_before, fmt, start_chips))
    con.commit()
    return cur.lastrowid


def end_session(con, sid, balance_after, deposit_during=0.0):
    con.execute("UPDATE sessions SET end_ts=?, balance_after=?, deposit_during=? WHERE id=?",
                (time.time(), balance_after, deposit_during, sid))
    con.commit()


def end_tournament_session(con, sid, place, size, prize):
    """End a tournament session: how you finished and what you won. The Casino records its own tournaments, so if it already did for
    this session's time, nothing is added twice. Returns True if a result was recorded."""
    row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    con.execute("UPDATE sessions SET end_ts=? WHERE id=?", (time.time(), sid))
    done = con.execute("SELECT 1 FROM tournaments WHERE source='casino' AND ts >= ? AND ABS(COALESCE(bb, 0) - ?) < 1e-9 LIMIT 1",
                       (row["start_ts"], row["bb"])).fetchone()
    n = con.execute("SELECT COUNT(*) FROM hands WHERE session_id=?", (sid,)).fetchone()[0]
    if not done:
        con.execute("INSERT INTO tournaments(ts,stake,place,size,buy_in,prize,hands,knockouts,level,final_table,heads_up,min_bb,table_moves,sharks,source,bb) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), "", place, size, round(row["buy_in"] * 100), round(prize * 100), n, 0, 0, 0, 0, 0, 0, 0, "rta", row["bb"]))
    con.commit()
    return not done


def open_session(con):
    return con.execute("SELECT * FROM sessions WHERE end_ts IS NULL ORDER BY id DESC LIMIT 1").fetchone()


CASINO_WINDOW_S = 20 * 60          # a hand typed into the RTA is matched to a Casino hand played within this long
CASINO_SESSION_GAP_S = 30 * 60


def _flag(practice):
    """What the hands table stores for where a hand was played: 0 cash (Grinding), 1 Practice, 2 a tournament."""
    return 2 if practice == 2 else 1 if practice else 0


def _cards(x):
    return set(x.split()) if isinstance(x, str) else set(x or [])


def _same_hand(hole_a, board_a, hole_b, board_b):
    """The same deal: identical hole cards, and the shorter board is part of the longer one (a hand logged before the river)."""
    ha, hb, ba, bb = _cards(hole_a), _cards(hole_b), _cards(board_a), _cards(board_b)
    return len(ha) == 2 and ha == hb and (ba <= bb or bb <= ba)


def _refresh_casino_session(con, sid):
    """A Casino-made session has no balances of its own: its result is just its hands. Drop it if it has none left."""
    row = con.execute("SELECT source FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row or row["source"] not in ("casino", "practice", "tournament"):
        return
    n, total, last = con.execute("SELECT COUNT(*), COALESCE(SUM(result), 0), MAX(ts) FROM hands WHERE session_id=?", (sid,)).fetchone()
    if n == 0:
        con.execute("DELETE FROM sessions WHERE id=?", (sid,))
    else:
        con.execute("UPDATE sessions SET balance_before=0, balance_after=?, end_ts=? WHERE id=?", (total, last, sid))


def _casino_session(con, bb, buy_in, practice, ts):
    kind = "tournament" if practice == 2 else "practice" if practice else "casino"
    row = con.execute("SELECT id FROM sessions WHERE source=? AND bb=? AND end_ts IS NOT NULL AND end_ts >= ? ORDER BY id DESC LIMIT 1",
                      (kind, bb, ts - CASINO_SESSION_GAP_S)).fetchone()
    if row:
        return row["id"]
    return con.execute("INSERT INTO sessions(start_ts,end_ts,balance_before,buy_in,bb,balance_after,source) VALUES(?,?,?,?,?,?,?)",
                       (ts, ts, 0, buy_in, bb, 0, kind)).lastrowid


def sync_casino_hand(con, trainer_id, c):
    """A finished Casino hand goes into the same table the RTA uses, so Analytics counts it even if you never touched the RTA.
    It is always ONE row: if you already typed this hand into the RTA, that row is the one (it just learns the exact result and the
    Casino hand it is); if you type it into the RTA later, the RTA fills in THIS row. A unique index makes a second row impossible.
    `c`: hole, board (lists), pot, net, stack, bb, buy_in (money, not chips), position, opps, decisions, ts, practice."""
    if con.execute("SELECT id FROM hands WHERE trainer_hand_id=?", (trainer_id,)).fetchone():
        return None                                                     # already saved
    for r in con.execute("SELECT h.*, s.bb AS sbb FROM hands h LEFT JOIN sessions s ON s.id=h.session_id "
                         "WHERE h.source='rta' AND h.trainer_hand_id IS NULL AND h.ts >= ? ORDER BY h.ts DESC", (c["ts"] - CASINO_WINDOW_S,)):
        if r["sbb"] is not None and abs(r["sbb"] - c["bb"]) > 1e-9:
            continue
        if _same_hand(r["hole"], r["board"], c["hole"], c["board"]):
            con.execute("UPDATE hands SET trainer_hand_id=?, source='both', board=?, result=?, practice=? WHERE id=?",
                        (trainer_id, " ".join(c["board"]), c["net"], _flag(c["practice"]), r["id"]))
            con.commit()
            return r["id"]
    sid = _casino_session(con, c["bb"], c["buy_in"], c["practice"], c["ts"])
    cur = con.execute(
        "INSERT INTO hands(session_id,ts,hole,board,pot,call,opps,range_pct,position,stack,equity,required,suggested,taken,result,decisions,practice,"
        "source,trainer_hand_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, c["ts"], " ".join(c["hole"]), " ".join(c["board"]), c["pot"], 0, c["opps"], None, c["position"], c["stack"], None, None, None,
         c["taken"], c["net"], json.dumps(c["decisions"]), _flag(c["practice"]), "casino", trainer_id))
    _refresh_casino_session(con, sid)
    con.commit()
    return cur.lastrowid


def log_hand(con, sid, q, position, stack, eq, required, suggested, taken, result, decisions=None, practice=False):
    for r in con.execute("SELECT h.*, s.bb AS sbb FROM hands h LEFT JOIN sessions s ON s.id=h.session_id "
                         "WHERE h.source='casino' AND h.ts >= ? ORDER BY h.ts DESC", (time.time() - CASINO_WINDOW_S,)):
        rta_bb = con.execute("SELECT bb FROM sessions WHERE id=?", (sid,)).fetchone() if sid else None
        if rta_bb and r["sbb"] is not None and abs(rta_bb[0] - r["sbb"]) > 1e-9:
            continue
        if _same_hand(r["hole"], r["board"], list(q.hole), list(q.board)):
            old = r["session_id"]
            con.execute("UPDATE hands SET source='both', pot=?, call=?, opps=?, range_pct=?, equity=?, required=?, suggested=?, taken=?, decisions=?, "
                        "session_id=COALESCE(?, session_id) WHERE id=?",
                        (q.pot, q.call, q.opponents, q.range_pct, eq, required, suggested, taken,
                         json.dumps(decisions) if decisions is not None else None, sid, r["id"]))
            if sid and old != sid:
                _refresh_casino_session(con, old)                       # the hand now lives in your RTA session
            con.commit()
            return r["id"]
    con.execute(
        "INSERT INTO hands(session_id,ts,hole,board,pot,call,opps,range_pct,position,stack,"
        "equity,required,suggested,taken,result,decisions,practice) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, time.time(), " ".join(q.hole), " ".join(q.board), q.pot, q.call, q.opponents,
         q.range_pct, position, stack, eq, required, suggested, taken, result, json.dumps(decisions) if decisions is not None else None,
         _flag(practice)))
    con.commit()


def clear_history(con):
    """Permanently delete every saved session and hand."""
    con.execute("DELETE FROM hands")
    con.execute("DELETE FROM sessions")
    con.commit()


# ---- trainer (the play-against-bots page): its own settings, stacks and hand history, separate from the tool's
def trainer_save(con, state):
    con.execute("INSERT INTO trainer_state(key,value) VALUES('table',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(state),))
    con.commit()


def trainer_load(con):
    row = con.execute("SELECT value FROM trainer_state WHERE key='table'").fetchone()
    return json.loads(row[0]) if row else None


def kv_load(con, key, default=None):
    row = con.execute("SELECT value FROM trainer_state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_save(con, key, value):
    con.execute("INSERT INTO trainer_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))
    con.commit()


def log_tournament(con, r):
    """One finished tournament (the dict from Table.tourney_result())."""
    con.execute("INSERT INTO tournaments(ts,stake,place,size,buy_in,prize,hands,knockouts,level,final_table,heads_up,min_bb,table_moves,sharks,source,bb) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), r["stake"], r["place"], r["size"], r["buy_in"], r["prize"], r["hands"], r["knockouts"], r["level"],
                 1 if r["final_table"] else 0, 1 if r["heads_up"] else 0, r["min_bb"], r["table_moves"], r["sharks"], "casino",
                 _stakes.STAKES[r["stake"]]["bb"] / 100 if r["stake"] in _stakes.STAKES else None))
    con.commit()


def log_trainer_hand(con, stake, hand_no, position, hole, board, pot, net, showdown, actions, strength=None, meta=None, practice=False):
    con.execute("INSERT INTO trainer_hands(ts,stake,hand_no,position,hole,board,pot,net,showdown,actions,strength,meta,practice) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), stake, hand_no, position, " ".join(hole), " ".join(board), pot, net,
                 1 if showdown else 0, json.dumps(actions), strength,
                 json.dumps(meta) if meta is not None else None, 2 if practice == 2 else 1 if practice else 0))
    con.commit()
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]
