import unittest

import chess

from chess_game import GameState, IllegalMoveError, InvalidPositionError


class GameStateTests(unittest.TestCase):
    def test_starting_position_and_legal_moves(self):
        game = GameState()
        self.assertEqual(game.fen, chess.STARTING_FEN)
        self.assertEqual(len(game.get_valid_moves()), 20)
        self.assertEqual(game.result, "*")

    def test_rejects_illegal_move_without_changing_position(self):
        game = GameState()
        before = game.fen
        with self.assertRaises(IllegalMoveError):
            game.make_move("e2e5")
        self.assertEqual(game.fen, before)
        self.assertEqual(game.move_history, ())

    def test_castling_moves_rook_and_undo_restores_position(self):
        fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
        game = GameState(fen)
        game.make_move("e1g1")
        self.assertEqual(game.board.piece_at(chess.F1), chess.Piece(chess.ROOK, chess.WHITE))
        self.assertIsNone(game.board.piece_at(chess.H1))
        game.undo_move()
        self.assertEqual(game.fen, fen)

    def test_en_passant_capture_and_undo(self):
        fen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
        game = GameState(fen)
        game.make_move("e5d6")
        self.assertEqual(game.board.piece_at(chess.D6), chess.Piece(chess.PAWN, chess.WHITE))
        self.assertIsNone(game.board.piece_at(chess.D5))
        game.undo_move()
        self.assertEqual(game.fen, fen)

    def test_promotion_choice(self):
        game = GameState("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
        game.make_move("a7a8n")
        self.assertEqual(game.board.piece_at(chess.A8), chess.Piece(chess.KNIGHT, chess.WHITE))

    def test_checkmate(self):
        game = GameState()
        for move in ("f2f3", "e7e5", "g2g4", "d8h4"):
            game.make_move(move)
        self.assertTrue(game.is_check)
        self.assertTrue(game.is_checkmate)
        self.assertTrue(game.is_game_over)
        self.assertEqual(game.result, "0-1")
        self.assertIn('[Result "0-1"]', game.export_pgn())

    def test_stalemate(self):
        game = GameState("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        self.assertTrue(game.is_stalemate)
        self.assertTrue(game.is_game_over)
        self.assertEqual(game.result, "1/2-1/2")

    def test_undo_and_redo(self):
        game = GameState()
        initial = game.fen
        game.make_move("e2e4")
        after_move = game.fen
        self.assertEqual(game.undo_move(), chess.Move.from_uci("e2e4"))
        self.assertEqual(game.fen, initial)
        self.assertEqual(game.redo_move(), chess.Move.from_uci("e2e4"))
        self.assertEqual(game.fen, after_move)

    def test_new_move_clears_redo_branch(self):
        game = GameState()
        game.make_move("e2e4")
        game.undo_move()
        game.make_move("d2d4")
        self.assertIsNone(game.redo_move())

    def test_fen_loading_and_validation(self):
        game = GameState()
        game.make_move("e2e4")
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
        game.load_fen(fen)
        self.assertEqual(game.fen, fen)
        self.assertEqual(game.move_history, ())
        with self.assertRaises(InvalidPositionError):
            game.load_fen("not a fen")
        with self.assertRaises(InvalidPositionError):
            GameState("8/8/8/8/8/8/8/8 w - - 0 1")

    def test_pgn_export_contains_move_history(self):
        game = GameState()
        game.make_move("e2e4")
        game.make_move("e7e5")
        pgn = game.export_pgn()
        self.assertIn('[Result "*"]', pgn)
        self.assertIn("1. e4 e5", pgn)

    def test_threefold_repetition_can_be_claimed(self):
        game = GameState()
        cycle = ("g1f3", "g8f6", "f3g1", "f6g8")
        for move in cycle * 2:
            game.make_move(move)
        self.assertTrue(game.can_claim_threefold_repetition)
        outcome = game.claim_draw()
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.termination, chess.Termination.THREEFOLD_REPETITION)
        self.assertTrue(game.is_game_over)

    def test_fifty_move_draw_can_be_claimed(self):
        game = GameState("4k3/8/8/8/8/8/8/R3K3 w - - 99 50")
        game.make_move("a1a2")
        self.assertTrue(game.can_claim_fifty_moves)
        outcome = game.claim_draw()
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.termination, chess.Termination.FIFTY_MOVES)

    def test_insufficient_material_ends_game(self):
        game = GameState("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        self.assertTrue(game.is_insufficient_material)
        self.assertTrue(game.is_game_over)
        self.assertEqual(game.get_valid_moves(), [])


if __name__ == "__main__":
    unittest.main()
