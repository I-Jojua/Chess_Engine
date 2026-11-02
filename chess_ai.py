"""Custom chess search and evaluation engine.

python-chess supplies legal moves and reversible board state. All evaluation,
search, pruning, move ordering, and transposition handling live in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable, TypeAlias

import chess
from opening_book import LocalOpeningBook


MATE_SCORE = 30_000
INFINITY = 32_000
MAX_QUIESCENCE_PLY = 12
MAX_TT_ENTRIES = 150_000
FUTILITY_MARGIN = 110

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
PHASE_WEIGHTS = {
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
}
MAX_GAME_PHASE = 24

# Rows are ordered from White's home rank to the promotion rank. Black's
# tables are vertically mirrored by _oriented_square().
PAWN_PST = (
    (0, 0, 0, 0, 0, 0, 0, 0),
    (5, 10, 10, -15, -15, 10, 10, 5),
    (5, -5, -10, 0, 0, -10, -5, 5),
    (0, 0, 0, 20, 20, 0, 0, 0),
    (5, 5, 10, 25, 25, 10, 5, 5),
    (10, 10, 20, 30, 30, 20, 10, 10),
    (50, 50, 50, 50, 50, 50, 50, 50),
    (0, 0, 0, 0, 0, 0, 0, 0),
)
KNIGHT_PST = (
    (-50, -35, -25, -20, -20, -25, -35, -50),
    (-35, -15, 0, 5, 5, 0, -15, -35),
    (-25, 5, 15, 20, 20, 15, 5, -25),
    (-20, 0, 20, 30, 30, 20, 0, -20),
    (-20, 5, 20, 30, 30, 20, 5, -20),
    (-25, 0, 15, 20, 20, 15, 0, -25),
    (-35, -15, 0, 0, 0, 0, -15, -35),
    (-50, -35, -25, -20, -20, -25, -35, -50),
)
BISHOP_PST = (
    (-20, -10, -10, -10, -10, -10, -10, -20),
    (-10, 5, 0, 0, 0, 0, 5, -10),
    (-10, 10, 10, 10, 10, 10, 10, -10),
    (-10, 0, 10, 15, 15, 10, 0, -10),
    (-10, 5, 5, 15, 15, 5, 5, -10),
    (-10, 0, 5, 10, 10, 5, 0, -10),
    (-10, 0, 0, 0, 0, 0, 0, -10),
    (-20, -10, -10, -10, -10, -10, -10, -20),
)
ROOK_PST = (
    (0, 0, 5, 10, 10, 5, 0, 0),
    (5, 10, 10, 10, 10, 10, 10, 5),
    (-5, 0, 0, 0, 0, 0, 0, -5),
    (-5, 0, 0, 0, 0, 0, 0, -5),
    (-5, 0, 0, 0, 0, 0, 0, -5),
    (-5, 0, 0, 0, 0, 0, 0, -5),
    (-5, 0, 0, 0, 0, 0, 0, -5),
    (0, 0, 5, 10, 10, 5, 0, 0),
)
QUEEN_PST = (
    (-20, -10, -10, -5, -5, -10, -10, -20),
    (-10, 0, 0, 0, 0, 0, 0, -10),
    (-10, 0, 5, 5, 5, 5, 0, -10),
    (-5, 0, 5, 5, 5, 5, 0, -5),
    (0, 0, 5, 5, 5, 5, 0, -5),
    (-10, 5, 5, 5, 5, 5, 0, -10),
    (-10, 0, 5, 0, 0, 0, 0, -10),
    (-20, -10, -10, -5, -5, -10, -10, -20),
)
KING_MIDDLEGAME_PST = (
    (20, 30, 10, 0, 0, 10, 30, 20),
    (20, 20, 0, 0, 0, 0, 20, 20),
    (-10, -20, -20, -20, -20, -20, -20, -10),
    (-20, -30, -30, -40, -40, -30, -30, -20),
    (-30, -40, -40, -50, -50, -40, -40, -30),
    (-30, -40, -40, -50, -50, -40, -40, -30),
    (-30, -40, -40, -50, -50, -40, -40, -30),
    (-30, -40, -40, -50, -50, -40, -40, -30),
)
KING_ENDGAME_PST = (
    (-50, -40, -30, -20, -20, -30, -40, -50),
    (-30, -20, -10, 0, 0, -10, -20, -30),
    (-30, -10, 20, 30, 30, 20, -10, -30),
    (-30, -10, 30, 40, 40, 30, -10, -30),
    (-30, -10, 30, 40, 40, 30, -10, -30),
    (-30, -10, 20, 30, 30, 20, -10, -30),
    (-30, -30, 0, 0, 0, 0, -30, -30),
    (-50, -30, -30, -30, -30, -30, -30, -50),
)
NON_KING_PST = {
    chess.PAWN: PAWN_PST,
    chess.KNIGHT: KNIGHT_PST,
    chess.BISHOP: BISHOP_PST,
    chess.ROOK: ROOK_PST,
    chess.QUEEN: QUEEN_PST,
}


@dataclass(frozen=True)
class SearchFeatures:
    pvs: bool = True
    aspiration_windows: bool = True
    lmr: bool = True
    null_move_pruning: bool = True
    futility_pruning: bool = True
    opening_book: bool = False
    adaptive_time_management: bool = True


@dataclass(frozen=True)
class SearchLimits:
    max_depth: int = 4
    time_limit: float | None = 1.0
    collect_root_scores: bool = False
    features: SearchFeatures = SearchFeatures()

    def __post_init__(self) -> None:
        if self.max_depth < 1 or self.max_depth > 64:
            raise ValueError("max_depth must be between 1 and 64")
        if self.time_limit is not None and self.time_limit < 0:
            raise ValueError("time_limit cannot be negative")


@dataclass(frozen=True)
class SearchResult:
    best_move: chess.Move | None
    score: int
    depth: int
    nodes: int
    elapsed_seconds: float
    principal_variation: tuple[chess.Move, ...] = ()
    cancelled: bool = False
    root_move_scores: tuple[tuple[chess.Move, int], ...] = ()
    diagnostics: "SearchDiagnostics" = field(default_factory=lambda: SearchDiagnostics())

    @property
    def evaluation(self) -> int:
        """Evaluation in centipawns from the root side-to-move perspective."""
        return self.score


@dataclass(frozen=True)
class SearchDiagnostics:
    pvs_researches: int = 0
    lmr_reductions: int = 0
    lmr_researches: int = 0
    null_move_cutoffs: int = 0
    futility_prunes: int = 0
    aspiration_fail_high: int = 0
    aspiration_fail_low: int = 0
    requested_time_budget: float | None = None
    elapsed_seconds: float = 0.0
    quiescence_nodes: int = 0
    nodes_per_second: float = 0.0
    stopped_by_deadline: bool = False
    stopped_by_cancellation: bool = False
    stopped_by_adaptive_budget: bool = False


class _Bound(Enum):
    EXACT = 0
    LOWER = 1
    UPPER = 2


@dataclass
class _TTEntry:
    depth: int
    score: int
    bound: _Bound
    best_move: chess.Move | None
    generation: int


@dataclass
class _SearchContext:
    deadline: float | None
    cancel_event: Event
    requested_time_budget: float | None = None
    adaptive_deadline: float | None = None
    nodes: int = 0
    quiescence_nodes: int = 0
    stopped: bool = False
    stopped_by_deadline: bool = False
    stopped_by_cancellation: bool = False
    stopped_by_adaptive_budget: bool = False
    killers: list[list[chess.Move | None]] = field(default_factory=list)
    history: dict[tuple[chess.Color, chess.Square, chess.Square], int] = field(default_factory=dict)
    repetition_history: list[tuple[str, ...]] = field(default_factory=list)
    collect_root_scores: bool = False
    features: SearchFeatures = SearchFeatures()
    root_alpha: int = -INFINITY
    root_beta: int = INFINITY
    pvs_researches: int = 0
    lmr_reductions: int = 0
    lmr_researches: int = 0
    null_move_cutoffs: int = 0
    futility_prunes: int = 0
    aspiration_fail_high: int = 0
    aspiration_fail_low: int = 0


_TranspositionKey: TypeAlias = tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]
_WorkerPositionKey: TypeAlias = tuple[tuple[str, ...], tuple[str, ...]]


def _position_key(board: chess.Board) -> tuple[str, ...]:
    """Collision-free FEN key, including rule-relevant counters and EP state."""
    return tuple(board.fen(en_passant="fen").split()[:5])


def _worker_position_key(board: chess.Board) -> _WorkerPositionKey:
    """Include history so equal FENs with different repetition context differ."""
    return _position_key(board), tuple(move.uci() for move in board.move_stack)


def _repetition_key(board: chess.Board) -> tuple[str, ...]:
    # FEN's default EP mode records only a legally usable en-passant square,
    # matching repetition identity. Move counters do not affect repetition.
    return tuple(board.fen().split()[:4])


def _initial_repetition_history(board: chess.Board) -> list[tuple[str, ...]]:
    replay = board.root()
    history = [_repetition_key(replay)]
    for move in board.move_stack:
        irreversible = replay.is_irreversible(move)
        replay.push(move)
        if irreversible:
            history = [_repetition_key(replay)]
        else:
            history.append(_repetition_key(replay))
    return history


def _oriented_square(square: chess.Square, color: chess.Color) -> chess.Square:
    return square if color == chess.WHITE else square ^ 56


def _pst_value(table: tuple[tuple[int, ...], ...], square: chess.Square, color: chess.Color) -> int:
    oriented = _oriented_square(square, color)
    return table[chess.square_rank(oriented)][chess.square_file(oriented)]


def _game_phase(board: chess.Board) -> int:
    phase = 0
    for piece_type, weight in PHASE_WEIGHTS.items():
        phase += weight * (len(board.pieces(piece_type, chess.WHITE)) + len(board.pieces(piece_type, chess.BLACK)))
    return min(MAX_GAME_PHASE, phase)


def evaluate_position(board: chess.Board) -> int:
    """Evaluate a position in centipawns from White's perspective."""
    phase = _game_phase(board)
    endgame_phase = MAX_GAME_PHASE - phase
    score = 0
    pawns: dict[chess.Color, list[chess.Square]] = {
        chess.WHITE: list(board.pieces(chess.PAWN, chess.WHITE)),
        chess.BLACK: list(board.pieces(chess.PAWN, chess.BLACK)),
    }

    for square, piece in board.piece_map().items():
        sign = 1 if piece.color == chess.WHITE else -1
        score += sign * PIECE_VALUES[piece.piece_type]
        if piece.piece_type == chess.KING:
            mg = _pst_value(KING_MIDDLEGAME_PST, square, piece.color)
            eg = _pst_value(KING_ENDGAME_PST, square, piece.color)
            score += sign * ((mg * phase + eg * endgame_phase) // MAX_GAME_PHASE)
        else:
            score += sign * _pst_value(NON_KING_PST[piece.piece_type], square, piece.color)

    # Mobility is measured symmetrically with pseudo-legal moves. The board's
    # side-to-move flag is restored even if move generation raises.
    saved_turn = board.turn
    try:
        board.turn = chess.WHITE
        white_mobility = board.pseudo_legal_moves.count()
        board.turn = chess.BLACK
        black_mobility = board.pseudo_legal_moves.count()
    finally:
        board.turn = saved_turn
    score += 2 * (white_mobility - black_mobility)

    score += _evaluate_pawn_structure(pawns, phase)
    score += _evaluate_king_safety(board, phase)
    return score


def _evaluate_pawn_structure(
    pawns: dict[chess.Color, list[chess.Square]], phase: int
) -> int:
    score = 0
    for color in (chess.WHITE, chess.BLACK):
        sign = 1 if color == chess.WHITE else -1
        own = pawns[color]
        enemy = pawns[not color]
        file_counts = [0] * 8
        for square in own:
            file_counts[chess.square_file(square)] += 1

        for file_index, count in enumerate(file_counts):
            if count > 1:
                score -= sign * 14 * (count - 1)

        for square in own:
            file_index = chess.square_file(square)
            rank = chess.square_rank(square)
            if (file_index == 0 or file_counts[file_index - 1] == 0) and (
                file_index == 7 or file_counts[file_index + 1] == 0
            ):
                score -= sign * 12

            passed = True
            for enemy_square in enemy:
                enemy_file = chess.square_file(enemy_square)
                enemy_rank = chess.square_rank(enemy_square)
                if abs(enemy_file - file_index) > 1:
                    continue
                if (color == chess.WHITE and enemy_rank > rank) or (
                    color == chess.BLACK and enemy_rank < rank
                ):
                    passed = False
                    break
            if passed:
                advance = rank if color == chess.WHITE else 7 - rank
                score += sign * (advance * advance * (2 if phase > 8 else 4))
    return score


def _evaluate_king_safety(board: chess.Board, phase: int) -> int:
    if phase == 0:
        return 0
    score = 0
    for color in (chess.WHITE, chess.BLACK):
        king = board.king(color)
        if king is None:
            continue
        sign = 1 if color == chess.WHITE else -1
        king_file = chess.square_file(king)
        king_rank = chess.square_rank(king)
        pawn_rank = king_rank + (1 if color == chess.WHITE else -1)
        shield = 0
        if 0 <= pawn_rank < 8:
            for file_index in range(max(0, king_file - 1), min(7, king_file + 1) + 1):
                square = chess.square(file_index, pawn_rank)
                if board.piece_at(square) == chess.Piece(chess.PAWN, color):
                    shield += 1
        score += sign * shield * 9 * phase // MAX_GAME_PHASE

        for file_index in range(max(0, king_file - 1), min(7, king_file + 1) + 1):
            if not board.pieces(chess.PAWN, color) & chess.BB_FILES[file_index]:
                score -= sign * (10 * phase // MAX_GAME_PHASE)

        enemy = not color
        pressure = 0
        for file_index in range(max(0, king_file - 1), min(7, king_file + 1) + 1):
            for rank_index in range(max(0, king_rank - 1), min(7, king_rank + 1) + 1):
                square = chess.square(file_index, rank_index)
                pressure += len(board.attackers(enemy, square))
        score -= sign * min(pressure, 10) * 3 * phase // MAX_GAME_PHASE
    return score


def _automatic_draw(board: chess.Board, repetition_history: list[tuple[str, ...]]) -> bool:
    if board.is_insufficient_material() or board.halfmove_clock >= 150:
        return True
    current = _repetition_key(board)
    return repetition_history.count(current) >= 5


def _terminal_score(
    board: chess.Board,
    ply: int,
    repetition_history: list[tuple[str, ...]],
) -> int | None:
    if _automatic_draw(board, repetition_history):
        return 0
    # These rule checks are costly because python-chess may replay history and
    # test candidate moves. Only call them when a claim is plausible.
    repeated_history = len(set(repetition_history)) < len(repetition_history)
    if board.halfmove_clock >= 99 and board.can_claim_fifty_moves():
        return 0
    if repeated_history and board.can_claim_threefold_repetition():
        return 0
    return None


def _non_pawn_material(board: chess.Board, color: chess.Color) -> tuple[int, int]:
    count = 0
    value = 0
    for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        number = len(board.pieces(piece_type, color))
        count += number
        value += number * PIECE_VALUES[piece_type]
    return count, value


def null_move_allowed(board: chess.Board, depth: int) -> bool:
    """Conservative zugzwang and draw-clock guard for null-move pruning."""
    if depth < 3 or board.is_check() or board.is_insufficient_material():
        return False
    # Avoid null searches close enough to a 50-move claim that the artificial
    # ply could change the draw status.
    if board.halfmove_clock >= max(0, 100 - depth):
        return False
    own_count, own_value = _non_pawn_material(board, board.turn)
    opponent_count, opponent_value = _non_pawn_material(board, not board.turn)
    # Requiring substantial material on both sides deliberately excludes
    # pawn-only, minor-piece-only, and rook-vs-king endgames where zugzwang is common.
    return own_count >= 1 and own_value >= PIECE_VALUES[chess.ROOK] and opponent_count >= 1 \
        and opponent_value >= PIECE_VALUES[chess.KNIGHT] and own_count + opponent_count >= 3


def futility_prune_allowed(
    *,
    depth: int,
    move_index: int,
    quiet: bool,
    in_check: bool,
    gives_check: bool,
    static_eval: int,
    alpha: int,
    child_terminal: bool,
    child_is_terminal: bool,
) -> bool:
    """Policy gate: shallow late quiet moves only, never checks or terminals."""
    return (
        depth == 1 and move_index > 0 and quiet and not in_check and not gives_check
        and not child_terminal and not child_is_terminal
        and static_eval + FUTILITY_MARGIN <= alpha
    )


def adaptive_budget_share(board: chess.Board, legal_moves: list[chess.Move]) -> float:
    """Conservative position-surface estimate for allocating the soft budget."""
    captures = sum(board.is_capture(move) for move in legal_moves)
    checks = sum(board.gives_check(move) for move in legal_moves)
    tactical = (
        board.is_check() or captures >= 3 or checks >= 2
        or any(move.promotion is not None for move in legal_moves)
    )
    return 0.90 if tactical else 0.55


class _SearchStopped(Exception):
    pass


class CustomChessEngine:
    """Negamax alpha-beta engine with iterative deepening and a bounded TT."""

    def __init__(
        self,
        transposition_capacity: int = MAX_TT_ENTRIES,
        opening_book: LocalOpeningBook | None = None,
    ) -> None:
        if transposition_capacity < 1:
            raise ValueError("transposition_capacity must be positive")
        self.transposition_capacity = transposition_capacity
        self._table: dict[_TranspositionKey, _TTEntry] = {}
        self._generation = 0
        self._history_heuristic: dict[tuple[chess.Color, chess.Square, chess.Square], int] = {}
        self.opening_book = opening_book

    @staticmethod
    def evaluate(board: chess.Board) -> int:
        """Evaluate from the current side-to-move perspective."""
        score = evaluate_position(board)
        return score if board.turn == chess.WHITE else -score

    @staticmethod
    def position_key(board: chess.Board) -> tuple[str, ...]:
        """Expose the exact FEN-derived position key used by the transposition table."""
        return _position_key(board)

    def clear_transposition_table(self) -> None:
        self._table.clear()

    def choose_move(
        self,
        position: chess.Board | object,
        limits: SearchLimits | None = None,
        cancel_event: Event | None = None,
    ) -> chess.Move | None:
        """Return the best legal move, or ``None`` for a terminal position."""
        return self.search(position, limits, cancel_event).best_move

    def search(
        self,
        position: chess.Board | object,
        limits: SearchLimits | None = None,
        cancel_event: Event | None = None,
        progress_callback: Callable[[SearchResult], None] | None = None,
    ) -> SearchResult:
        limits = limits or SearchLimits()
        source_board = position if isinstance(position, chess.Board) else getattr(position, "board", None)
        if not isinstance(source_board, chess.Board):
            raise TypeError("position must be a chess.Board or a GameState exposing .board")

        start = monotonic()
        deadline = None if limits.time_limit is None else start + limits.time_limit
        board = source_board.copy(stack=True)
        legal_moves = list(board.legal_moves)
        event = cancel_event or Event()
        self._generation += 1
        context = _SearchContext(
            deadline=deadline,
            cancel_event=event,
            requested_time_budget=limits.time_limit,
            killers=[[None, None] for _ in range(limits.max_depth + MAX_QUIESCENCE_PLY + 2)],
            history=self._history_heuristic,
            repetition_history=_initial_repetition_history(board),
            collect_root_scores=limits.collect_root_scores,
            features=limits.features,
        )

        if not legal_moves or _automatic_draw(board, context.repetition_history):
            elapsed = monotonic() - start
            return SearchResult(None, 0, 0, 0, elapsed, diagnostics=self._diagnostics(context, elapsed))

        if limits.features.opening_book and self.opening_book is not None and not event.is_set():
            book_move = self.opening_book.choose(board)
            if book_move in legal_moves:
                elapsed = monotonic() - start
                return SearchResult(book_move, 0, 0, 0, elapsed, (book_move,),
                                    diagnostics=self._diagnostics(context, elapsed))

        best_move = legal_moves[0]
        best_score = self.evaluate(board)
        best_pv: tuple[chess.Move, ...] = (best_move,)
        best_root_scores: tuple[tuple[chess.Move, int], ...] = ()
        completed_depth = 0
        previous_best: chess.Move | None = None
        cancelled = False

        if limits.time_limit is not None and limits.features.adaptive_time_management:
            # Tactical positions get a larger share of the configured hard cap.
            # This affects only when iterative deepening stops, never its ceiling.
            budget_share = adaptive_budget_share(board, legal_moves)
            context.adaptive_deadline = start + limits.time_limit * budget_share

        try:
            for depth in range(1, limits.max_depth + 1):
                self._check_stop(context, force_time_check=True)
                if context.adaptive_deadline is not None and monotonic() >= context.adaptive_deadline:
                    context.stopped_by_adaptive_budget = True
                    context.stopped_by_deadline = True
                    break
                use_aspiration = (
                    context.features.aspiration_windows and depth >= 2
                    and completed_depth > 0 and not context.collect_root_scores
                )
                window = 50
                alpha = max(-INFINITY, best_score - window) if use_aspiration else -INFINITY
                beta = min(INFINITY, best_score + window) if use_aspiration else INFINITY
                while True:
                    context.root_alpha, context.root_beta = alpha, beta
                    score, move, root_scores = self._search_root(
                        board, legal_moves, depth, context, previous_best
                    )
                    # Failed aspiration bounds are discarded. Only a completed
                    # in-window search may replace the previous iteration result.
                    if score <= alpha and alpha > -INFINITY:
                        context.aspiration_fail_low += 1
                        alpha = max(-INFINITY, score - window)
                        window *= 2
                        continue
                    if score >= beta and beta < INFINITY:
                        context.aspiration_fail_high += 1
                        beta = min(INFINITY, score + window)
                        window *= 2
                        continue
                    break
                if move is not None:
                    best_move = move
                    best_score = score
                    previous_best = move
                    completed_depth = depth
                    best_pv = self._extract_pv(board, move, depth, context)
                    best_root_scores = tuple(root_scores)
                    if progress_callback is not None:
                        progress_elapsed = monotonic() - start
                        progress_callback(
                            SearchResult(
                                best_move=best_move,
                                score=best_score,
                                depth=completed_depth,
                                nodes=context.nodes,
                                elapsed_seconds=monotonic() - start,
                                principal_variation=best_pv,
                                root_move_scores=best_root_scores,
                                diagnostics=self._diagnostics(context, progress_elapsed),
                            )
                        )
                if context.adaptive_deadline is not None and monotonic() >= context.adaptive_deadline:
                    context.stopped_by_adaptive_budget = True
                    context.stopped_by_deadline = True
                    break
        except _SearchStopped:
            cancelled = True

        elapsed = monotonic() - start
        return SearchResult(
            best_move=best_move,
            score=best_score,
            depth=completed_depth,
            nodes=context.nodes,
            elapsed_seconds=elapsed,
            principal_variation=best_pv,
            cancelled=cancelled or event.is_set(),
            root_move_scores=best_root_scores,
            diagnostics=self._diagnostics(context, elapsed),
        )

    @staticmethod
    def _diagnostics(context: _SearchContext, elapsed: float = 0.0) -> SearchDiagnostics:
        return SearchDiagnostics(
            pvs_researches=context.pvs_researches,
            lmr_reductions=context.lmr_reductions,
            lmr_researches=context.lmr_researches,
            null_move_cutoffs=context.null_move_cutoffs,
            futility_prunes=context.futility_prunes,
            aspiration_fail_high=context.aspiration_fail_high,
            aspiration_fail_low=context.aspiration_fail_low,
            requested_time_budget=context.requested_time_budget,
            elapsed_seconds=elapsed,
            quiescence_nodes=context.quiescence_nodes,
            nodes_per_second=context.nodes / elapsed if elapsed > 0 else 0.0,
            stopped_by_deadline=context.stopped_by_deadline,
            stopped_by_cancellation=context.stopped_by_cancellation,
            stopped_by_adaptive_budget=context.stopped_by_adaptive_budget,
        )

    def _check_stop(self, context: _SearchContext, force_time_check: bool = False) -> None:
        if context.cancel_event.is_set():
            context.stopped = True
            context.stopped_by_cancellation = True
            raise _SearchStopped
        if force_time_check or context.nodes % 16 == 0:
            if context.deadline is not None and monotonic() >= context.deadline:
                context.stopped = True
                context.stopped_by_deadline = True
                raise _SearchStopped
            if context.adaptive_deadline is not None and monotonic() >= context.adaptive_deadline:
                context.stopped = True
                context.stopped_by_deadline = True
                context.stopped_by_adaptive_budget = True
                raise _SearchStopped

    def _visit(self, context: _SearchContext) -> None:
        context.nodes += 1
        # Quiescence nodes call _visit too; tracking is attached in the search
        # routines so normal node totals remain directly comparable.
        self._check_stop(context)

    def _search_root(
        self,
        board: chess.Board,
        legal_moves: list[chess.Move],
        depth: int,
        context: _SearchContext,
        previous_best: chess.Move | None,
    ) -> tuple[int, chess.Move | None, list[tuple[chess.Move, int]]]:
        alpha = context.root_alpha
        beta = context.root_beta
        best_score = -INFINITY
        best_move = None
        root_scores: list[tuple[chess.Move, int]] = []
        ordered = self._order_moves(board, legal_moves, previous_best, context, ply=0)
        for move_index, move in enumerate(ordered):
            old_history = context.repetition_history
            context.repetition_history = self._push_for_search(board, move, old_history)
            try:
                if context.collect_root_scores:
                    score = -self._negamax(board, depth - 1, -INFINITY, INFINITY, 1, context)
                elif context.features.pvs and move_index > 0:
                    score = -self._negamax(board, depth - 1, -alpha - 1, -alpha, 1, context)
                    if alpha < score < beta:
                        context.pvs_researches += 1
                        score = -self._negamax(board, depth - 1, -beta, -alpha, 1, context)
                else:
                    score = -self._negamax(board, depth - 1, -beta, -alpha, 1, context)
            finally:
                board.pop()
                context.repetition_history = old_history
            if score > best_score:
                best_score, best_move = score, move
            root_scores.append((move, score))
            alpha = max(alpha, score)
        root_scores.sort(key=lambda item: item[1], reverse=True)
        return best_score, best_move, root_scores

    def _negamax(
        self,
        board: chess.Board,
        depth: int,
        alpha: int,
        beta: int,
        ply: int,
        context: _SearchContext,
    ) -> int:
        self._visit(context)
        terminal = _terminal_score(board, ply, context.repetition_history)
        if terminal is not None:
            return terminal
        if depth <= 0:
            return self._quiescence(board, alpha, beta, ply, context, 0)

        key = self._tt_key(board, context)
        alpha_start = alpha
        beta_start = beta
        entry = self._table.get(key)
        tt_move = entry.best_move if entry else None
        if entry is not None and entry.depth >= depth:
            tt_score = self._score_from_table(entry.score, ply)
            if entry.bound == _Bound.EXACT:
                return tt_score
            if entry.bound == _Bound.LOWER:
                alpha = max(alpha, tt_score)
            else:
                beta = min(beta, tt_score)
            if alpha >= beta:
                return tt_score

        legal_moves = list(board.legal_moves)
        if not legal_moves:
            return -MATE_SCORE + ply if board.is_check() else 0

        in_check = board.is_check()
        non_pv_node = beta - alpha <= 1
        if (context.features.null_move_pruning and not context.collect_root_scores
                and non_pv_node and null_move_allowed(board, depth)):
            old_history = context.repetition_history
            context.repetition_history = []
            try:
                board.push(chess.Move.null())
                try:
                    reduction = 2 + int(depth >= 6)
                    null_score = -self._negamax(
                        board, depth - 1 - reduction, -beta, -beta + 1, ply + 1, context
                    )
                finally:
                    board.pop()
            finally:
                context.repetition_history = old_history
            if null_score >= beta:
                context.null_move_cutoffs += 1
                return null_score

        best_score = -INFINITY
        best_move = None
        static_eval = self.evaluate(board) if context.features.futility_pruning and depth == 1 \
            and non_pv_node and not in_check and not context.collect_root_scores else -INFINITY
        ordered_moves = self._order_moves(board, legal_moves, tt_move, context, ply)
        killer_pair = context.killers[ply] if ply < len(context.killers) else (None, None)
        for move_index, move in enumerate(ordered_moves):
            is_capture = board.is_capture(move)
            is_quiet = not is_capture and move.promotion is None
            futility_candidate = (
                static_eval != -INFINITY and move_index > 0 and is_quiet
                and static_eval + FUTILITY_MARGIN <= alpha
            )
            reduce_candidate = (
                context.features.lmr and not context.collect_root_scores and depth >= 3
                and move_index >= 3 and is_quiet and not in_check
                and move != tt_move and move not in killer_pair
            )
            old_history = context.repetition_history
            context.repetition_history = self._push_for_search(board, move, old_history)
            score: int | None = None
            try:
                gives_check = board.is_check() if reduce_candidate or futility_candidate else False
                prune_for_futility = False
                if futility_candidate and not gives_check:
                    child_terminal = _terminal_score(board, ply + 1, context.repetition_history)
                    prune_for_futility = futility_prune_allowed(
                        depth=depth, move_index=move_index, quiet=is_quiet, in_check=in_check,
                        gives_check=gives_check, static_eval=static_eval, alpha=alpha,
                        child_terminal=child_terminal is not None,
                        child_is_terminal=board.is_game_over(claim_draw=False),
                    )
                if prune_for_futility:
                    context.futility_prunes += 1
                else:
                    reduction = 0
                    if reduce_candidate and not gives_check:
                        reduction = 2 if depth >= 5 and move_index >= 7 else 1
                    is_pvs = context.features.pvs and move_index > 0
                    if reduction:
                        context.lmr_reductions += 1
                        reduced_depth = max(0, depth - 1 - reduction)
                        if is_pvs:
                            score = -self._negamax(
                                board, reduced_depth, -alpha - 1, -alpha, ply + 1, context
                            )
                        else:
                            score = -self._negamax(
                                board, reduced_depth, -beta, -alpha, ply + 1, context
                            )
                        if score > alpha:
                            context.lmr_researches += 1
                            if is_pvs:
                                score = -self._negamax(
                                    board, depth - 1, -alpha - 1, -alpha, ply + 1, context
                                )
                            else:
                                score = -self._negamax(
                                    board, depth - 1, -beta, -alpha, ply + 1, context
                                )
                    elif is_pvs:
                        score = -self._negamax(
                            board, depth - 1, -alpha - 1, -alpha, ply + 1, context
                        )
                    else:
                        score = -self._negamax(
                            board, depth - 1, -beta, -alpha, ply + 1, context
                        )
                    if context.features.pvs and score is not None and alpha < score < beta:
                        context.pvs_researches += 1
                        score = -self._negamax(
                            board, depth - 1, -beta, -alpha, ply + 1, context
                        )
            finally:
                board.pop()
                context.repetition_history = old_history

            if score is None:
                continue

            if score > best_score:
                best_score, best_move = score, move
            alpha = max(alpha, score)
            if alpha >= beta:
                if not board.is_capture(move) and move.promotion is None:
                    self._record_quiet_cutoff(move, depth, ply, board.turn, context)
                break

        if best_score <= alpha_start:
            bound = _Bound.UPPER
        elif best_score >= beta_start:
            bound = _Bound.LOWER
        else:
            bound = _Bound.EXACT
        self._store(key, _TTEntry(depth, self._score_to_table(best_score, ply), bound, best_move, self._generation))
        return best_score

    def _quiescence(
        self,
        board: chess.Board,
        alpha: int,
        beta: int,
        ply: int,
        context: _SearchContext,
        qply: int,
    ) -> int:
        context.quiescence_nodes += 1
        self._visit(context)
        terminal = _terminal_score(board, ply, context.repetition_history)
        if terminal is not None:
            return terminal

        legal_moves = list(board.legal_moves)
        if not legal_moves:
            return -MATE_SCORE + ply if board.is_check() else 0

        in_check = board.is_check()
        if not in_check:
            stand_pat = self.evaluate(board)
            if stand_pat >= beta:
                return stand_pat
            alpha = max(alpha, stand_pat)
            if qply >= MAX_QUIESCENCE_PLY:
                return stand_pat
            moves = [move for move in legal_moves if board.is_capture(move) or move.promotion]
        else:
            moves = legal_moves
            if qply >= MAX_QUIESCENCE_PLY:
                return self.evaluate(board)

        for move in self._order_moves(board, moves, None, context, ply):
            old_history = context.repetition_history
            context.repetition_history = self._push_for_search(board, move, old_history)
            try:
                score = -self._quiescence(board, -beta, -alpha, ply + 1, context, qply + 1)
            finally:
                board.pop()
                context.repetition_history = old_history
            if score >= beta:
                return score
            alpha = max(alpha, score)
        return alpha

    def _push_for_search(
        self,
        board: chess.Board,
        move: chess.Move,
        history: list[tuple[str, ...]],
    ) -> list[tuple[str, ...]]:
        irreversible = board.is_irreversible(move)
        board.push(move)
        current = _repetition_key(board)
        return [current] if irreversible else history + [current]

    def _tt_key(self, board: chess.Board, context: _SearchContext) -> _TranspositionKey:
        # Including the reversible repetition history prevents reusing a score
        # where the same board has different threefold-claim possibilities.
        return _position_key(board), tuple(context.repetition_history)

    def _store(self, key: _TranspositionKey, entry: _TTEntry) -> None:
        if len(self._table) >= self.transposition_capacity and key not in self._table:
            stale_keys = [k for k, value in self._table.items() if value.generation != self._generation]
            for stale_key in stale_keys[: max(1, self.transposition_capacity // 4)]:
                self._table.pop(stale_key, None)
            if len(self._table) >= self.transposition_capacity:
                self._table.clear()
        self._table[key] = entry

    @staticmethod
    def _score_to_table(score: int, ply: int) -> int:
        if score > MATE_SCORE - 1000:
            return score + ply
        if score < -MATE_SCORE + 1000:
            return score - ply
        return score

    @staticmethod
    def _score_from_table(score: int, ply: int) -> int:
        if score > MATE_SCORE - 1000:
            return score - ply
        if score < -MATE_SCORE + 1000:
            return score + ply
        return score

    def _order_moves(
        self,
        board: chess.Board,
        moves: list[chess.Move],
        tt_move: chess.Move | None,
        context: _SearchContext,
        ply: int,
    ) -> list[chess.Move]:
        killer_pair = context.killers[ply] if ply < len(context.killers) else [None, None]
        color = board.turn

        def score(move: chess.Move) -> int:
            if move == tt_move:
                return 2_000_000
            if move.promotion:
                return 1_500_000 + PIECE_VALUES[move.promotion]
            if board.is_capture(move):
                victim = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
                attacker = board.piece_type_at(move.from_square)
                return 1_000_000 + 10 * PIECE_VALUES.get(victim or chess.PAWN, 0) - PIECE_VALUES.get(attacker or chess.PAWN, 0)
            if move == killer_pair[0]:
                return 900_000
            if move == killer_pair[1]:
                return 800_000
            return context.history.get((color, move.from_square, move.to_square), 0)

        return sorted(moves, key=score, reverse=True)

    def _record_quiet_cutoff(
        self,
        move: chess.Move,
        depth: int,
        ply: int,
        color: chess.Color,
        context: _SearchContext,
    ) -> None:
        if ply < len(context.killers):
            killers = context.killers[ply]
            if move != killers[0]:
                killers[1] = killers[0]
                killers[0] = move
        key = (color, move.from_square, move.to_square)
        context.history[key] = min(1_000_000, context.history.get(key, 0) + depth * depth)

    def _extract_pv(
        self,
        board: chess.Board,
        first_move: chess.Move,
        depth: int,
        context: _SearchContext,
    ) -> tuple[chess.Move, ...]:
        replay = board.copy(stack=True)
        history = list(context.repetition_history)
        pv: list[chess.Move] = []
        move: chess.Move | None = first_move
        for _ in range(depth):
            if move is None or move not in replay.legal_moves:
                break
            pv.append(move)
            history = self._push_for_search(replay, move, history)
            entry = self._table.get(self._tt_key(replay, _SearchContext(None, Event(), repetition_history=history)))
            move = entry.best_move if entry else None
        return tuple(pv)


@dataclass(frozen=True)
class SearchTaskResult:
    request_id: int
    position_key: _WorkerPositionKey
    result: SearchResult | None
    error: Exception | None = None


class EngineWorker:
    """Run searches off the Pygame event loop and discard stale results."""

    def __init__(self, engine: CustomChessEngine | None = None) -> None:
        self.engine = engine or CustomChessEngine()
        self._lock = Lock()
        self._engine_lock = Lock()
        self._results: Queue[SearchTaskResult] = Queue()
        self._progress_results: Queue[SearchTaskResult] = Queue()
        self._request_id = 0
        self._active_id: int | None = None
        self._cancel_event: Event | None = None
        self._thread: Thread | None = None

    @property
    def is_searching(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, position: chess.Board, limits: SearchLimits | None = None) -> int:
        if not isinstance(position, chess.Board):
            raise TypeError("EngineWorker.start expects a chess.Board")
        self.cancel()
        board_copy = position.copy(stack=True)
        key = _worker_position_key(board_copy)
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            event = Event()
            self._cancel_event = event
            self._active_id = request_id
            thread = Thread(
                target=self._run,
                args=(request_id, board_copy, key, limits or SearchLimits(), event),
                name=f"chess-search-{request_id}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        return request_id

    def _run(
        self,
        request_id: int,
        board: chess.Board,
        key: tuple[str, ...],
        limits: SearchLimits,
        event: Event,
    ) -> None:
        try:
            # A cancelled search may still be unwinding when a replacement is
            # queued. Serialize access because the engine owns a shared TT.
            with self._engine_lock:
                result = self.engine.search(
                    board,
                    limits,
                    event,
                    progress_callback=lambda progress: self._progress_results.put(
                        SearchTaskResult(request_id, key, progress)
                    ),
                )
            envelope = SearchTaskResult(request_id, key, result)
        except Exception as exc:  # Deliver worker failures without killing the UI.
            envelope = SearchTaskResult(request_id, key, None, exc)
        self._results.put(envelope)

    def poll(self, current_position: chess.Board | None = None) -> SearchTaskResult | None:
        """Return a completed result only if request and position are current."""
        accepted = None
        while True:
            try:
                result = self._results.get_nowait()
            except Empty:
                break
            with self._lock:
                valid_request = result.request_id == self._active_id
            valid_position = (
                current_position is None
                or result.position_key == _worker_position_key(current_position)
            )
            if valid_request and valid_position:
                accepted = result
        return accepted

    def poll_progress(self, current_position: chess.Board | None = None) -> SearchTaskResult | None:
        """Nonblocking poll for the newest completed iterative-deepening stats."""
        accepted = None
        while True:
            try:
                result = self._progress_results.get_nowait()
            except Empty:
                break
            with self._lock:
                valid_request = result.request_id == self._active_id
            valid_position = (
                current_position is None
                or result.position_key == _worker_position_key(current_position)
            )
            if valid_request and valid_position:
                accepted = result
        return accepted

    def cancel(self) -> None:
        with self._lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._active_id = None
            self._cancel_event = None

    def close(self) -> None:
        self.cancel()
