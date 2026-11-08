"""Parse the shorthand line: '6d 5h | 5s 7h Qh | pot 2600 call 400 | 2 opp [top25]'."""
import re
from dataclasses import dataclass
from typing import List, Optional

RANKS = "23456789TJQKA"
SUITS = "cdhs"
_CARD = re.compile(r"([2-9TJQKA])([cdhs])", re.I)


class ParseError(ValueError):
    pass


@dataclass
class Query:
    hole: List[str]
    board: List[str]
    pot: float
    call: float
    opponents: int
    range_pct: Optional[int] = None  # None = random hands


def parse_cards(text: str) -> List[str]:
    text = text.replace(",", " ").strip()
    if not text:
        return []
    cards = []
    for tok in text.split():
        found = _CARD.findall(tok)
        if "".join(r + s for r, s in found).lower() != tok.lower():
            raise ParseError(f"Bad card text: {tok!r}")
        cards += [r.upper() + s.lower() for r, s in found]
    if len(set(cards)) != len(cards):
        raise ParseError("Duplicate card in hand/board")
    return cards


def parse_line(line: str) -> Query:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 4:
        raise ParseError("Expected: hole | board | pot N call N | N opp [topNN]")
    hole, board = parse_cards(parts[0]), parse_cards(parts[1])
    if len(hole) != 2:
        raise ParseError("Need exactly 2 hole cards")
    if len(board) not in (0, 3, 4, 5):
        raise ParseError("Board must be 0, 3, 4 or 5 cards")
    if len(set(hole + board)) != len(hole + board):
        raise ParseError("Duplicate card between hole and board")
    m = re.search(r"pot\s+([\d.]+)", parts[2], re.I)
    c = re.search(r"call\s+([\d.]+)", parts[2], re.I)
    if not m:
        raise ParseError("Missing 'pot N'")
    pot, call = float(m.group(1)), float(c.group(1)) if c else 0.0
    o = re.search(r"(\d+)\s*opp", parts[3], re.I)
    if not o or not 1 <= int(o.group(1)) <= 8:
        raise ParseError("Need '1-8 opp'")
    r = re.search(r"top\s*(\d+)", parts[3], re.I)
    pct = int(r.group(1)) if r else None
    if pct is not None and not 1 <= pct <= 100:
        raise ParseError("topNN must be 1-100")
    return Query(hole, board, pot, call, int(o.group(1)), pct)
