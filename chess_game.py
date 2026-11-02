"""Application-facing chess game state backed by python-chess.

The Pygame layer and future AI providers should use this class instead of
implementing chess rules themselves. ``board`` is exposed for read-only UI
inspection; use the methods below to change the position.
"""

from __future__ import annotations

import chess
import chess.pgn


class IllegalMoveError(ValueError):
    """Raised when a requested move is not legal in the current position."""


class InvalidPositionError(ValueError):
    """Raised when a FEN string does not describe a valid chess position."""


class GameState:
    """Own a chess position, legal move history, and game result state."""

    def __init__(self, fen: str | None = None) -> None:
        self._board = self._board_from_fen(fen) if fen is not None else chess.Board()
        self._redo_stack: list[chess.Move] = []
        self._claimed_outcome: chess.Outcome | None = None

    @staticmethod
    def _validate_board(board: chess.Board) -> None:
        status = board.status()
        if status != chess.STATUS_VALID:
            raise InvalidPositionError(f"Invalid chess position (status={status!s})")

    @classmethod
    def _board_from_fen(cls, fen: str) -> chess.Board:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise InvalidPositionError(f"Invalid FEN: {exc}") from exc
        cls._validate_board(board)
        return board

    @property
    def board(self) -> chess.Board:
        """The current board for rendering and read-only position inspection."""
        return self._board

    @property
    def white_to_move(self) -> bool:
        return self._board.turn == chess.WHITE

    @property
    def fen(self) -> str:
        # Preserve the FEN en-passant target even when no capture is currently
        # possible. This makes FEN load/export round trips lossless.
        return self._board.fen(en_passant="fen")

    @property
    def move_history(self) -> tuple[chess.Move, ...]:
        return tuple(self._board.move_stack)

    @property
    def outcome(self) -> chess.Outcome | None:
        """Return the finished result, excluding unclaimed draw offers."""
        return self._claimed_outcome or self._board.outcome(claim_draw=False)

    @property
    def result(self) -> str:
        outcome = self.outcome
        return outcome.result() if outcome is not None else "*"

    @property
    def is_game_over(self) -> bool:
        return self.outcome is not None

    @property
    def is_check(self) -> bool:
        return self._board.is_check()

    @property
    def is_checkmate(self) -> bool:
        return self._board.is_checkmate()

    @property
    def is_stalemate(self) -> bool:
        return self._board.is_stalemate()

    @property
    def is_insufficient_material(self) -> bool:
        return self._board.is_insufficient_material()

    def get_valid_moves(self) -> list[chess.Move]:
        """Return legal moves for the side to move."""
        if self.is_game_over:
            return []
        return list(self._board.legal_moves)

    @property
    def can_claim_threefold_repetition(self) -> bool:
        return not self.is_game_over and self._board.can_claim_threefold_repetition()

    @property
    def can_claim_fifty_moves(self) -> bool:
        return not self.is_game_over and self._board.can_claim_fifty_moves()

    @property
    def can_claim_draw(self) -> bool:
        return self.can_claim_threefold_repetition or self.can_claim_fifty_moves

    def make_move(self, move: chess.Move | str) -> chess.Move:
        """Validate and apply a legal UCI move or ``chess.Move`` instance."""
        if self.is_game_over:
            raise IllegalMoveError("The game is already over")

        if isinstance(move, str):
            try:
                move = self._board.parse_uci(move)
            except ValueError as exc:
                raise IllegalMoveError(f"Invalid UCI move: {move}") from exc

        if move not in self._board.legal_moves:
            raise IllegalMoveError(f"Illegal move in current position: {move.uci()}")

        self._board.push(move)
        self._redo_stack.clear()
        self._claimed_outcome = None
        return move

    def undo_move(self) -> chess.Move | None:
        """Retract a claimed draw, or undo one move when no claim is active."""
        if self._claimed_outcome is not None:
            self._claimed_outcome = None
            return None
        if not self._board.move_stack:
            return None
        move = self._board.pop()
        self._redo_stack.append(move)
        return move

    def redo_move(self) -> chess.Move | None:
        """Replay the most recently undone move if it remains legal."""
        if not self._redo_stack or self.is_game_over:
            return None
        move = self._redo_stack[-1]
        if move not in self._board.legal_moves:
            raise IllegalMoveError("The undone move is no longer legal")
        self._redo_stack.pop()
        self._board.push(move)
        return move

    def claim_draw(self) -> chess.Outcome | None:
        """Claim a currently available threefold-repetition or 50-move draw."""
        if self.is_game_over or not self._board.can_claim_draw():
            return None

        if self._board.can_claim_threefold_repetition():
            termination = chess.Termination.THREEFOLD_REPETITION
        else:
            termination = chess.Termination.FIFTY_MOVES
        self._claimed_outcome = chess.Outcome(termination, winner=None)
        return self._claimed_outcome

    def load_fen(self, fen: str) -> None:
        """Load a valid FEN and clear move/redo history."""
        self._board = self._board_from_fen(fen)
        self._redo_stack.clear()
        self._claimed_outcome = None

    def export_pgn(self) -> str:
        """Export the current move stack as a PGN game."""
        game = chess.pgn.Game.from_board(self._board)
        if self._claimed_outcome is not None:
            game.headers["Result"] = self._claimed_outcome.result()
        exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
        return game.accept(exporter)

    def new_game(self, fen: str | None = None) -> None:
        """Start a new game, optionally from a supplied valid FEN."""
        self._board = self._board_from_fen(fen) if fen is not None else chess.Board()
        self._redo_stack.clear()
        self._claimed_outcome = None
