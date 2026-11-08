"""Back up sessions, hands and trainer data as plain JSON lines, and restore them on a new machine.

Where the data lives: set POKER_DATA_DIR to a folder OUTSIDE the code (for example ~/poker-data). Each app then keeps its
database and backups in its own subfolder there (<folder>/holdem/ or <folder>/plo/). If that folder is its own git
repository, the backup commits and pushes there, so your hand history never goes into the code repo. Unset, the
database stays in the project folder and backups go to backup/ in this repo, as before.

The repo you push to MUST be private: hand history is pushed to it. run() refuses to push while GitHub says it is public.
Restore on a new laptop (after cloning):  python3 -m pokercalc.backup restore
Copy existing data to a new location (copies, never moves):  python3 -m pokercalc.backup migrate ~/poker-data
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import store

ROOT = Path(__file__).resolve().parent.parent
TABLES = ("sessions", "hands", "trainer_hands", "trainer_state", "tournaments")


def data_dir():
    d = store.data_dir()
    return d / "backup" if d else ROOT / "backup"


def export_data(con, folder):
    """Write one file per table, one row per line, ordered by id so git diffs only show new rows."""
    folder.mkdir(parents=True, exist_ok=True)
    for t in TABLES:
        rows = [dict(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")]
        (folder / f"{t}.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _toplevel(folder):
    """The git repository that holds this folder, or None if it is not in one."""
    r = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _is_public(repo):
    """True if the origin repo is publicly readable (GitHub answers 404 for a private repo without a login)."""
    m = re.search(r"github\.com[:/]([^/]+/[^/.\s]+)", _git(repo, "remote", "get-url", "origin").stdout)
    if not m:
        return True   # can't tell, so don't push data
    try:
        urllib.request.urlopen(f"https://api.github.com/repos/{m.group(1)}", timeout=10)
        return True
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return True


def run(con):
    """Export, commit (only the backup folder) to the repo that holds it, and try to push. Returns a short message; never raises."""
    folder = data_dir()
    try:
        export_data(con, folder)
        repo = _toplevel(folder)
        if repo is None:
            return f"Backup written to {folder} (not in a git repository, so not committed)."
        rel = str(folder.resolve().relative_to(repo.resolve()))
        _git(repo, "add", "--", rel)
        if not _git(repo, "status", "--porcelain", "--", rel).stdout.strip():
            return "Backup up to date."
        c = _git(repo, "commit", "-m", "Update poker data backup", "--", rel)
        if c.returncode:
            return "Backup saved but the commit failed: " + (c.stderr or c.stdout).strip()[:120]
        if _git(repo, "remote", "get-url", "origin").returncode:
            return "Backup committed locally (this repository has no remote, so nothing was pushed)."
        if _is_public(repo):
            return "Backup committed locally but NOT pushed: the repo is public (or unreachable). Make it private first."
        if _git(repo, "push").returncode:
            return "Backup committed; press Sync in VS Code to push it."
        return "Backup pushed."
    except Exception as e:  # a failed backup must never break ending a session
        return f"Backup failed: {e}"


def migrate(base):
    """COPY (never move or delete) this app's database and backup files into <base>/<app>/. Prints what to do next."""
    dest = Path(base).expanduser() / store.APP
    src_db, src_backup = ROOT / "poker.db", ROOT / "backup"
    if (dest / "poker.db").exists():
        raise SystemExit(f"{dest / 'poker.db'} already exists; not overwriting it.")
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    if src_db.exists():
        s, d = sqlite3.connect(str(src_db)), sqlite3.connect(str(dest / "poker.db"))
        s.backup(d)                                   # a consistent copy even if the app is running
        s.close(), d.close()
        copied.append("poker.db")
    if src_backup.exists():
        shutil.copytree(src_backup, dest / "backup", dirs_exist_ok=True)
        copied.append("backup/")
    print(f"Copied {', '.join(copied) or 'nothing (no data found)'} to {dest}. Nothing was moved or deleted.")
    print(f"Next: make {Path(base).expanduser()} a PRIVATE git repository, then set  POKER_DATA_DIR={Path(base).expanduser()}  "
          "(for example in ~/.bashrc) and restart the app. Keep the old files until you have checked the app shows your data.")


def restore(con, folder):
    """Load the exported rows into an EMPTY database. Refuses to touch one that already has data."""
    if con.execute("SELECT 1 FROM sessions LIMIT 1").fetchone() or con.execute("SELECT 1 FROM hands LIMIT 1").fetchone():
        raise SystemExit("poker.db already has data; refusing to overwrite it.")
    n = {}
    for t in TABLES:
        path = folder / f"{t}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        have = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        for r in rows:
            r = {k: v for k, v in r.items() if k in have}
            con.execute(f"INSERT INTO {t}({','.join(r)}) VALUES({','.join('?' * len(r))})", list(r.values()))
        n[t] = len(rows)
    con.commit()
    return n


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "restore":
        print("Restored", restore(store.connect(), data_dir()))
    elif cmd == "backup":
        print(run(store.connect()))
    elif cmd == "migrate" and len(sys.argv) > 2:
        migrate(sys.argv[2])
    else:
        sys.exit("usage: python3 -m pokercalc.backup [backup|restore|migrate <folder>]")
