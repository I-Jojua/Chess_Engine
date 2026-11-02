import unittest

import chess

from chess_ai import SearchResult
from game_config import (
    Difficulty,
    SideChoice,
    TIME_CONTROLS,
    difficulty_config,
    resolve_colors,
    select_ai_move,
    time_control_groups,
)


class FixedChoice:
    def __init__(self, value):
        self.value = value

    def choice(self, choices):
        if self.value in choices:
            return self.value
        return choices[-1]


class GameConfigTests(unittest.TestCase):
    def test_each_difficulty_has_centralized_limits(self):
        easy = difficulty_config(Difficulty.EASY)
        medium = difficulty_config(Difficulty.MEDIUM)
        hard = difficulty_config(Difficulty.HARD)
        expert = difficulty_config(Difficulty.EXPERT)

        self.assertEqual(easy.max_depth, 1)
        self.assertLess(easy.time_limit, medium.time_limit)
        self.assertLess(medium.max_depth, hard.max_depth)
        self.assertGreater(hard.time_limit, medium.time_limit)
        self.assertEqual(expert.max_depth, 64)
        self.assertEqual(expert.time_limit, 8.0)
        self.assertGreaterEqual(easy.time_limit, 0.5)
        self.assertLessEqual(easy.time_limit, 1.0)
        self.assertGreaterEqual(medium.time_limit, 1.0)
        self.assertLessEqual(medium.time_limit, 2.0)
        self.assertGreaterEqual(hard.time_limit, 2.0)
        self.assertLessEqual(hard.time_limit, 4.0)
        self.assertGreaterEqual(expert.time_limit, 5.0)
        self.assertLessEqual(expert.time_limit, 8.0)
        self.assertEqual(expert.variety_centipawns, 0)
        self.assertTrue(easy.search_limits.collect_root_scores)
        self.assertTrue(medium.search_limits.collect_root_scores)
        self.assertFalse(hard.search_limits.collect_root_scores)

    def test_expert_limits_are_configurable_within_bounds(self):
        config = difficulty_config(Difficulty.EXPERT, expert_max_depth=12, expert_max_time=6)
        self.assertEqual((config.max_depth, config.time_limit), (12, 6))
        with self.assertRaises(ValueError):
            difficulty_config(Difficulty.EXPERT, expert_max_depth=65)

    def test_all_requested_time_controls_are_present_and_grouped(self):
        self.assertEqual([control.name for control in TIME_CONTROLS], [
            "1+0", "2+1", "3+0", "3+2", "5+0", "10+0", "15+10", "30+0"
        ])
        self.assertEqual(len(time_control_groups()), 4)

    def test_white_and_black_side_choices_set_opposite_ai_color(self):
        self.assertEqual(resolve_colors(SideChoice.WHITE), (chess.WHITE, chess.BLACK))
        self.assertEqual(resolve_colors(SideChoice.BLACK), (chess.BLACK, chess.WHITE))

    def test_random_side_choice_uses_injected_random_source(self):
        self.assertEqual(
            resolve_colors(SideChoice.RANDOM, FixedChoice(chess.BLACK)),
            (chess.BLACK, chess.WHITE),
        )

    def test_variety_never_selects_an_illegal_move(self):
        board = chess.Board()
        scores = tuple((move, 20 - index * 15) for index, move in enumerate(board.legal_moves))
        result = SearchResult(scores[0][0], scores[0][1], 1, 20, 0.1, root_move_scores=scores)
        move = select_ai_move(board, result, Difficulty.EASY, FixedChoice(scores[-1][0]))
        self.assertIn(move, board.legal_moves)


if __name__ == "__main__":
    unittest.main()
