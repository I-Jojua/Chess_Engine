"""Reusable, testable presentation models for the Pygame chess UI."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import chess


@dataclass(frozen=True)
class BoardTheme:
    name: str
    light: tuple[int, int, int]
    dark: tuple[int, int, int]
    last_move: tuple[int, int, int, int] = (246, 208, 76, 115)
    selected: tuple[int, int, int, int] = (70, 150, 230, 120)
    check: tuple[int, int, int, int] = (230, 55, 55, 150)


BOARD_THEMES = (
    BoardTheme("Green", (238, 238, 210), (118, 150, 86)),
    BoardTheme("Wood", (240, 217, 181), (181, 136, 99)),
    BoardTheme("Blue", (222, 227, 230), (86, 125, 148)),
    BoardTheme("Slate", (202, 204, 208), (91, 97, 107)),
)

PIECE_THEMES = ("Classic", "Ivory", "Sapphire")
PIECE_TINTS = {
    "Classic": {chess.WHITE: (255, 255, 255), chess.BLACK: (255, 255, 255)},
    "Ivory": {chess.WHITE: (255, 250, 228), chess.BLACK: (210, 184, 130)},
    "Sapphire": {chess.WHITE: (224, 241, 255), chess.BLACK: (135, 177, 213)},
}


@dataclass
class UISettings:
    board_theme: int = 0
    piece_theme: int = 0
    sound_enabled: bool = True
    fullscreen: bool = False
    flipped: bool = False

    def cycle_board_theme(self) -> None:
        self.board_theme = (self.board_theme + 1) % len(BOARD_THEMES)

    def cycle_piece_theme(self) -> None:
        self.piece_theme = (self.piece_theme + 1) % len(PIECE_THEMES)

    @property
    def theme(self) -> BoardTheme:
        return BOARD_THEMES[self.board_theme % len(BOARD_THEMES)]

    @property
    def piece_theme_name(self) -> str:
        return PIECE_THEMES[self.piece_theme % len(PIECE_THEMES)]


@dataclass
class MoveAnimation:
    """Nonblocking interpolation for the last move, expressed in board pixels."""

    move: chess.Move
    started_at: float
    duration: float = 0.16

    @classmethod
    def start(cls, move: chess.Move, duration: float = 0.16) -> "MoveAnimation":
        return cls(move, monotonic(), duration)

    def progress(self, now: float | None = None) -> float:
        current = monotonic() if now is None else now
        if self.duration <= 0:
            return 1.0
        return min(1.0, max(0.0, (current - self.started_at) / self.duration))


def square_cell(square: chess.Square, flipped: bool = False) -> tuple[int, int]:
    file_index, rank_index = chess.square_file(square), chess.square_rank(square)
    return (7 - file_index, rank_index) if flipped else (file_index, 7 - rank_index)


def evaluation_white_centipawns(score: int, engine_color: chess.Color) -> int:
    """Convert root-side search score into White's perspective."""
    return score if engine_color == chess.WHITE else -score


def evaluation_white_share(score_cp: int) -> float:
    """Map centipawns to a smoothly bounded White share for the eval bar."""
    # A simple monotonic scale is easier to read than a discontinuous clamp.
    return min(0.97, max(0.03, 0.5 + score_cp / 1400.0))


def move_history_rows(board: chess.Board) -> tuple[tuple[int, str, str], ...]:
    replay = board.root()
    rows: list[tuple[int, str, str]] = []
    for index in range(0, len(board.move_stack), 2):
        white = board.move_stack[index]
        white_san = replay.san(white)
        replay.push(white)
        black_san = ""
        if index + 1 < len(board.move_stack):
            black = board.move_stack[index + 1]
            black_san = replay.san(black)
            replay.push(black)
        rows.append((index // 2 + 1, white_san, black_san))
    return tuple(rows)


def layout_history_rows(
    rows: tuple[tuple[int, str, str], ...],
    top: int,
    available_height: int,
    *,
    max_rows: int = 3,
    line_height: int = 24,
) -> tuple[tuple[tuple[int, str, str], int], ...]:
    """Lay out the newest complete move rows that fit in the history area."""
    if max_rows <= 0 or line_height <= 0 or available_height <= 0 or not rows:
        return ()
    visible_count = min(len(rows), max_rows, available_height // line_height)
    if visible_count <= 0:
        return ()
    visible = rows[-visible_count:]
    return tuple((row, top + index * line_height) for index, row in enumerate(visible))


def layout_panel_text(
    lines: tuple[str, ...],
    top: int,
    bottom: int,
    *,
    line_height: int,
) -> tuple[tuple[str, int], ...]:
    """Place status lines top-down, prioritizing state before optional details."""
    if line_height <= 0 or bottom <= top:
        return ()
    count = min(len(lines), max(0, (bottom - top) // line_height))
    return tuple((line, top + index * line_height) for index, line in enumerate(lines[:count]))


def captured_pieces(board: chess.Board) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Return captured piece types for White and Black, in capture order."""
    replay = board.root()
    captured: dict[chess.Color, list[int]] = {chess.WHITE: [], chess.BLACK: []}
    for move in board.move_stack:
        mover = replay.turn
        if replay.is_capture(move):
            victim = replay.piece_at(move.to_square)
            if replay.is_en_passant(move):
                victim = chess.Piece(chess.PAWN, not mover)
            if victim is not None:
                captured[mover].append(victim.piece_type)
        replay.push(move)
    return tuple(captured[chess.WHITE]), tuple(captured[chess.BLACK])


def promotion_piece_order() -> tuple[int, ...]:
    return chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT
