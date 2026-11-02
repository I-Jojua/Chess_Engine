"""Local PGN file persistence with standard headers and mainline validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import chess
import chess.pgn


@dataclass(frozen=True)
class LoadedPGN:
    board: chess.Board
    headers: Mapping[str, str]


def game_to_pgn(board: chess.Board, headers: Mapping[str, str] | None = None) -> str:
    if not isinstance(board, chess.Board):
        raise TypeError("board must be a chess.Board")
    game = chess.pgn.Game.from_board(board.copy(stack=True))
    if headers:
        for key, value in headers.items():
            if key not in ("FEN", "SetUp", "Result"):
                game.headers[str(key)] = str(value)
    outcome = board.outcome(claim_draw=True)
    explicit_result = str(headers.get("Result", "*")) if headers else "*"
    game.headers["Result"] = explicit_result if explicit_result in ("1-0", "0-1", "1/2-1/2", "*") and explicit_result != "*" else (outcome.result() if outcome is not None else "*")
    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


def save_pgn(path: str | Path, board: chess.Board, headers: Mapping[str, str] | None = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(game_to_pgn(board, headers), encoding="utf-8")
    return target


def load_pgn(path: str | Path) -> LoadedPGN:
    target = Path(path)
    with target.open("r", encoding="utf-8-sig") as stream:
        game = chess.pgn.read_game(stream)
    if game is None:
        raise ValueError("The selected file does not contain a PGN game")
    if game.errors:
        messages = "; ".join(str(error) for error in game.errors[:3])
        raise ValueError(f"PGN contains invalid moves: {messages}")
    board = game.board()
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError(f"PGN contains illegal move: {move.uci()}")
        board.push(move)
    return LoadedPGN(board, dict(game.headers))
