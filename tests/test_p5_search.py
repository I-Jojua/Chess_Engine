import threading
import unittest

import chess

from chess_ai import (
    CustomChessEngine,
    SearchFeatures,
    SearchLimits,
    _SearchContext,
    futility_prune_allowed,
    null_move_allowed,
)
from opening_book import LocalOpeningBook


def exhaustive_negamax(board, depth, engine):
    outcome = board.outcome(claim_draw=False)
    if outcome is not None or board.can_claim_draw():
        if outcome is not None and outcome.winner is not None:
            return -30_000
        return 0
    if depth == 0:
        return engine.evaluate(board)
    best = -32_000
    for move in list(board.legal_moves):
        board.push(move)
        best = max(best, -exhaustive_negamax(board, depth - 1, engine))
        board.pop()
    return best


BASE_FEATURES = SearchFeatures(False, False, False, False, False, False)


class P5SearchTests(unittest.TestCase):
    def test_pvs_matches_reference_alpha_beta_and_non_pvs(self):
        board = chess.Board("4k3/8/8/8/8/8/R7/4K3 w - - 0 1")
        reference_engine = CustomChessEngine()
        expected = exhaustive_negamax(board.copy(stack=True), 3, reference_engine)
        plain = CustomChessEngine().search(board, SearchLimits(3, 5, features=BASE_FEATURES))
        pvs_features = SearchFeatures(True, False, False, False, False, False)
        pvs = CustomChessEngine().search(board, SearchLimits(3, 5, features=pvs_features))
        self.assertEqual(plain.score, expected)
        self.assertEqual(pvs.score, expected)
        self.assertIn(pvs.best_move, board.legal_moves)

    def test_aspiration_recovers_from_fail_low_and_fail_high(self):
        class ForceAspirationFailures(CustomChessEngine):
            forced = set()

            def _search_root(self, board, legal_moves, depth, context, previous_best):
                outcome = super()._search_root(board, legal_moves, depth, context, previous_best)
                if context.root_alpha > -32_000 and depth == 2 and 2 not in self.forced:
                    self.forced.add(2)
                    return context.root_alpha - 1, outcome[1], outcome[2]
                if context.root_beta < 32_000 and depth == 3 and 3 not in self.forced:
                    self.forced.add(3)
                    return context.root_beta + 1, outcome[1], outcome[2]
                return outcome

        board = chess.Board()
        engine = ForceAspirationFailures()
        result = engine.search(board, SearchLimits(3, 5))
        no_aspiration = SearchFeatures(True, False, True, True, True, False)
        expected = CustomChessEngine().search(board, SearchLimits(3, 5, features=no_aspiration))
        self.assertEqual(result.score, expected.score)
        self.assertEqual(result.best_move, expected.best_move)
        self.assertGreaterEqual(result.diagnostics.aspiration_fail_low, 1)
        self.assertGreaterEqual(result.diagnostics.aspiration_fail_high, 1)

    def test_lmr_reductions_research_moves_that_raise_alpha(self):
        features = SearchFeatures(True, True, True, False, False, False)
        result = CustomChessEngine().search(chess.Board(), SearchLimits(5, 8, features=features))
        self.assertEqual(result.depth, 5)
        self.assertGreater(result.diagnostics.lmr_reductions, 0)
        self.assertGreater(result.diagnostics.lmr_researches, 0)

    def test_null_move_pruning_rejects_zugzwang_sensitive_positions(self):
        self.assertTrue(null_move_allowed(chess.Board(), 4))
        pawn_endgame = chess.Board("8/5pk1/6p1/8/3P4/5P2/5K2/8 w - - 0 1")
        rook_vs_king = chess.Board("7k/8/8/8/8/8/6R1/6K1 w - - 0 1")
        self.assertFalse(null_move_allowed(pawn_endgame, 4))
        self.assertFalse(null_move_allowed(rook_vs_king, 4))
        in_check = chess.Board("k3r3/8/8/8/8/8/8/4K3 w - - 0 1")
        self.assertTrue(in_check.is_check())
        self.assertFalse(null_move_allowed(in_check, 4))
        near_draw = chess.Board("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 97 50")
        self.assertFalse(null_move_allowed(near_draw, 4))

    def test_null_move_feature_can_be_disabled(self):
        features = SearchFeatures(True, True, False, False, False, False)
        result = CustomChessEngine().search(chess.Board(), SearchLimits(4, 5, features=features))
        self.assertEqual(result.diagnostics.null_move_cutoffs, 0)

    def test_futility_pruning_excludes_tactical_check_and_terminal_moves(self):
        common = dict(depth=1, move_index=3, quiet=True, in_check=False, gives_check=False,
                      static_eval=-200, alpha=0, child_terminal=False, child_is_terminal=False)
        self.assertTrue(futility_prune_allowed(**common))
        for changes in (
            {"quiet": False}, {"in_check": True}, {"gives_check": True},
            {"child_terminal": True}, {"child_is_terminal": True},
            {"depth": 2}, {"move_index": 0},
        ):
            case = dict(common)
            case.update(changes)
            self.assertFalse(futility_prune_allowed(**case), changes)

    def test_futility_feature_can_be_disabled(self):
        features = SearchFeatures(True, True, False, False, False, False)
        result = CustomChessEngine().search(chess.Board(), SearchLimits(3, 5, features=features))
        self.assertEqual(result.diagnostics.futility_prunes, 0)

    def test_move_ordering_is_deterministic_and_honors_tt_move(self):
        board = chess.Board()
        engine = CustomChessEngine()
        state = _SearchContext(None, threading.Event(), killers=[[None, None] for _ in range(8)])
        moves = list(board.legal_moves)
        ordered_a = engine._order_moves(board, moves, None, state, 0)
        ordered_b = engine._order_moves(board, moves, None, state, 0)
        self.assertEqual(ordered_a, ordered_b)
        self.assertEqual(engine._order_moves(board, moves, moves[-1], state, 0)[0], moves[-1])

    def test_opening_book_validates_moves_and_falls_back_to_search(self):
        board = chess.Board()
        key = LocalOpeningBook.position_key(board)
        valid_book = LocalOpeningBook({key: ["e2e4", "e2e5"]})
        result = CustomChessEngine(opening_book=valid_book).search(
            board, SearchLimits(4, 2, features=SearchFeatures(opening_book=True))
        )
        self.assertEqual(result.best_move, chess.Move.from_uci("e2e4"))
        self.assertEqual(result.depth, 0)
        invalid_book = LocalOpeningBook({key: ["e2e5"]})
        fallback = CustomChessEngine(opening_book=invalid_book).search(
            board, SearchLimits(1, 2, features=SearchFeatures(opening_book=True))
        )
        self.assertEqual(fallback.depth, 1)
        self.assertIn(fallback.best_move, board.legal_moves)

    def test_opening_book_disabled_falls_back(self):
        board = chess.Board()
        book = LocalOpeningBook({LocalOpeningBook.position_key(board): ["e2e4"]})
        result = CustomChessEngine(opening_book=book).search(board, SearchLimits(1, 2))
        self.assertEqual(result.depth, 1)

    def test_time_limit_cancellation_and_board_immutability_with_features(self):
        board = chess.Board()
        before_fen, before_moves = board.fen(), tuple(board.move_stack)
        result = CustomChessEngine().search(board, SearchLimits(20, 0.02))
        self.assertIn(result.best_move, board.legal_moves)
        self.assertLess(result.elapsed_seconds, 1.0)
        self.assertEqual(board.fen(), before_fen)
        self.assertEqual(tuple(board.move_stack), before_moves)
        cancel = threading.Event()
        cancel.set()
        cancelled = CustomChessEngine().search(board, SearchLimits(10, 5), cancel)
        self.assertTrue(cancelled.cancelled)
        self.assertIn(cancelled.best_move, board.legal_moves)

    def test_default_features_still_find_forced_mate(self):
        board = chess.Board("7k/8/5KQ1/8/8/8/8/8 w - - 0 1")
        result = CustomChessEngine().search(board, SearchLimits(3, 5))
        self.assertEqual(result.best_move, chess.Move.from_uci("g6g7"))
        self.assertGreater(result.score, 29_000)


if __name__ == "__main__":
    unittest.main()
