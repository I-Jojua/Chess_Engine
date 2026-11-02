"""Testable controller for side selection, timed moves, undo, and redo."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable

import chess

from game_clock import GameClock, TimeoutResult, timeout_result
from game_config import Difficulty, SideChoice, TimeControl, resolve_colors
from chess_game import GameState, IllegalMoveError


@dataclass(frozen=True)
class _MoveRecord:
    move: chess.Move
    clock_before: tuple[float, float, chess.Color, bool, chess.Color | None]
    clock_after: tuple[float, float, chess.Color, bool, chess.Color | None]


class GameSession:
    """Own the P0 game state and match-specific P2 controls."""

    def __init__(
        self,
        difficulty: Difficulty,
        side_choice: SideChoice,
        time_control: TimeControl,
        *,
        rng=None,
        now: Callable[[], float] = monotonic,
    ) -> None:
        self.difficulty = difficulty
        self.side_choice = side_choice
        self.time_control = time_control
        self.player_color, self.ai_color = resolve_colors(side_choice, rng)
        self._now = now
        self.game = GameState()
        self.clock = GameClock(time_control, now=now)
        self._timeout: TimeoutResult | None = None
        self._records: list[_MoveRecord] = []
        self._redo_records: list[_MoveRecord] = []

    @property
    def outcome(self) -> chess.Outcome | TimeoutResult | None:
        return self._timeout or self.game.outcome

    @property
    def is_game_over(self) -> bool:
        return self.outcome is not None

    @property
    def is_human_turn(self) -> bool:
        return not self.is_game_over and self.game.board.turn == self.player_color

    @property
    def is_ai_turn(self) -> bool:
        return not self.is_game_over and self.game.board.turn == self.ai_color

    def update_clock(self, now: float | None = None) -> TimeoutResult | None:
        flagged = self.clock.flag_fall(now)
        if flagged is not None and self._timeout is None:
            self._timeout = timeout_result(self.game.board, flagged)
        if self.game.outcome is not None:
            self.clock.stop(now)
        return self._timeout

    def make_move(self, move: chess.Move, now: float | None = None) -> bool:
        """Apply a legal move and update the clock/history atomically."""
        if self.is_game_over:
            return False
        current = self._now() if now is None else now
        self.update_clock(current)
        if self.is_game_over:
            return False
        if move not in self.game.board.legal_moves:
            raise IllegalMoveError(f"Illegal move in current position: {move.uci()}")

        mover = self.game.board.turn
        before = self.clock.snapshot(current)
        flagged = self.clock.complete_move(mover, current)
        if flagged is not None:
            self._timeout = timeout_result(self.game.board, flagged)
            return False

        self.game.make_move(move)
        after = self.clock.snapshot(current)
        self._records.append(_MoveRecord(move, before, after))
        self._redo_records.clear()
        if self.game.outcome is not None:
            self.clock.stop(current)
        return True

    def make_human_move(self, move: chess.Move, now: float | None = None) -> bool:
        if not self.is_human_turn:
            return False
        return self.make_move(move, now)

    def make_ai_move(self, move: chess.Move, now: float | None = None) -> bool:
        if not self.is_ai_turn:
            return False
        return self.make_move(move, now)

    def claim_draw(self, now: float | None = None) -> chess.Outcome | None:
        if not self.is_human_turn:
            return None
        self.update_clock(now)
        if self.is_game_over:
            return None
        outcome = self.game.claim_draw()
        if outcome is not None:
            self.clock.stop(now)
        return outcome

    def _undo_one(self, now: float | None = None) -> bool:
        if not self.game.board.move_stack or not self._records:
            return False
        record = self._records.pop()
        self.game.undo_move()
        self._redo_records.append(record)
        self.clock.restore(record.clock_before, now)
        self._timeout = None
        return True

    def undo_human_turn(self, now: float | None = None) -> int:
        """Undo the human move and its AI reply, if the reply has happened."""
        if not self.game.board.move_stack:
            return 0
        removed = 0
        last_mover = not self.game.board.turn
        self._undo_one(now)
        removed += 1

        # If the AI just replied, remove the preceding human move too. If an
        # AI opening move was the only move, leave the human's turn to play.
        if last_mover == self.ai_color and self.game.board.move_stack:
            previous_mover = not self.game.board.turn
            if previous_mover == self.player_color:
                self._undo_one(now)
                removed += 1
        return removed

    def redo_move(self, now: float | None = None) -> bool:
        if not self._redo_records or self.is_game_over:
            return False
        record = self._redo_records[-1]
        if record.move not in self.game.board.legal_moves:
            return False
        if self.game.redo_move() is None:
            return False
        self._redo_records.pop()
        self._records.append(record)
        self.clock.restore(record.clock_after, now)
        self._timeout = None
        if self.game.outcome is not None:
            self.clock.stop(now)
        return True

    def restart(self, now: float | None = None) -> None:
        self.game.new_game()
        self._records.clear()
        self._redo_records.clear()
        self._timeout = None
        self.clock.reset(now=now)
