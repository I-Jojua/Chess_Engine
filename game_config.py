"""Centralized, testable settings for AI difficulty and new games."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import random
from typing import Protocol

import chess

from chess_ai import SearchLimits, SearchResult


class Difficulty(str, Enum):
    EASY = "Easy"
    MEDIUM = "Medium"
    HARD = "Hard"
    EXPERT = "Expert"


class SideChoice(str, Enum):
    WHITE = "White"
    BLACK = "Black"
    RANDOM = "Random"


class TimeCategory(str, Enum):
    BULLET = "Bullet"
    BLITZ = "Blitz"
    RAPID = "Rapid"
    CLASSICAL = "Classical"


@dataclass(frozen=True)
class DifficultyConfig:
    level: Difficulty
    max_depth: int
    time_limit: float
    variety_centipawns: int = 0

    @property
    def search_limits(self) -> SearchLimits:
        return SearchLimits(
            max_depth=self.max_depth,
            time_limit=self.time_limit,
            collect_root_scores=self.variety_centipawns > 0,
        )


@dataclass(frozen=True)
class TimeControl:
    name: str
    category: TimeCategory
    initial_seconds: int
    increment_seconds: int


TIME_CONTROLS = (
    TimeControl("1+0", TimeCategory.BULLET, 60, 0),
    TimeControl("2+1", TimeCategory.BULLET, 120, 1),
    TimeControl("3+0", TimeCategory.BLITZ, 180, 0),
    TimeControl("3+2", TimeCategory.BLITZ, 180, 2),
    TimeControl("5+0", TimeCategory.BLITZ, 300, 0),
    TimeControl("10+0", TimeCategory.RAPID, 600, 0),
    TimeControl("15+10", TimeCategory.RAPID, 900, 10),
    TimeControl("30+0", TimeCategory.CLASSICAL, 1800, 0),
)

EXPERT_MAX_DEPTH = 64
EXPERT_MAX_TIME_SECONDS = 8.0

_DIFFICULTY_CONFIGS = {
    Difficulty.EASY: DifficultyConfig(Difficulty.EASY, 1, 0.60, 180),
    Difficulty.MEDIUM: DifficultyConfig(Difficulty.MEDIUM, 3, 1.50, 55),
    Difficulty.HARD: DifficultyConfig(Difficulty.HARD, 5, 3.00, 0),
}


class ChoiceRandom(Protocol):
    def choice(self, choices): ...


def difficulty_config(
    level: Difficulty,
    *,
    expert_max_depth: int = EXPERT_MAX_DEPTH,
    expert_max_time: float = EXPERT_MAX_TIME_SECONDS,
) -> DifficultyConfig:
    if level == Difficulty.EXPERT:
        if not 1 <= expert_max_depth <= EXPERT_MAX_DEPTH:
            raise ValueError(f"expert_max_depth must be between 1 and {EXPERT_MAX_DEPTH}")
        if not 0 < expert_max_time <= EXPERT_MAX_TIME_SECONDS:
            raise ValueError(
                f"expert_max_time must be greater than 0 and at most {EXPERT_MAX_TIME_SECONDS} seconds"
            )
        return DifficultyConfig(level, expert_max_depth, expert_max_time, 0)
    try:
        return _DIFFICULTY_CONFIGS[level]
    except KeyError as exc:
        raise ValueError(f"Unsupported difficulty: {level!r}") from exc


def resolve_colors(
    side: SideChoice,
    rng: ChoiceRandom | None = None,
) -> tuple[chess.Color, chess.Color]:
    """Return ``(player_color, ai_color)`` for a selected player side."""
    if side == SideChoice.WHITE:
        player_color = chess.WHITE
    elif side == SideChoice.BLACK:
        player_color = chess.BLACK
    elif side == SideChoice.RANDOM:
        player_color = (rng or random).choice((chess.WHITE, chess.BLACK))
    else:
        raise ValueError(f"Unsupported side: {side!r}")
    return player_color, not player_color


def select_ai_move(
    board: chess.Board,
    result: SearchResult,
    level: Difficulty,
    rng: ChoiceRandom | None = None,
) -> chess.Move | None:
    """Apply bounded move variety while always returning a current legal move."""
    legal = set(board.legal_moves)
    ranked = [(move, score) for move, score in result.root_move_scores if move in legal]
    if not ranked:
        return result.best_move if result.best_move in legal else (next(iter(legal), None))

    best_score = max(score for _, score in ranked)
    margin = difficulty_config(level).variety_centipawns
    candidates = [move for move, score in ranked if score >= best_score - margin]
    if not candidates:
        candidates = [max(ranked, key=lambda item: item[1])[0]]
    if margin and len(candidates) > 1:
        return (rng or random).choice(candidates)
    return max(ranked, key=lambda item: item[1])[0]


def time_control_groups() -> dict[TimeCategory, tuple[TimeControl, ...]]:
    return {
        category: tuple(item for item in TIME_CONTROLS if item.category == category)
        for category in TimeCategory
    }
