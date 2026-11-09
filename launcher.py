"""Starts the app for the packaged, double-click version (Windows zip) and for `python launcher.py`.

It keeps the player's save file in their own user folder (not next to the program, so updating the program never loses it), picks a free port,
opens the game in the browser, and stays open until the window is closed.
"""
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = int(os.environ.get("POKER_PORT") or 5173)          # (POKER_PORT only for tests and the curious)


def data_folder(windows=None):
    """Where the save file lives: %APPDATA%\\PokerStudyTool on Windows, ~/.local/share/PokerStudyTool elsewhere."""
    if (os.name == "nt") if windows is None else windows:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / "PokerStudyTool")


def is_running(port):
    """True if this app is already answering on that port (a second double-click should just open the browser again)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def free_port(preferred=PORT):
    for port in [preferred] + list(range(preferred + 1, preferred + 30)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def main():
    os.environ.setdefault("POKER_DATA_DIR", data_folder())            # before the app is imported: it reads this
    folder = os.environ["POKER_DATA_DIR"]
    Path(folder).mkdir(parents=True, exist_ok=True)
    if is_running(PORT):
        webbrowser.open(f"http://127.0.0.1:{PORT}/trainer")
        print("The game is already running: it has been opened in your browser again.")
        time.sleep(2)
        return
    import server                                                      # (after the data folder is set)
    port = free_port()
    url = f"http://127.0.0.1:{port}/trainer"
    print("=" * 64)
    print(" Poker Study Tool (play money only)")
    print(f" The game is opening in your web browser: {url}")
    print(f" Your results are saved on this computer, in: {folder}")
    print(" To quit, close this window.")
    print("=" * 64)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                              # a closed window would hide the reason: keep it visible
        print("\nSomething went wrong:", e)
        input("Press Enter to close.")
        sys.exit(1)
