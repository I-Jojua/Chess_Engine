import threading
import time
import unittest

import chess

from chess_ai import CustomChessEngine, EngineWorker, SearchLimits, evaluate_position


def reference_minimax(board, depth, engine):
    """Small exhaustive negamax reference used to check alpha-beta scores."""
    outcome = board.outcome(claim_draw=False)
    if outcome is not None or board.can_claim_draw():
        if outcome is not None and outcome.winner is not None:
            return -30_000
        return 0
    if depth == 0:
        return engine.evaluate(board)
    moves = list(board.legal_moves)
    if not moves:
        return -30_000 if board.is_check() else 0
    best = -32_000
    for move in moves:
        board.push(move)
        score = -reference_minimax(board, depth - 1, engine)
        board.pop()
        best = max(best, score)
    return best


class CustomEngineTests(unittest.TestCase):
    def test_returns_a_legal_move(self):
        board = chess.Board()
        result = CustomChessEngine().search(board, SearchLimits(max_depth=2, time_limit=2))
        self.assertIsNotNone(result.best_move)
        self.assertIn(result.best_move, board.legal_moves)
        self.assertEqual(result.depth, 2)

    def test_returns_no_move_for_terminal_positions(self):
        checkmate = chess.Board("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")
        stalemate = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        engine = CustomChessEngine()
        self.assertIsNone(engine.search(checkmate).best_move)
        self.assertIsNone(engine.search(stalemate).best_move)

    def test_finds_forced_mate_in_one(self):
        board = chess.Board("7k/8/5KQ1/8/8/8/8/8 w - - 0 1")
        result = CustomChessEngine().search(board, SearchLimits(max_depth=2, time_limit=3))
        self.assertEqual(result.best_move, chess.Move.from_uci("g6g7"))
        self.assertGreater(result.score, 29_000)

    def test_evaluation_recognizes_winning_material(self):
        board = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
        self.assertGreater(evaluate_position(board), 800)

    def test_alpha_beta_matches_exhaustive_reference(self):
        board = chess.Board("4k3/8/8/8/8/8/R7/4K3 w - - 0 1")
        engine = CustomChessEngine()
        expected = reference_minimax(board.copy(stack=True), 2, engine)
        result = engine.search(board, SearchLimits(max_depth=2, time_limit=5))
        self.assertEqual(result.depth, 2)
        self.assertEqual(result.score, expected)

    def test_transposition_table_does_not_change_score(self):
        board = chess.Board()
        baseline = CustomChessEngine(transposition_capacity=1).search(
            board, SearchLimits(max_depth=3, time_limit=5)
        )
        engine = CustomChessEngine(transposition_capacity=10_000)
        first = engine.search(board, SearchLimits(max_depth=3, time_limit=5))
        second = engine.search(board, SearchLimits(max_depth=3, time_limit=5))
        self.assertEqual(first.score, baseline.score)
        self.assertEqual(second.score, baseline.score)

    def test_root_scores_support_variety_and_keep_the_best_move_legal(self):
        board = chess.Board()
        result = CustomChessEngine().search(
            board, SearchLimits(max_depth=1, time_limit=2, collect_root_scores=True)
        )
        self.assertEqual(len(result.root_move_scores), len(list(board.legal_moves)))
        self.assertEqual(result.best_move, result.root_move_scores[0][0])
        self.assertTrue(all(move in board.legal_moves for move, _ in result.root_move_scores))

    def test_search_obeys_time_limit_and_returns_legal_fallback(self):
        board = chess.Board()
        started = time.monotonic()
        result = CustomChessEngine().search(board, SearchLimits(max_depth=20, time_limit=0.03))
        wall_time = time.monotonic() - started
        self.assertIn(result.best_move, board.legal_moves)
        self.assertLess(wall_time, 0.75)
        self.assertLessEqual(result.elapsed_seconds, 0.75)

    def test_pre_cancel_returns_legal_fallback(self):
        board = chess.Board()
        cancel = threading.Event()
        engine = CustomChessEngine()
        # A pre-set token exercises the same cancellation path deterministically.
        cancel.set()
        result = engine.search(board, SearchLimits(max_depth=8, time_limit=5), cancel)
        self.assertTrue(result.cancelled)
        self.assertIn(result.best_move, board.legal_moves)
        self.assertEqual(result.depth, 0)

    def test_cancellation_keeps_last_completed_iteration(self):
        class CancelAfterFirstIteration(CustomChessEngine):
            def _search_root(self, board, legal_moves, depth, context, previous_best):
                completed = super()._search_root(board, legal_moves, depth, context, previous_best)
                if depth == 1:
                    context.cancel_event.set()
                return completed

        board = chess.Board()
        result = CancelAfterFirstIteration().search(
            board, SearchLimits(max_depth=8, time_limit=5)
        )
        self.assertTrue(result.cancelled)
        self.assertEqual(result.depth, 1)
        self.assertIn(result.best_move, board.legal_moves)

    def test_search_does_not_mutate_input_board(self):
        board = chess.Board()
        board.push_uci("e2e4")
        before_fen = board.fen(en_passant="fen")
        before_stack = tuple(board.move_stack)
        CustomChessEngine().search(board, SearchLimits(max_depth=2, time_limit=2))
        self.assertEqual(board.fen(en_passant="fen"), before_fen)
        self.assertEqual(tuple(board.move_stack), before_stack)

    def test_worker_discards_cancelled_search_result(self):
        board = chess.Board()
        worker = EngineWorker()
        worker.start(board, SearchLimits(max_depth=12, time_limit=5))
        worker.cancel()
        deadline = time.monotonic() + 1
        while worker.is_searching and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIsNone(worker.poll(board))
        worker.close()

    def test_worker_publishes_nonblocking_iteration_statistics(self):
        board = chess.Board()
        worker = EngineWorker()
        request_id = worker.start(board, SearchLimits(max_depth=4, time_limit=1))
        deadline = time.monotonic() + 2
        progress = None
        while progress is None and time.monotonic() < deadline:
            progress = worker.poll_progress(board)
            time.sleep(0.005)
        self.assertIsNotNone(progress)
        self.assertEqual(progress.request_id, request_id)
        self.assertGreaterEqual(progress.result.depth, 1)
        worker.cancel()
        worker.close()


if __name__ == "__main__":
    unittest.main()
