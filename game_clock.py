"""Monotonic, increment-aware clocks for local chess games."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable

import chess

from game_config import TimeControl


@dataclass(frozen=True)
class TimeoutResult:
    winner: chess.Color | None
    reason: str = "Timeout"

    @property
    def result(self) -> str:
        if self.winner is None:
            return "1/2-1/2"
        return "1-0" if self.winner == chess.WHITE else "0-1"


class GameClock:
    """Track both players' remaining time without depending on frame rate."""

    def __init__(
        self,
        time_control: TimeControl,
        initial_turn: chess.Color = chess.WHITE,
        now: Callable[[], float] = monotonic,
        start_immediately: bool = True,
    ) -> None:
        self.time_control = time_control
        self._now = now
        self._remaining = {
            chess.WHITE: float(time_control.initial_seconds),
            chess.BLACK: float(time_control.initial_seconds),
        }
        self.turn = initial_turn
        self._active_since: float | None = now() if start_immediately else None
        self._expired_color: chess.Color | None = None

    def start(self, now: float | None = None) -> None:
        if self._expired_color is None and self._active_since is None:
            self._active_since = self._now() if now is None else now

    def reset(self, initial_turn: chess.Color = chess.WHITE, now: float | None = None) -> None:
        self._remaining[chess.WHITE] = float(self.time_control.initial_seconds)
        self._remaining[chess.BLACK] = float(self.time_control.initial_seconds)
        self.turn = initial_turn
        self._expired_color = None
        self._active_since = self._now() if now is None else now

    def remaining(self, color: chess.Color, now: float | None = None) -> float:
        value = self._remaining[color]
        if color == self.turn and self._active_since is not None and self._expired_color is None:
            current = self._now() if now is None else now
            value -= max(0.0, current - self._active_since)
        return max(0.0, value)

    def flag_fall(self, now: float | None = None) -> chess.Color | None:
        if self._expired_color is not None:
            return self._expired_color
        if self._active_since is None:
            return None
        current = self._now() if now is None else now
        if self.remaining(self.turn, current) <= 0:
            self._remaining[self.turn] = 0.0
            self._expired_color = self.turn
            self._active_since = None
        return self._expired_color

    def complete_move(self, mover: chess.Color, now: float | None = None) -> chess.Color | None:
        """Record a completed move; return the flagged color if time ran out."""
        current = self._now() if now is None else now
        flagged = self.flag_fall(current)
        if flagged is not None:
            return flagged
        if mover != self.turn:
            raise ValueError("The move's color does not match the active clock")

        self._remaining[mover] = self.remaining(mover, current) + self.time_control.increment_seconds
        self.turn = not mover
        self._active_since = current if self._active_since is not None else None
        return None

    def stop(self, now: float | None = None) -> chess.Color | None:
        flagged = self.flag_fall(now)
        if self._active_since is not None:
            current = self._now() if now is None else now
            self._remaining[self.turn] = self.remaining(self.turn, current)
            self._active_since = None
        return flagged

    def snapshot(self, now: float | None = None) -> tuple[float, float, chess.Color, bool, chess.Color | None]:
        current = self._now() if now is None else now
        return (
            self.remaining(chess.WHITE, current),
            self.remaining(chess.BLACK, current),
            self.turn,
            self._active_since is not None,
            self._expired_color,
        )

    def restore(
        self,
        snapshot: tuple[float, float, chess.Color, bool, chess.Color | None],
        now: float | None = None,
    ) -> None:
        white, black, turn, was_running, expired = snapshot
        current = self._now() if now is None else now
        self._remaining[chess.WHITE] = white
        self._remaining[chess.BLACK] = black
        self.turn = turn
        self._expired_color = expired
        self._active_since = current if was_running and expired is None else None


def timeout_result(board: chess.Board, flagged_color: chess.Color) -> TimeoutResult:
    """Resolve timeout to a win, or draw if the opponent cannot mate."""
    winner = not flagged_color
    if board.has_insufficient_material(winner):
        winner = None
    return TimeoutResult(winner)


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds + 0.999))
    minutes, remainder = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes:02d}:{remainder:02d}"
