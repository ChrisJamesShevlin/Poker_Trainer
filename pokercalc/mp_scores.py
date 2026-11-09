"""The running score between the people who play at the host's multiplayer table: one lifetime total per nickname, kept in the host's own data
folder (never in the code, never in the Analytics database). Each finished hand adds the hand's win or loss for each human, so a sitting that ends
abruptly (the window closed, the friend dropped) still counts, and a free top-up when someone is out of chips does not hide a loss.
Totals are in chips (pence), like everything at the table."""
import json
import os
import threading
import time
from pathlib import Path

from . import store

FILE = "multiplayer_scores.json"


def default_path():
    d = store.data_dir()
    return (d if d else Path(__file__).resolve().parent.parent) / FILE


class Scores:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()
        try:
            self.data = {k: v for k, v in json.loads(self.path.read_text()).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.data = {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
            os.replace(tmp, self.path)                        # a crash half-way never leaves a broken file
        except OSError:
            pass                                              # the score is a nicety: it must never break a hand

    def _row(self, name):
        return self.data.setdefault(name, {"net": 0, "hands": 0})

    def add(self, name, chips, won=False, pot=0, showdown=False):
        """One finished hand for one player: what they won or lost, whether they won the pot (a split counts), the pot, and whether it went to a showdown."""
        with self._lock:
            row, chips = self._row(name), int(chips)
            row["net"] += chips
            row["hands"] += 1
            if won:
                row["won"] = row.get("won", 0) + 1
                row["biggest_pot"] = max(row.get("biggest_pot", 0), int(pot))
                if showdown:
                    row["showdowns_won"] = row.get("showdowns_won", 0) + 1
            row["best_win"] = max(row.get("best_win", 0), chips)
            row["worst_loss"] = min(row.get("worst_loss", 0), chips)
            row["last"] = int(time.time())
            self._save()

    def topup(self, name):
        """Out of chips and topped up for free: counted, and it costs nothing to the score because every hand's result is already profit or loss."""
        with self._lock:
            row = self._row(name)
            row["topups"] = row.get("topups", 0) + 1
            self._save()

    def board(self):
        """Best first: one row per player with the totals the page shows."""
        with self._lock:
            rows = []
            for n, r in self.data.items():
                hands, won = r.get("hands", 0), r.get("won", 0)
                rows.append({"name": n, "net": r.get("net", 0), "hands": hands, "won": won, "win_pct": round(100 * won / hands) if hands else 0,
                             "showdowns_won": r.get("showdowns_won", 0), "biggest_pot": r.get("biggest_pot", 0), "best_win": r.get("best_win", 0),
                             "worst_loss": r.get("worst_loss", 0), "topups": r.get("topups", 0)})
        return sorted(rows, key=lambda r: (-r["net"], r["name"]))

    def reset(self):
        with self._lock:
            self.data = {}
            self._save()
