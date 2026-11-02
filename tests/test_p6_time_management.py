import threading
import time
import unittest

import chess

from chess_ai import CustomChessEngine, SearchFeatures, SearchLimits, adaptive_budget_share
from game_config import Difficulty, difficulty_config


class P6TimeManagementTests(unittest.TestCase):
    def test_difficulty_budgets_are_within_requested_ranges(self):
        expected = {
            Difficulty.EASY: (0.5, 1.0),
            Difficulty.MEDIUM: (1.0, 2.0),
            Difficulty.HARD: (2.0, 4.0),
            Difficulty.EXPERT: (5.0, 8.0),
        }
        for level, (minimum, maximum) in expected.items():
            with self.subTest(level=level):
                config = difficulty_config(level)
                self.assertGreaterEqual(config.time_limit, minimum)
                self.assertLessEqual(config.time_limit, maximum)
                self.assertEqual(config.search_limits.time_limit, config.time_limit)

    def test_adaptive_budget_gives_tactical_positions_more_time(self):
        quiet = chess.Board()
        tactical = chess.Board("7k/8/5KQ1/8/8/8/8/8 w - - 0 1")
        self.assertEqual(adaptive_budget_share(quiet, list(quiet.legal_moves)), 0.55)
        self.assertEqual(adaptive_budget_share(tactical, list(tactical.legal_moves)), 0.90)

    def test_search_reports_budget_elapsed_quiescence_and_stop_reason(self):
        result = CustomChessEngine().search(
            chess.Board(), SearchLimits(64, 0.04, features=SearchFeatures(adaptive_time_management=False))
        )
        info = result.diagnostics
        self.assertEqual(info.requested_time_budget, 0.04)
        self.assertAlmostEqual(info.elapsed_seconds, result.elapsed_seconds)
        self.assertGreater(info.quiescence_nodes, 0)
        self.assertGreaterEqual(info.nodes_per_second, 0)
        self.assertTrue(info.stopped_by_deadline)
        self.assertFalse(info.stopped_by_cancellation)
        self.assertLess(result.elapsed_seconds, 0.5)

    def test_quiescence_observes_expired_deadline_and_keeps_legal_fallback(self):
        class ExpireInsideQuiescence(CustomChessEngine):
            def _quiescence(self, board, alpha, beta, ply, context, qply):
                context.deadline = time.monotonic() - 1
                return super()._quiescence(board, alpha, beta, ply, context, qply)

        board = chess.Board()
        result = ExpireInsideQuiescence().search(
            board, SearchLimits(4, 5, features=SearchFeatures(adaptive_time_management=False))
        )
        self.assertEqual(result.depth, 0)
        self.assertIn(result.best_move, board.legal_moves)
        self.assertTrue(result.diagnostics.stopped_by_deadline)
        self.assertGreaterEqual(result.diagnostics.quiescence_nodes, 1)

    def test_aspiration_retry_uses_same_deadline_and_last_completed_iteration(self):
        class ExpireAfterAspirationFailure(CustomChessEngine):
            def _search_root(self, board, legal_moves, depth, context, previous_best):
                result = super()._search_root(board, legal_moves, depth, context, previous_best)
                if depth == 2 and context.root_alpha > -32_000:
                    context.deadline = time.monotonic() - 1
                    return context.root_alpha - 1, result[1], result[2]
                return result

        board = chess.Board()
        result = ExpireAfterAspirationFailure().search(
            board, SearchLimits(5, 5, features=SearchFeatures(adaptive_time_management=False))
        )
        self.assertEqual(result.depth, 1)
        self.assertIn(result.best_move, board.legal_moves)
        self.assertTrue(result.diagnostics.stopped_by_deadline)

    def test_adaptive_budget_stops_easy_positions_early_and_can_be_disabled(self):
        board = chess.Board()
        adaptive = CustomChessEngine().search(board, SearchLimits(64, 0.15))
        self.assertTrue(adaptive.diagnostics.stopped_by_adaptive_budget)
        fixed = CustomChessEngine().search(
            board, SearchLimits(64, 0.04, features=SearchFeatures(adaptive_time_management=False))
        )
        self.assertFalse(fixed.diagnostics.stopped_by_adaptive_budget)
        self.assertTrue(fixed.diagnostics.stopped_by_deadline)

    def test_cancellation_is_observed_while_searching(self):
        board = chess.Board()
        cancel = threading.Event()
        results = []
        thread = threading.Thread(
            target=lambda: results.append(CustomChessEngine().search(
                board, SearchLimits(64, None), cancel
            )),
            daemon=True,
        )
        thread.start()
        time.sleep(0.03)
        cancel.set()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0].cancelled)
        self.assertTrue(results[0].diagnostics.stopped_by_cancellation)
        self.assertIn(results[0].best_move, board.legal_moves)

    def test_deadline_before_first_iteration_returns_legal_fallback(self):
        board = chess.Board()
        result = CustomChessEngine().search(board, SearchLimits(64, 0))
        self.assertEqual(result.depth, 0)
        self.assertIn(result.best_move, board.legal_moves)
        self.assertTrue(result.diagnostics.stopped_by_deadline)


if __name__ == "__main__":
    unittest.main()
