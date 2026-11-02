"""Optional, local JSON UCI opening book.

Entries map the first four FEN fields (placement, turn, castling, en-passant)
to ordered UCI move lists. To extend the book, append legal UCI choices under
the desired position key in ``data/opening_book.json``. Unknown or illegal
entries are ignored and the custom engine searches normally.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

import chess


class LocalOpeningBook:
    def __init__(self, entries: Mapping[str, Sequence[str]] | None = None) -> None:
        self.entries = {str(key): tuple(str(move) for move in moves)
                        for key, moves in (entries or {}).items()}

    @classmethod
    def from_json(cls, path: str | Path) -> "LocalOpeningBook":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Opening book JSON must be a position-to-moves object")
        return cls(data)

    @staticmethod
    def position_key(board: chess.Board) -> str:
        return " ".join(board.fen(en_passant="fen").split()[:4])

    def moves_for(self, board: chess.Board) -> tuple[chess.Move, ...]:
        legal = set(board.legal_moves)
        moves = []
        for uci in self.entries.get(self.position_key(board), ()):
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                continue
            if move in legal and move not in moves:
                moves.append(move)
        return tuple(moves)

    def choose(self, board: chess.Board) -> chess.Move | None:
        moves = self.moves_for(board)
        return moves[0] if moves else None
