import unittest

import chess

from ui_components import (
    BOARD_THEMES,
    PIECE_THEMES,
    MoveAnimation,
    UISettings,
    captured_pieces,
    evaluation_white_centipawns,
    evaluation_white_share,
    layout_history_rows,
    layout_panel_text,
    move_history_rows,
    square_cell,
)


class UIComponentTests(unittest.TestCase):
    def test_theme_settings_cycle_board_and_piece_themes(self):
        settings = UISettings()
        initial = settings.theme
        settings.cycle_board_theme()
        settings.cycle_piece_theme()
        self.assertNotEqual(settings.theme, initial)
        self.assertEqual(len(BOARD_THEMES), 4)
        self.assertEqual(len(PIECE_THEMES), 3)
        self.assertEqual(settings.piece_theme_name, "Ivory")

    def test_square_coordinates_follow_board_orientation(self):
        self.assertEqual(square_cell(chess.A1), (0, 7))
        self.assertEqual(square_cell(chess.H8), (7, 0))
        self.assertEqual(square_cell(chess.A1, flipped=True), (7, 0))

    def test_animation_progress_is_clamped_and_nonblocking(self):
        animation = MoveAnimation(chess.Move.from_uci("e2e4"), 10.0, 0.2)
        self.assertEqual(animation.progress(9.0), 0.0)
        self.assertAlmostEqual(animation.progress(10.1), 0.5)
        self.assertEqual(animation.progress(11.0), 1.0)

    def test_evaluation_is_normalized_to_white_and_bounded(self):
        self.assertEqual(evaluation_white_centipawns(250, chess.WHITE), 250)
        self.assertEqual(evaluation_white_centipawns(250, chess.BLACK), -250)
        self.assertGreater(evaluation_white_share(250), 0.5)
        self.assertLess(evaluation_white_share(-250), 0.5)
        self.assertEqual(evaluation_white_share(10000), 0.97)

    def test_move_history_and_captured_pieces_include_en_passant(self):
        board = chess.Board()
        for uci in ("e2e4", "a7a6", "e4e5", "d7d5", "e5d6"):
            board.push_uci(uci)
        rows = move_history_rows(board)
        self.assertEqual(rows[0], (1, "e4", "a6"))
        self.assertEqual(rows[-1], (3, "exd6", ""))
        white_captured, black_captured = captured_pieces(board)
        self.assertEqual(white_captured, (chess.PAWN,))
        self.assertEqual(black_captured, ())

    def test_history_layout_handles_zero_one_two_and_many_move_rows(self):
        rows = ((1, "e4", "e5"), (2, "Nf3", "Nc6"), (3, "Bb5", "a6"), (4, "Ba4", "Nf6"))
        self.assertEqual(layout_history_rows((), 20, 70), ())
        self.assertEqual(layout_history_rows(rows[:1], 20, 70), ((rows[0], 20),))
        self.assertEqual(layout_history_rows(rows[:2], 20, 70), ((rows[0], 20), (rows[1], 44)))
        self.assertEqual(layout_history_rows(rows, 20, 72), ((rows[1], 20), (rows[2], 44), (rows[3], 68)))

    def test_history_and_status_layout_respect_available_panel_height(self):
        rows = ((1, "e4", "e5"), (2, "Nf3", "Nc6"), (3, "Bb5", "a6"))
        self.assertEqual(layout_history_rows(rows, 12, 30, line_height=20), ((rows[2], 12),))
        status = ("Your turn · Check", "AI depth 4 · 10,000 nodes", "Engine error: timeout")
        layout = layout_panel_text(status, 100, 140, line_height=20)
        self.assertEqual(layout, ((status[0], 100), (status[1], 120)))
        self.assertEqual(layout_panel_text(status, 100, 115, line_height=20), ())


if __name__ == "__main__":
    unittest.main()
