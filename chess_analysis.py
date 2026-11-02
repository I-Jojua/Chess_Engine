"""Custom-engine analysis, move review, accuracy, and cancellable workers."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Mapping

import chess

from chess_ai import CustomChessEngine, SearchLimits, SearchResult
from ui_components import evaluation_white_centipawns


@dataclass(frozen=True)
class AnalysisLimits:
    """Per-position analysis budget. Review searches each ply independently."""

    depth: int = 4
    time_limit: float | None = 0.2

    def __post_init__(self) -> None:
        if not 1 <= self.depth <= 64:
            raise ValueError("depth must be between 1 and 64")
        if self.time_limit is not None and self.time_limit < 0:
            raise ValueError("time_limit cannot be negative")

    def search_limits(self) -> SearchLimits:
        return SearchLimits(self.depth, self.time_limit, collect_root_scores=True)


@dataclass(frozen=True)
class PositionAnalysis:
    fen: str
    score_white_cp: int
    best_move: chess.Move | None
    principal_variation: tuple[chess.Move, ...]
    depth: int
    nodes: int
    elapsed_seconds: float
    root_move_scores: tuple[tuple[chess.Move, int], ...] = ()


@dataclass(frozen=True)
class MoveReview:
    ply: int
    move: chess.Move
    san: str
    mover: chess.Color
    classification: str
    centipawn_loss: int
    before_eval_white_cp: int
    after_eval_white_cp: int
    accuracy: float


@dataclass(frozen=True)
class GameReview:
    positions: tuple[PositionAnalysis, ...]
    moves: tuple[MoveReview, ...]
    white_accuracy: float
    black_accuracy: float
    summary: str
    major_swings: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class ReviewProgress:
    completed_positions: int
    total_positions: int
    latest: PositionAnalysis | None = None


@dataclass(frozen=True)
class AnalysisUpdate:
    request_id: int
    kind: str
    progress: ReviewProgress | None = None
    position: PositionAnalysis | None = None
    review: GameReview | None = None
    error: str | None = None
    done: bool = False
    cancelled: bool = False


MOVE_ORDER = (
    "Brilliant", "Great", "Best", "Excellent", "Good", "Inaccuracy", "Mistake", "Blunder"
)


def analyze_position(
    position: chess.Board,
    limits: AnalysisLimits | None = None,
    *,
    engine: CustomChessEngine | None = None,
    cancel_event: Event | None = None,
) -> PositionAnalysis:
    """Analyze a legal position on a stack-preserving copy; return White score."""
    if not isinstance(position, chess.Board):
        raise TypeError("position must be a chess.Board")
    limits = limits or AnalysisLimits()
    work = position.copy(stack=True)
    selected_engine = engine or CustomChessEngine()
    result = selected_engine.search(work, limits.search_limits(), cancel_event)
    if work.is_checkmate():
        score_white = -30_000 if work.turn == chess.WHITE else 30_000
    elif work.is_game_over(claim_draw=True):
        score_white = 0
    else:
        score_white = evaluation_white_centipawns(result.score, work.turn)
    return PositionAnalysis(
        work.fen(en_passant="fen"), score_white, result.best_move,
        result.principal_variation, result.depth, result.nodes,
        result.elapsed_seconds, result.root_move_scores,
    )


def classify_move(
    *,
    move: chess.Move,
    best_move: chess.Move | None,
    centipawn_loss: int,
    legal_move_count: int,
    unique_best_margin_cp: int = 0,
    material_sacrifice: bool = False,
) -> str:
    """Classify using documented centipawn-loss bands and rare tactical flags.

    Policy: ≤10 cp is Best; ≤30 Excellent; ≤80 Good; ≤150 Inaccuracy;
    ≤300 Mistake; above 300 Blunder. "Great" requires the engine's best move
    to lead the runner-up by at least 100 cp. "Brilliant" additionally requires
    a material sacrifice flag and the same strong unique-best margin. This
    deliberately conservative rule is not a proprietary Chess.com metric.
    """
    loss = max(0, int(centipawn_loss))
    is_best = best_move is not None and move == best_move
    unique = is_best and legal_move_count > 1 and unique_best_margin_cp >= 100
    if unique and material_sacrifice and loss <= 10:
        return "Brilliant"
    if unique:
        return "Great"
    if loss <= 10:
        return "Best"
    if loss <= 30:
        return "Excellent"
    if loss <= 80:
        return "Good"
    if loss <= 150:
        return "Inaccuracy"
    if loss <= 300:
        return "Mistake"
    return "Blunder"


def material_sacrifice_indicator(board: chess.Board, move: chess.Move) -> bool:
    """Conservative flag: a non-pawn piece moves onto an undefended attacked square."""
    if move.promotion is not None or board.is_capture(move):
        return False
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type not in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        return False
    mover = board.turn
    child = board.copy(stack=False)
    child.push(move)
    return child.is_attacked_by(child.turn, move.to_square) and not child.is_attacked_by(mover, move.to_square)


def accuracy_from_centipawn_loss(loss: int) -> float:
    """Custom review accuracy: 100 * exp(-max(CPL, 0) / 400), in percent.

    Game accuracy is the arithmetic mean of a player's move accuracies. This
    transparent approximation is only for this application; it does not claim
    to match Chess.com's proprietary accuracy calculation.
    """
    return 100.0 * exp(-max(0, loss) / 400.0)


def evaluation_to_white(score: int, side_to_move: chess.Color) -> int:
    return evaluation_white_centipawns(score, side_to_move)


def board_at_ply(board: chess.Board, ply: int) -> chess.Board:
    """Return an independent board at ply 0..len(move_stack)."""
    if not 0 <= ply <= len(board.move_stack):
        raise ValueError("ply is outside this game's move history")
    position = board.root()
    for move in board.move_stack[:ply]:
        position.push(move)
    return position


class GameReviewAnalyzer:
    def __init__(self, engine: CustomChessEngine | None = None) -> None:
        self.engine = engine or CustomChessEngine()

    def analyze(
        self,
        game: chess.Board,
        limits: AnalysisLimits | None = None,
        *,
        cancel_event: Event | None = None,
        progress_callback=None,
    ) -> GameReview:
        limits = limits or AnalysisLimits()
        if not isinstance(game, chess.Board):
            raise TypeError("game must be a chess.Board")
        source = game.copy(stack=True)
        positions = [board_at_ply(source, ply) for ply in range(len(source.move_stack) + 1)]
        analyses: list[PositionAnalysis] = []
        for index, position in enumerate(positions):
            if cancel_event is not None and cancel_event.is_set():
                break
            analysis = analyze_position(position, limits, engine=self.engine, cancel_event=cancel_event)
            analyses.append(analysis)
            if progress_callback is not None:
                progress_callback(ReviewProgress(index + 1, len(positions), analysis))
            if cancel_event is not None and cancel_event.is_set():
                break
        if len(analyses) != len(positions):
            raise InterruptedError("Game review was cancelled")

        move_reviews: list[MoveReview] = []
        white_accuracy: list[float] = []
        black_accuracy: list[float] = []
        replay = source.root()
        for index, move in enumerate(source.move_stack):
            mover = replay.turn
            san = replay.san(move)
            before, after = analyses[index], analyses[index + 1]
            if mover == chess.WHITE:
                loss = max(0, before.score_white_cp - after.score_white_cp)
            else:
                loss = max(0, after.score_white_cp - before.score_white_cp)
            ordered_scores = sorted((score for _, score in before.root_move_scores), reverse=True)
            margin = ordered_scores[0] - ordered_scores[1] if len(ordered_scores) > 1 else 0
            label = classify_move(
                move=move, best_move=before.best_move, centipawn_loss=loss,
                legal_move_count=replay.legal_moves.count(), unique_best_margin_cp=margin,
                material_sacrifice=material_sacrifice_indicator(replay, move),
            )
            accuracy = accuracy_from_centipawn_loss(loss)
            move_reviews.append(MoveReview(index + 1, move, san, mover, label, loss,
                                           before.score_white_cp, after.score_white_cp, accuracy))
            (white_accuracy if mover == chess.WHITE else black_accuracy).append(accuracy)
            replay.push(move)

        white = sum(white_accuracy) / len(white_accuracy) if white_accuracy else 100.0
        black = sum(black_accuracy) / len(black_accuracy) if black_accuracy else 100.0
        counts = {label: sum(item.classification == label for item in move_reviews) for label in MOVE_ORDER}
        counts_text = ", ".join(f"{name.lower()} {count}" for name, count in counts.items() if count)
        summary = f"White accuracy {white:.1f}%, Black accuracy {black:.1f}%. " + (counts_text or "No moves to review.")
        swings = tuple((item.ply, analyses[item.ply].score_white_cp - analyses[item.ply - 1].score_white_cp)
                       for item in move_reviews
                       if abs(analyses[item.ply].score_white_cp - analyses[item.ply - 1].score_white_cp) >= 200)
        return GameReview(tuple(analyses), tuple(move_reviews), white, black, summary, swings)


class AnalysisWorker:
    """One cancellable daemon worker; polls publish immutable progress/results."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._queue: Queue[AnalysisUpdate] = Queue()
        self._request_id = 0
        self._cancel_event: Event | None = None
        self._thread: Thread | None = None

    def _start(self, kind: str, board: chess.Board, limits: AnalysisLimits) -> int:
        self.cancel()
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            cancel_event = Event()
            self._cancel_event = cancel_event
            source = board.copy(stack=True)
            thread = Thread(target=self._run, args=(request_id, kind, source, limits, cancel_event), daemon=True)
            self._thread = thread
            thread.start()
            return request_id

    def start_position(self, board: chess.Board, limits: AnalysisLimits | None = None) -> int:
        return self._start("position", board, limits or AnalysisLimits())

    def start_review(self, board: chess.Board, limits: AnalysisLimits | None = None) -> int:
        return self._start("review", board, limits or AnalysisLimits())

    def _run(self, request_id: int, kind: str, board: chess.Board, limits: AnalysisLimits, cancel_event: Event) -> None:
        try:
            if kind == "position":
                result = analyze_position(board, limits, cancel_event=cancel_event)
                update = AnalysisUpdate(request_id, kind, position=result, done=True, cancelled=cancel_event.is_set())
                self._queue.put(update)
            else:
                def progress(value: ReviewProgress) -> None:
                    self._queue.put(AnalysisUpdate(request_id, kind, progress=value))
                review = GameReviewAnalyzer().analyze(board, limits, cancel_event=cancel_event, progress_callback=progress)
                self._queue.put(AnalysisUpdate(request_id, kind, review=review, done=True))
        except InterruptedError:
            self._queue.put(AnalysisUpdate(request_id, kind, done=True, cancelled=True))
        except Exception as exc:
            self._queue.put(AnalysisUpdate(request_id, kind, error=str(exc), done=True,
                                           cancelled=cancel_event.is_set()))

    def poll(self, request_id: int) -> AnalysisUpdate | None:
        newest = None
        while True:
            try:
                update = self._queue.get_nowait()
            except Empty:
                return newest
            if update.request_id == request_id:
                newest = update

    def cancel(self) -> None:
        with self._lock:
            if self._cancel_event is not None:
                self._cancel_event.set()

    def is_searching(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float = 2.0) -> bool:
        """Test and shutdown helper; the Pygame path never waits on this method."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True
