"""Where the data lives: POKER_DATA_DIR keeps the database and backups out of the code, one subfolder per app."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pokercalc import analytics, backup, store
from pokercalc.parser import Query

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def add_hand(con, buy=5, bb=0.05, result=2.5):
    sid = store.start_session(con, buy, buy, bb)
    store.log_hand(con, sid, Query(["Ah", "Kd"], ["2c", "3d", "4h"], 1, 0, 1, None), "BTN", buy, 0.5, 0.0, "RAISE", "RAISE", result,
                   [{"street": 3, "suggested": "RAISE", "taken": "RAISE"}])
    store.end_session(con, sid, buy + result)


def test_unset_means_the_old_locations(monkeypatch):
    monkeypatch.delenv("POKER_DATA_DIR", raising=False)
    assert store.default_db() == Path(store.__file__).resolve().parent.parent / "poker.db"
    assert backup.data_dir() == backup.ROOT / "backup"


def test_set_puts_the_database_and_backups_in_a_subfolder_for_this_app(monkeypatch, tmp_path):
    monkeypatch.setenv("POKER_DATA_DIR", str(tmp_path / "data"))
    assert store.default_db() == tmp_path / "data" / store.APP / "poker.db"
    assert backup.data_dir() == tmp_path / "data" / store.APP / "backup"
    con = store.connect()                                       # creates the folder on first use
    add_hand(con)
    assert (tmp_path / "data" / store.APP / "poker.db").exists()
    assert store.APP in ("holdem", "plo")


def test_the_two_apps_use_different_subfolders():
    import re
    here = Path(store.__file__).read_text()
    assert re.search(r'APP = "(holdem|plo)"', here)             # each copy of the app names itself; they never share a folder


def test_backup_goes_to_the_data_folders_own_repo_not_the_code_repo(monkeypatch, tmp_path):
    data = tmp_path / "data"
    subprocess.run(["git", "init", "-q", str(data)], check=True)
    monkeypatch.setenv("POKER_DATA_DIR", str(data))
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    con = store.connect()
    add_hand(con)
    msg = backup.run(con)
    assert "committed locally" in msg and "no remote" in msg    # committed there; nothing to push to
    log = subprocess.run(["git", "-C", str(data), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "Update poker data backup" in log
    assert (data / store.APP / "backup" / "hands.jsonl").read_text().count("\n") == 1
    assert backup.run(con) == "Backup up to date."


def test_a_data_folder_that_is_not_a_repo_is_just_written(monkeypatch, tmp_path):
    monkeypatch.setenv("POKER_DATA_DIR", str(tmp_path / "plain"))
    con = store.connect()
    add_hand(con)
    assert "not in a git repository" in backup.run(con)
    assert (tmp_path / "plain" / store.APP / "backup" / "sessions.jsonl").exists()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_migrate_copies_everything_and_never_touches_the_original(monkeypatch, tmp_path):
    # a fake project folder with a database and backups, so the real ones are never involved
    project = tmp_path / "project"
    (project / "backup").mkdir(parents=True)
    monkeypatch.setattr(backup, "ROOT", project)
    src = store.connect(project / "poker.db")
    add_hand(src)
    add_hand(src, result=-1.0)
    backup.export_data(src, project / "backup")
    src.close()
    before = {p.name: sha(p) for p in [project / "poker.db", *sorted((project / "backup").glob("*.jsonl"))]}
    backup.migrate(tmp_path / "newhome")
    dest = tmp_path / "newhome" / store.APP
    moved = store.connect(dest / "poker.db")
    assert moved.execute("select count(*) from hands").fetchone()[0] == 2
    assert analytics.summary(moved)["total_pl"] == pytest.approx(1.5)             # the analytics see the same hands
    assert (dest / "backup" / "hands.jsonl").read_text() == (project / "backup" / "hands.jsonl").read_text()
    after = {p.name: sha(p) for p in [project / "poker.db", *sorted((project / "backup").glob("*.jsonl"))]}
    assert before == after                                                          # the originals are untouched
    with pytest.raises(SystemExit):                                                 # and it will not overwrite a database
        backup.migrate(tmp_path / "newhome")


def test_analytics_are_the_same_wherever_the_database_lives(monkeypatch, tmp_path):
    a = store.connect(tmp_path / "a.db")
    add_hand(a), add_hand(a, buy=100, bb=1.0, result=-10)
    monkeypatch.setenv("POKER_DATA_DIR", str(tmp_path / "data"))
    b = store.connect()
    add_hand(b), add_hand(b, buy=100, bb=1.0, result=-10)
    assert analytics.summary(a)["winnings_bb"] == analytics.summary(b)["winnings_bb"] == [0.0, 50.0, 40.0]
