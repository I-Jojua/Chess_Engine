import unittest
import time

import chess

from chess_ai import EngineWorker, SearchLimits
from game_clock import GameClock, timeout_result
from game_config import Difficulty, SideChoice, TIME_CONTROLS
from game_session import GameSession


class FakeTime:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class FixedChoice:
    def __init__(self, value):
        self.value = value

    def choice(self, choices):
        return self.value


class GameClockTests(unittest.TestCase):
    def test_countdown_uses_elapsed_monotonic_time(self):
        now = FakeTime()
        clock = GameClock(TIME_CONTROLS[0], now=now)
        now.value = 12.75
        self.assertAlmostEqual(clock.remaining(chess.WHITE), 47.25)
        self.assertEqual(clock.remaining(chess.BLACK), 60)

    def test_increment_is_added_after_a_move_and_turn_switches(self):
        now = FakeTime()
        clock = GameClock(TIME_CONTROLS[1], now=now)
        now.value = 10
        self.assertIsNone(clock.complete_move(chess.WHITE))
        self.assertAlmostEqual(clock.remaining(chess.WHITE), 111)
        self.assertEqual(clock.turn, chess.BLACK)
        self.assertEqual(clock.remaining(chess.BLACK), 120)

    def test_flag_fall_and_timeout_winner(self):
        now = FakeTime()
        clock = GameClock(TIME_CONTROLS[0], now=now)
        now.value = 60.1
        self.assertEqual(clock.flag_fall(), chess.WHITE)
        board = chess.Board()
        result = timeout_result(board, chess.WHITE)
        self.assertEqual(result.winner, chess.BLACK)
        self.assertEqual(result.result, "0-1")

    def test_timeout_is_a_draw_if_opponent_cannot_mate(self):
        board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        result = timeout_result(board, chess.WHITE)
        self.assertIsNone(result.winner)
        self.assertEqual(result.result, "1/2-1/2")

    def test_ai_clock_keeps_counting_while_ai_is_thinking(self):
        now = FakeTime()
        session = GameSession(Difficulty.EASY, SideChoice.WHITE, TIME_CONTROLS[0], now=now)
        session.make_human_move(chess.Move.from_uci("e2e4"))
        now.value = 7.5
        self.assertTrue(session.is_ai_turn)
        self.assertAlmostEqual(session.clock.remaining(chess.BLACK), 52.5)

    def test_session_timeout_sets_correct_game_result(self):
        now = FakeTime()
        session = GameSession(Difficulty.EASY, SideChoice.WHITE, TIME_CONTROLS[0], now=now)
        now.value = 61
        outcome = session.update_clock()
        self.assertEqual(outcome.winner, chess.BLACK)
        self.assertEqual(outcome.result, "0-1")
        self.assertTrue(session.is_game_over)


class GameSessionTests(unittest.TestCase):
    def test_session_starts_with_requested_human_and_opposite_ai_sides(self):
        white = GameSession(Difficulty.EASY, SideChoice.WHITE, TIME_CONTROLS[0])
        black = GameSession(Difficulty.EASY, SideChoice.BLACK, TIME_CONTROLS[0])
        self.assertEqual((white.player_color, white.ai_color), (chess.WHITE, chess.BLACK))
        self.assertTrue(white.is_human_turn)
        self.assertEqual((black.player_color, black.ai_color), (chess.BLACK, chess.WHITE))
        self.assertTrue(black.is_ai_turn)

    def test_only_the_correct_side_can_submit_a_move(self):
        session = GameSession(Difficulty.EASY, SideChoice.WHITE, TIME_CONTROLS[0])
        human_move = chess.Move.from_uci("e2e4")
        self.assertFalse(session.make_ai_move(human_move))
        self.assertTrue(session.make_human_move(human_move))
        ai_move = chess.Move.from_uci("e7e5")
        self.assertFalse(session.make_human_move(ai_move))
        self.assertTrue(session.make_ai_move(ai_move))

    def test_random_side_resolution(self):
        session = GameSession(
            Difficulty.MEDIUM,
            SideChoice.RANDOM,
            TIME_CONTROLS[0],
            rng=FixedChoice(chess.BLACK),
        )
        self.assertEqual(session.player_color, chess.BLACK)
        self.assertEqual(session.ai_color, chess.WHITE)

    def test_undo_after_ai_reply_rewinds_human_turn(self):
        session = GameSession(Difficulty.MEDIUM, SideChoice.WHITE, TIME_CONTROLS[4])
        for move in ("e2e4", "e7e5", "g1f3", "b8c6"):
            self.assertTrue(session.make_move(chess.Move.from_uci(move)))
        self.assertTrue(session.is_human_turn)
        self.assertEqual(session.undo_human_turn(), 2)
        self.assertEqual(session.game.board.peek(), chess.Move.from_uci("e7e5"))
        self.assertTrue(session.is_human_turn)
        self.assertTrue(session.redo_move())
        self.assertEqual(session.game.board.peek(), chess.Move.from_uci("g1f3"))
        self.assertTrue(session.redo_move())
        self.assertEqual(session.game.board.peek(), chess.Move.from_uci("b8c6"))

    def test_undo_during_ai_turn_only_rewinds_human_move(self):
        session = GameSession(Difficulty.MEDIUM, SideChoice.WHITE, TIME_CONTROLS[4])
        session.make_move(chess.Move.from_uci("e2e4"))
        self.assertTrue(session.is_ai_turn)
        self.assertEqual(session.undo_human_turn(), 1)
        self.assertTrue(session.is_human_turn)
        self.assertEqual(len(session.game.move_history), 0)

    def test_restart_clears_moves_and_restores_clocks(self):
        now = FakeTime()
        session = GameSession(Difficulty.HARD, SideChoice.WHITE, TIME_CONTROLS[0], now=now)
        now.value = 20
        session.make_move(chess.Move.from_uci("e2e4"))
        session.restart(now=now.value)
        self.assertEqual(session.game.move_history, ())
        self.assertEqual(session.clock.remaining(chess.WHITE), 60)
        self.assertTrue(session.is_human_turn)

    def test_restart_during_ai_search_discards_old_result(self):
        session = GameSession(Difficulty.HARD, SideChoice.BLACK, TIME_CONTROLS[0])
        worker = EngineWorker()
        worker.start(session.game.board, SearchLimits(max_depth=20, time_limit=5))
        worker.cancel()
        session.restart()
        replacement_id = worker.start(session.game.board, SearchLimits(max_depth=1, time_limit=2))
        deadline = time.monotonic() + 1
        while worker.is_searching and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(session.is_ai_turn)
        result = worker.poll(session.game.board)
        self.assertIsNotNone(result)
        self.assertEqual(result.request_id, replacement_id)
        self.assertIn(result.result.best_move, session.game.board.legal_moves)
        worker.close()


if __name__ == "__main__":
    unittest.main()
