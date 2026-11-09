"""The packaged (double-click) app: where it saves, how it picks a port, and that a machine without git still gets a calm message."""
import socket
import sqlite3

import launcher
from pokercalc import backup, store


def test_the_save_lives_in_the_players_own_folder_not_next_to_the_program(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert launcher.data_folder(windows=True) == str(tmp_path / "Roaming" / "PokerStudyTool")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert launcher.data_folder(windows=False) == str(tmp_path / "share" / "PokerStudyTool")


def test_the_app_uses_the_data_folder_for_its_database(monkeypatch, tmp_path):
    monkeypatch.setenv("POKER_DATA_DIR", str(tmp_path / "PokerStudyTool"))
    con = store.connect()
    con.close()
    assert (tmp_path / "PokerStudyTool" / "holdem" / "poker.db").exists()


def test_a_busy_port_is_skipped(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        assert launcher.free_port(busy) != busy


def test_a_machine_without_git_gets_a_calm_message_and_the_data_is_still_exported(monkeypatch, tmp_path):
    monkeypatch.setenv("POKER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(backup.shutil, "which", lambda name: None)
    con = store.connect()
    msg = backup.run(con)
    assert msg == "Your results are saved on this computer."
    assert (tmp_path / "data" / "holdem" / "backup" / "sessions.jsonl").exists()
