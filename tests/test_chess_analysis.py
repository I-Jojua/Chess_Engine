import math
import unittest

import chess

from chess_analysis import (
    AnalysisLimits,
    AnalysisWorker,
    GameReviewAnalyzer,
    accuracy_from_centipawn_loss,
    analyze_position,
    board_at_ply,
    classify_move,
    evaluation_to_white,
)
from chess_ai import CustomChessEngine
from pgn_storage import load_pgn, save_pgn
from review_view import evaluation_band, evaluation_graph_data, graph_ply_at_x, graph_x_for_ply


class ChessAnalysisTests(unittest.TestCase):
    def test_evaluation_perspective_converts_consistently_to_white(self):
        self.assertEqual(evaluation_to_white(125, chess.WHITE), 125)
        self.assertEqual(evaluation_to_white(125, chess.BLACK), -125)
        self.assertEqual(evaluation_to_white(-300, chess.BLACK), 300)

    def test_documented_move_classification_bands_and_rare_labels(self):
        move = chess.Move.from_uci("e2e4")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=0, legal_move_count=20), "Best")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=11, legal_move_count=20), "Excellent")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=31, legal_move_count=20), "Good")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=81, legal_move_count=20), "Inaccuracy")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=151, legal_move_count=20), "Mistake")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=301, legal_move_count=20), "Blunder")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=0, legal_move_count=20,
                                       unique_best_margin_cp=100), "Great")
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=0, legal_move_count=20,
                                       unique_best_margin_cp=100, material_sacrifice=True), "Brilliant")
        # Ordinary best moves are not elevated to Brilliant/Great without evidence.
        self.assertEqual(classify_move(move=move, best_move=move, centipawn_loss=0, legal_move_count=20), "Best")

    def test_accuracy_formula_is_documented_and_monotonic(self):
        self.assertEqual(accuracy_from_centipawn_loss(0), 100.0)
        self.assertAlmostEqual(accuracy_from_centipawn_loss(400), 100 / math.e)
        self.assertGreater(accuracy_from_centipawn_loss(50), accuracy_from_centipawn_loss(150))

    def test_graph_data_bands_and_point_navigation(self):
        board = chess.Board()
        for uci in ("e2e4", "e7e5"):
            board.push_uci(uci)
        review = GameReviewAnalyzer(CustomChessEngine()).analyze(board, AnalysisLimits(1, 0.1))
        points = evaluation_graph_data(review)
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0][0], 0)
        self.assertEqual(graph_x_for_ply(2, 3, 10, 100), 110)
        self.assertEqual(graph_ply_at_x(110, 3, 10, 100), 2)
        self.assertEqual(graph_ply_at_x(-100, 3, 10, 100), 0)
        self.assertEqual(evaluation_band(0), "equal")
        self.assertEqual(evaluation_band(800), "white-winning")
        self.assertEqual(evaluation_band(-800), "black-winning")

    def test_position_navigation_returns_independent_position(self):
        board = chess.Board()
        board.push_uci("e2e4")
        board.push_uci("e7e5")
        at_one = board_at_ply(board, 1)
        self.assertEqual(at_one.peek().uci(), "e2e4")
        at_one.push_uci("c7c5")
        self.assertEqual(len(board.move_stack), 2)
        with self.assertRaises(ValueError):
            board_at_ply(board, 3)

    def test_review_accuracy_and_move_report(self):
        board = chess.Board()
        board.push_uci("e2e4")
        board.push_uci("e7e5")
        review = GameReviewAnalyzer().analyze(board, AnalysisLimits(1, 0.1))
        self.assertEqual(len(review.positions), 3)
        self.assertEqual(len(review.moves), 2)
        self.assertGreaterEqual(review.white_accuracy, 0)
        self.assertLessEqual(review.white_accuracy, 100)
        self.assertIn("White accuracy", review.summary)

    def test_review_keeps_original_completed_game_unchanged(self):
        board = chess.Board()
        for uci in ("e2e4", "c7c5", "g1f3"):
            board.push_uci(uci)
        original_fen, original_stack = board.fen(), tuple(board.move_stack)
        GameReviewAnalyzer().analyze(board, AnalysisLimits(1, 0.1))
        self.assertEqual(board.fen(), original_fen)
        self.assertEqual(tuple(board.move_stack), original_stack)

    def test_analysis_does_not_mutate_original_position(self):
        board = chess.Board()
        board.push_uci("d2d4")
        fen, stack = board.fen(), tuple(board.move_stack)
        analysis = analyze_position(board, AnalysisLimits(2, 0.1))
        self.assertIsNotNone(analysis.best_move)
        self.assertEqual(board.fen(), fen)
        self.assertEqual(tuple(board.move_stack), stack)

    def test_cancelled_background_analysis_finishes_without_review_result(self):
        board = chess.Board()
        worker = AnalysisWorker()
        request_id = worker.start_position(board, AnalysisLimits(64, None))
        worker.cancel()
        self.assertTrue(worker.wait(3.0))
        update = worker.poll(request_id)
        self.assertIsNotNone(update)
        self.assertTrue(update.done)
        self.assertTrue(update.cancelled)

    def test_pgn_save_load_preserves_headers_and_moves(self):
        import tempfile
        from pathlib import Path

        board = chess.Board()
        for uci in ("d2d4", "d7d5", "c2c4"):
            board.push_uci(uci)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            path = save_pgn(Path(directory) / "review.pgn", board, {"White": "Player", "Black": "Local AI", "Event": "Test match"})
            loaded = load_pgn(path)
        self.assertEqual(loaded.board.fen(), board.fen())
        self.assertEqual(tuple(loaded.board.move_stack), tuple(board.move_stack))
        self.assertEqual(loaded.headers["White"], "Player")
        self.assertEqual(loaded.headers["Black"], "Local AI")
        self.assertEqual(loaded.headers["Event"], "Test match")


if __name__ == "__main__":
    unittest.main()
