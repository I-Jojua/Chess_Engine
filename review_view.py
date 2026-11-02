"""Small pure helpers for evaluation graph navigation and review labels."""

from __future__ import annotations

from typing import Iterable

from chess_analysis import GameReview


def evaluation_graph_data(review: GameReview) -> tuple[tuple[int, int], ...]:
    """Return ``(ply, White centipawns)`` including the initial position."""
    return tuple((ply, point.score_white_cp) for ply, point in enumerate(review.positions))


def graph_x_for_ply(ply: int, count: int, left: int, width: int) -> int:
    if count <= 1:
        return left + width // 2
    return left + round(max(0, min(count - 1, ply)) * width / (count - 1))


def graph_ply_at_x(x: int, count: int, left: int, width: int) -> int:
    """Map a graph click to the closest position (ply), clamped to the graph."""
    if count <= 1 or width <= 0:
        return 0
    fraction = (x - left) / width
    return max(0, min(count - 1, round(fraction * (count - 1))))


def evaluation_band(score_white_cp: int) -> str:
    """Graph meaning: even within ±150 cp, advantage beyond ±500 cp."""
    if score_white_cp >= 500:
        return "white-winning"
    if score_white_cp <= -500:
        return "black-winning"
    if abs(score_white_cp) <= 150:
        return "equal"
    return "white-edge" if score_white_cp > 0 else "black-edge"


def move_label_at_ply(review: GameReview, ply: int) -> str:
    if ply <= 0 or ply > len(review.moves):
        return "Start position" if ply == 0 else ""
    item = review.moves[ply - 1]
    return f"{item.ply}. {item.san} · {item.classification} · {item.centipawn_loss} cp loss"
