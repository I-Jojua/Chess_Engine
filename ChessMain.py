"""Local Pygame chess application controller and screen composition."""

from __future__ import annotations

from pathlib import Path

import chess
import pygame as p

from chess_ai import CustomChessEngine, EngineWorker
from chess_analysis import AnalysisLimits, AnalysisWorker, GameReview, PositionAnalysis, board_at_ply
from game_clock import format_clock
from game_config import Difficulty, SideChoice, TIME_CONTROLS, difficulty_config, select_ai_move
from game_session import GameSession
from pgn_storage import LoadedPGN, load_pgn, save_pgn
from review_view import evaluation_band, evaluation_graph_data, graph_ply_at_x, graph_x_for_ply, move_label_at_ply
from ui_audio import SoundManager
from ui_components import (
    BOARD_THEMES, PIECE_TINTS, PIECE_THEMES, MoveAnimation, UISettings,
    captured_pieces, evaluation_white_centipawns, evaluation_white_share,
    layout_history_rows, layout_panel_text, move_history_rows, promotion_piece_order, square_cell,
)


WIDTH, HEIGHT = 1180, 760
DIMENSION, SQ_SIZE = 8, 72
BOARD_LEFT, BOARD_TOP = 40, 76
BOARD_SIZE = SQ_SIZE * DIMENSION
PANEL_LEFT = BOARD_LEFT + BOARD_SIZE + 40
MAX_FPS = 60
IMAGE_DIR = Path(__file__).resolve().parent / "images"
IMAGES: dict[str, p.Surface] = {}
TINTED_IMAGES: dict[tuple[str, str], p.Surface] = {}
PIECE_CODES = {chess.PAWN: "P", chess.ROOK: "R", chess.KNIGHT: "N", chess.BISHOP: "B", chess.QUEEN: "Q", chess.KING: "K"}
PROMOTION_KEYS = {p.K_1: chess.QUEEN, p.K_2: chess.ROOK, p.K_3: chess.BISHOP, p.K_4: chess.KNIGHT}
DIFFICULTY_BUTTONS = [p.Rect(220 + i * 170, 170, 150, 46) for i in range(4)]
SIDE_BUTTONS = [p.Rect(270 + i * 180, 274, 160, 46) for i in range(3)]
TIME_BUTTONS = [p.Rect(170 + (i % 4) * 210, 385 + (i // 4) * 62, 178, 46) for i in range(len(TIME_CONTROLS))]
MENU_START = p.Rect(424, 530, 250, 58)
MENU_SETTINGS = p.Rect(310, 610, 220, 46)
MENU_LOAD = p.Rect(560, 610, 260, 46)
REVIEW_GRAPH_RECT = p.Rect(PANEL_LEFT + 22, 142, WIDTH - PANEL_LEFT - 44, 150)
REVIEW_BOARD_RECT = p.Rect(BOARD_LEFT, BOARD_TOP, BOARD_SIZE, BOARD_SIZE)
REVIEW_BUTTONS = {
    name: p.Rect(PANEL_LEFT + 18 + index * 108, 625, 102, 36)
    for index, name in enumerate(("start", "previous", "next", "end"))
}
REVIEW_BUTTONS.update({
    "analyze": p.Rect(PANEL_LEFT + 18, 670, 135, 36),
    "save": p.Rect(PANEL_LEFT + 162, 670, 125, 36),
    "return": p.Rect(PANEL_LEFT + 296, 670, 142, 36),
    "reanalyze": p.Rect(PANEL_LEFT + 18, 710, 220, 34),
})
RESULT_NEW = p.Rect(WIDTH // 2 - 205, HEIGHT // 2 + 42, 125, 42)
RESULT_REVIEW = p.Rect(WIDTH // 2 - 62, HEIGHT // 2 + 42, 125, 42)
RESULT_RESTART = p.Rect(WIDTH // 2 + 81, HEIGHT // 2 + 42, 125, 42)
PROMOTION_BUTTONS = [p.Rect(0, 0, 62, 62) for _ in range(4)]


def loadImages() -> None:
    """Load and scale the existing local chess piece images."""
    for color in ("w", "b"):
        for code in PIECE_CODES.values():
            key = color + code
            source = p.image.load(str(IMAGE_DIR / f"{key}.png")).convert_alpha()
            IMAGES[key] = p.transform.smoothscale(source, (SQ_SIZE, SQ_SIZE))
    TINTED_IMAGES.clear()
    for theme in PIECE_THEMES:
        for key, source in IMAGES.items():
            tinted = source.copy()
            color = chess.WHITE if key[0] == "w" else chess.BLACK
            tinted.fill((*PIECE_TINTS[theme][color], 255), special_flags=p.BLEND_RGBA_MULT)
            TINTED_IMAGES[(theme, key)] = tinted


def main() -> None:
    p.init()
    flags = p.FULLSCREEN if False else 0
    screen = p.display.set_mode((WIDTH, HEIGHT), flags)
    p.display.set_caption("Chess — Local Desktop")
    frame_clock = p.time.Clock()
    loadImages()
    sounds = SoundManager(p)
    settings = UISettings()
    engine_worker = EngineWorker(CustomChessEngine())
    analysis_worker = AnalysisWorker()
    position_worker = AnalysisWorker()
    selections: dict[str, object] = {"difficulty": Difficulty.MEDIUM, "side": SideChoice.WHITE, "time_control": TIME_CONTROLS[4]}
    screen_state = "menu"
    return_screen = "menu"
    session: GameSession | None = None
    selected_square: chess.Square | None = None
    legal_moves: list[chess.Move] = []
    pending_promotion: list[chess.Move] = []
    ai_error: str | None = None
    last_search_result = None
    active_search_id: int | None = None
    animation: MoveAnimation | None = None
    resigned_by: chess.Color | None = None
    agreed_draw = False
    notice = ""
    notice_until = 0.0
    review_return_screen = "menu"
    review_source: chess.Board | None = None
    review_headers: dict[str, str] = {}
    review_data: GameReview | None = None
    review_progress = None
    review_request_id: int | None = None
    position_request_id: int | None = None
    position_analysis: PositionAnalysis | None = None
    selected_ply = 0
    explore_board: chess.Board | None = None
    explore_base_ply = 0
    review_selected_square: chess.Square | None = None
    review_depth = 3
    review_time_limit = 0.2
    pgn_notice = ""
    pgn_notice_until = 0.0

    def begin_review(board: chess.Board, headers: dict[str, str] | None = None, return_to: str = "menu") -> None:
        nonlocal screen_state, review_return_screen, review_source, review_headers
        nonlocal review_data, review_progress, review_request_id, position_request_id, position_analysis
        nonlocal selected_ply, explore_board, review_selected_square
        position_worker.cancel()
        review_return_screen = return_to
        review_source = board.copy(stack=True)
        review_headers = dict(headers or {})
        review_data, review_progress, position_analysis, position_request_id = None, None, None, None
        selected_ply = len(review_source.move_stack)
        explore_board, review_selected_square = None, None
        review_request_id = analysis_worker.start_review(review_source, AnalysisLimits(review_depth, review_time_limit))
        screen_state = "review"

    def save_game_dialog() -> None:
        nonlocal pgn_notice, pgn_notice_until
        if session is None:
            return
        try:
            path = _choose_pgn_path(save=True)
            if path:
                result = "*"
                if agreed_draw or (session.outcome is not None and session.outcome.winner is None):
                    result = "1/2-1/2"
                elif resigned_by is not None:
                    result = "0-1" if resigned_by == chess.WHITE else "1-0"
                elif session.outcome is not None:
                    result = "1-0" if session.outcome.winner == chess.WHITE else "0-1"
                headers = {"Event": "Local Chess Game", "White": "You" if session.player_color else "Custom AI",
                           "Black": "You" if session.player_color == chess.BLACK else "Custom AI",
                           "TimeControl": session.time_control.name, "Result": result}
                save_pgn(path, session.game.board, headers)
                pgn_notice = f"Saved PGN: {Path(path).name}"
            else:
                return
        except Exception as exc:
            pgn_notice = f"PGN save failed: {exc}"
        pgn_notice_until = p.time.get_ticks() / 1000.0 + 4.0

    def save_review_dialog() -> None:
        nonlocal pgn_notice, pgn_notice_until
        result = _save_board_dialog(review_source, review_headers)
        if result is None:
            return
        pgn_notice = "PGN saved" if result == "" else f"PGN save failed: {result}"
        pgn_notice_until = p.time.get_ticks() / 1000.0 + 4.0

    def load_game_dialog() -> None:
        nonlocal pgn_notice, pgn_notice_until
        try:
            path = _choose_pgn_path(save=False)
            if not path:
                return
            loaded: LoadedPGN = load_pgn(path)
            pgn_notice = f"Loaded PGN: {Path(path).name}"
            pgn_notice_until = p.time.get_ticks() / 1000.0 + 4.0
            begin_review(loaded.board, dict(loaded.headers), "game" if session is not None else "menu")
        except Exception as exc:
            pgn_notice = f"PGN load failed: {exc}"
            pgn_notice_until = p.time.get_ticks() / 1000.0 + 5.0

    def analyze_review_position() -> None:
        nonlocal position_request_id, position_analysis
        current = explore_board or _review_position(review_source, selected_ply)
        if current is None:
            return
        position_analysis = None
        position_request_id = position_worker.start_position(current, AnalysisLimits(review_depth, review_time_limit))

    def change_review_position(ply: int) -> None:
        nonlocal selected_ply, explore_board, review_selected_square, position_analysis, position_request_id
        if review_source is None:
            return
        position_worker.cancel()
        position_request_id = None
        selected_ply = max(0, min(len(review_source.move_stack), ply))
        explore_board, review_selected_square, position_analysis = None, None, None
    running = True

    def set_notice(text: str) -> None:
        nonlocal notice, notice_until
        notice, notice_until = text, p.time.get_ticks() / 1000.0 + 2.5

    def toggle_fullscreen() -> None:
        nonlocal screen
        settings.fullscreen = not settings.fullscreen
        screen = p.display.set_mode((WIDTH, HEIGHT), p.FULLSCREEN if settings.fullscreen else 0)

    def start_ai_search() -> None:
        nonlocal active_search_id, ai_error
        if session is None or not session.is_ai_turn or resigned_by is not None or agreed_draw:
            return
        profile = difficulty_config(session.difficulty)
        active_search_id = engine_worker.start(session.game.board, profile.search_limits)
        ai_error = None

    def clear_terminal_flags() -> None:
        nonlocal resigned_by, agreed_draw
        resigned_by, agreed_draw = None, False

    def apply_move(move: chess.Move, by_ai: bool = False) -> None:
        nonlocal legal_moves, last_search_result, animation, selected_square
        if session is None:
            return
        was_capture = session.game.board.is_capture(move)
        moved = session.make_ai_move(move) if by_ai else session.make_human_move(move)
        if not moved:
            return
        animation = MoveAnimation.start(move)
        sounds.play("capture" if was_capture else "move", settings.sound_enabled)
        if session.game.board.is_check():
            sounds.play("check", settings.sound_enabled)
        legal_moves = session.game.get_valid_moves()
        if not by_ai:
            last_search_result = None
        selected_square = None
        if session.is_ai_turn:
            start_ai_search()

    def undo() -> None:
        nonlocal active_search_id, last_search_result, selected_square, pending_promotion, animation
        if session is None:
            return
        engine_worker.cancel()
        active_search_id = None
        clear_terminal_flags()
        session.undo_human_turn()
        legal_moves[:] = session.game.get_valid_moves()
        last_search_result, selected_square, pending_promotion, animation = None, None, [], None
        if session.is_ai_turn:
            start_ai_search()

    def restart() -> None:
        nonlocal active_search_id, last_search_result, selected_square, pending_promotion, animation, ai_error
        if session is None:
            return
        engine_worker.cancel()
        active_search_id = None
        session.restart()
        clear_terminal_flags()
        legal_moves[:] = session.game.get_valid_moves()
        last_search_result, selected_square, pending_promotion, animation, ai_error = None, None, [], None, None
        if session.is_ai_turn:
            start_ai_search()

    def finish_as_resigned() -> None:
        nonlocal resigned_by, active_search_id
        if session is None or session.is_game_over:
            return
        resigned_by = session.player_color
        engine_worker.cancel()
        active_search_id = None
        sounds.play("result", settings.sound_enabled)

    def offer_draw() -> None:
        nonlocal agreed_draw, active_search_id
        if session is None or not session.is_human_turn:
            return
        if session.game.can_claim_draw:
            session.claim_draw()
            set_notice("Draw claimed under chess rules")
            return
        # A quick material-only decision keeps the event loop responsive.
        values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
        score = 0
        for square, piece in session.game.board.piece_map().items():
            if piece.piece_type == chess.KING:
                continue
            value = values[piece.piece_type]
            score += value if piece.color == session.ai_color else -value
        if score >= -1:
            agreed_draw = True
            engine_worker.cancel()
            active_search_id = None
            set_notice("The AI accepts the draw")
            sounds.play("result", settings.sound_enabled)
        else:
            set_notice("The AI declines the draw")

    try:
        while running:
            now = p.time.get_ticks() / 1000.0
            if session is not None and screen_state == "game":
                session.update_clock()
                progress = engine_worker.poll_progress(session.game.board)
                if progress is not None and progress.request_id == active_search_id and progress.result is not None:
                    last_search_result = progress.result
                completed = engine_worker.poll(session.game.board)
                if completed is not None and completed.request_id == active_search_id:
                    active_search_id = None
                    if completed.error:
                        ai_error = f"Engine error: {completed.error}"
                    elif completed.result is not None and session.is_ai_turn:
                        last_search_result = completed.result
                        chosen = select_ai_move(session.game.board, completed.result, session.difficulty)
                        if chosen is not None:
                            apply_move(chosen, by_ai=True)
            if screen_state == "review":
                if review_request_id is not None:
                    update = analysis_worker.poll(review_request_id)
                    if update is not None:
                        if update.progress is not None:
                            review_progress = update.progress
                        if update.review is not None:
                            review_data = update.review
                            review_progress = None
                        if update.error:
                            pgn_notice = f"Analysis failed: {update.error}"
                            pgn_notice_until = now + 4.0
                        if update.done:
                            review_request_id = None
                if position_request_id is not None:
                    update = position_worker.poll(position_request_id)
                    if update is not None and update.done:
                        position_analysis = update.position
                        position_request_id = None
                        if update.error:
                            pgn_notice = f"Analysis failed: {update.error}"
                            pgn_notice_until = now + 4.0

            for event in p.event.get():
                if event.type == p.QUIT:
                    running = False
                    continue
                if event.type == p.KEYDOWN:
                    if event.key == p.K_ESCAPE:
                        if screen_state == "settings":
                            screen_state = return_screen
                        elif screen_state == "review":
                            analysis_worker.cancel()
                            position_worker.cancel()
                            screen_state = review_return_screen
                        elif screen_state == "game":
                            screen_state = "menu"
                            engine_worker.cancel()
                            active_search_id = None
                            session = None
                        else:
                            running = False
                        continue
                    if screen_state == "review":
                        if event.key in (p.K_LEFT, p.K_LEFTBRACKET):
                            change_review_position(selected_ply - 1)
                        elif event.key in (p.K_RIGHT, p.K_RIGHTBRACKET):
                            change_review_position(selected_ply + 1)
                        elif event.key == p.K_HOME:
                            change_review_position(0)
                        elif event.key == p.K_END and review_source is not None:
                            change_review_position(len(review_source.move_stack))
                        elif event.key == p.K_v:
                            change_review_position(selected_ply)
                        elif event.key == p.K_BACKSPACE and explore_board is not None and len(explore_board.move_stack) > explore_base_ply:
                            explore_board.pop()
                            position_analysis = None
                            position_worker.cancel()
                            position_request_id = None
                        elif event.key == p.K_a:
                            analyze_review_position()
                        elif event.key in (p.K_1, p.K_2, p.K_3, p.K_4, p.K_5, p.K_6):
                            review_depth = event.key - p.K_0
                        elif event.key in (p.K_PLUS, p.K_EQUALS, p.K_KP_PLUS):
                            review_time_limit = min(2.0, round(review_time_limit + 0.1, 2))
                        elif event.key in (p.K_MINUS, p.K_KP_MINUS):
                            review_time_limit = max(0.1, round(review_time_limit - 0.1, 2))
                        elif event.key == p.K_r and review_source is not None:
                            review_data, review_progress = None, None
                            review_request_id = analysis_worker.start_review(
                                review_source, AnalysisLimits(review_depth, review_time_limit))
                        elif event.key == p.K_s:
                            save_review_dialog()
                        continue
                    if screen_state == "game" and session is not None:
                        if pending_promotion and event.key in PROMOTION_KEYS:
                            wanted = PROMOTION_KEYS[event.key]
                            move = next((item for item in pending_promotion if item.promotion == wanted), None)
                            if move is not None:
                                apply_move(move)
                            pending_promotion = []
                        elif event.key == p.K_z:
                            undo()
                        elif event.key == p.K_y and not session.is_game_over:
                            if session.redo_move():
                                legal_moves[:] = session.game.get_valid_moves()
                                last_search_result = None
                                if session.is_ai_turn:
                                    start_ai_search()
                        elif event.key == p.K_r:
                            restart()
                        elif event.key == p.K_d:
                            offer_draw()
                        elif event.key == p.K_f:
                            toggle_fullscreen()
                        elif event.key == p.K_b:
                            settings.flipped = not settings.flipped
                        elif event.key == p.K_s:
                            return_screen = screen_state
                            screen_state = "settings"
                        elif event.key == p.K_m:
                            engine_worker.cancel()
                            active_search_id = None
                            session = None
                            screen_state = "menu"
                    continue

                if event.type != p.MOUSEBUTTONDOWN or event.button != 1:
                    continue
                if screen_state == "menu":
                    if MENU_START.collidepoint(event.pos):
                        session = GameSession(selections["difficulty"], selections["side"], selections["time_control"])
                        legal_moves = session.game.get_valid_moves()
                        selected_square, pending_promotion, last_search_result = None, [], None
                        ai_error, animation = None, None
                        clear_terminal_flags()
                        settings.flipped = False
                        screen_state = "game"
                        if session.is_ai_turn:
                            start_ai_search()
                    elif MENU_SETTINGS.collidepoint(event.pos):
                        return_screen, screen_state = "menu", "settings"
                    elif MENU_LOAD.collidepoint(event.pos):
                        load_game_dialog()
                    else:
                        _update_menu_selection(selections, event.pos)
                    continue
                if screen_state == "settings":
                    action = settings_action(event.pos)
                    if action == "board":
                        settings.cycle_board_theme()
                    elif action == "pieces":
                        settings.cycle_piece_theme()
                    elif action == "sound":
                        settings.sound_enabled = not settings.sound_enabled
                    elif action == "flip":
                        settings.flipped = not settings.flipped
                    elif action == "fullscreen":
                        toggle_fullscreen()
                    elif action == "back":
                        screen_state = return_screen
                    continue
                if screen_state == "review" and review_source is not None:
                    action = review_control_action(event.pos)
                    if action == "previous":
                        change_review_position(selected_ply - 1)
                    elif action == "next":
                        change_review_position(selected_ply + 1)
                    elif action == "start":
                        change_review_position(0)
                    elif action == "end":
                        change_review_position(len(review_source.move_stack))
                    elif action == "return":
                        analysis_worker.cancel()
                        position_worker.cancel()
                        screen_state = review_return_screen
                    elif action == "analyze":
                        analyze_review_position()
                    elif action == "reanalyze":
                        if review_source is not None:
                            review_data, review_progress = None, None
                            review_request_id = analysis_worker.start_review(
                                review_source, AnalysisLimits(review_depth, review_time_limit))
                    elif action == "save":
                        save_review_dialog()
                    elif REVIEW_GRAPH_RECT.collidepoint(event.pos) and review_data is not None:
                        change_review_position(graph_ply_at_x(event.pos[0], len(review_data.positions),
                                                             REVIEW_GRAPH_RECT.left, REVIEW_GRAPH_RECT.width))
                    elif REVIEW_BOARD_RECT.collidepoint(event.pos):
                        shown_board = explore_board or _review_position(review_source, selected_ply)
                        if shown_board is not None and screenToSquare(event.pos, _review_orientation(review_return_screen, session, settings)) is not None:
                            square = screenToSquare(event.pos, _review_orientation(review_return_screen, session, settings))
                            if review_selected_square is None:
                                piece = shown_board.piece_at(square)
                                if piece and piece.color == shown_board.turn:
                                    review_selected_square = square
                            else:
                                candidates = [move for move in shown_board.legal_moves
                                              if move.from_square == review_selected_square and move.to_square == square]
                                if candidates:
                                    move = next((item for item in candidates if item.promotion == chess.QUEEN), candidates[0])
                                    if explore_board is None:
                                        explore_board = shown_board.copy(stack=True)
                                        explore_base_ply = len(explore_board.move_stack)
                                    explore_board.push(move)
                                    review_selected_square = None
                                    position_analysis = None
                                    position_worker.cancel()
                                    position_request_id = None
                                else:
                                    piece = shown_board.piece_at(square)
                                    review_selected_square = square if piece and piece.color == shown_board.turn else None
                    continue
                if screen_state != "game" or session is None:
                    continue

                if _game_finished(session, resigned_by, agreed_draw):
                    if RESULT_NEW.collidepoint(event.pos):
                        engine_worker.cancel()
                        active_search_id, session, screen_state = None, None, "menu"
                    elif RESULT_REVIEW.collidepoint(event.pos):
                        headers = {"White": "You" if session.player_color else "Custom AI",
                                   "Black": "You" if session.player_color == chess.BLACK else "Custom AI",
                                   "TimeControl": session.time_control.name}
                        begin_review(session.game.board, headers, "game")
                    elif RESULT_RESTART.collidepoint(event.pos):
                        restart()
                    continue

                action = control_action(event.pos, screen.get_size())
                if action == "new":
                    engine_worker.cancel()
                    active_search_id, session, screen_state = None, None, "menu"
                elif action == "undo":
                    undo()
                elif action == "redo" and not session.is_game_over:
                    if session.redo_move():
                        legal_moves[:] = session.game.get_valid_moves()
                        last_search_result = None
                        if session.is_ai_turn:
                            start_ai_search()
                elif action == "restart":
                    restart()
                elif action == "resign":
                    finish_as_resigned()
                elif action == "draw":
                    offer_draw()
                elif action == "flip":
                    settings.flipped = not settings.flipped
                elif action == "settings":
                    return_screen, screen_state = "game", "settings"
                elif action == "fullscreen":
                    toggle_fullscreen()
                elif action == "sound":
                    settings.sound_enabled = not settings.sound_enabled
                elif action == "review":
                    if _game_finished(session, resigned_by, agreed_draw):
                        headers = {"White": "You" if session.player_color else "Custom AI",
                                   "Black": "You" if session.player_color == chess.BLACK else "Custom AI",
                                   "TimeControl": session.time_control.name}
                        begin_review(session.game.board, headers, "game")
                    else:
                        set_notice("Finish the game before opening Game Review")
                elif action == "save":
                    save_game_dialog()
                elif action == "load":
                    load_game_dialog()
                elif pending_promotion:
                    selected = next((move for rect, move in zip(PROMOTION_BUTTONS, _ordered_promotions(pending_promotion)) if rect.collidepoint(event.pos)), None)
                    if selected is not None:
                        apply_move(selected)
                        pending_promotion = []
                    continue
                elif session.is_game_over or resigned_by is not None or agreed_draw or not session.is_human_turn:
                    continue
                else:
                    square = screenToSquare(event.pos, _orientation(session, settings))
                    if square is None:
                        continue
                    if selected_square is None:
                        piece = session.game.board.piece_at(square)
                        if piece and piece.color == session.player_color:
                            selected_square = square
                        continue
                    if square == selected_square:
                        selected_square = None
                        continue
                    candidates = [m for m in legal_moves if m.from_square == selected_square and m.to_square == square]
                    promotions = [m for m in candidates if m.promotion]
                    if promotions:
                        pending_promotion = promotions
                    elif candidates:
                        apply_move(candidates[0])
                    else:
                        piece = session.game.board.piece_at(square)
                        selected_square = square if piece and piece.color == session.player_color else None

            if screen_state == "menu":
                drawMenu(screen, selections, settings, pgn_notice if now < pgn_notice_until else "")
            elif screen_state == "settings":
                drawSettings(screen, settings, return_screen)
            elif screen_state == "review" and review_source is not None:
                drawReview(screen, review_source, selected_ply, review_data, review_progress,
                           explore_board, explore_base_ply, review_selected_square, position_analysis, position_worker.is_searching(),
                           review_headers, _review_orientation(review_return_screen, session, settings),
                           settings, review_depth, review_time_limit, pgn_notice if now < pgn_notice_until else "")
            elif session is not None:
                drawGameState(screen, session, selected_square, legal_moves, pending_promotion,
                              last_search_result, engine_worker.is_searching, ai_error,
                              settings, animation, resigned_by, agreed_draw, notice if now < notice_until else "")
            frame_clock.tick(MAX_FPS)
            p.display.flip()
    finally:
        engine_worker.close()
        analysis_worker.cancel()
        position_worker.cancel()
        p.quit()


def _orientation(session: GameSession, settings: UISettings) -> chess.Color:
    return not session.player_color if settings.flipped else session.player_color


def _ordered_promotions(moves: list[chess.Move]) -> list[chess.Move]:
    return [next((move for move in moves if move.promotion == kind), moves[0]) for kind in promotion_piece_order()]


def _controls(screen_width: int = WIDTH, screen_height: int = HEIGHT) -> dict[str, p.Rect]:
    labels = ("new", "undo", "redo", "restart", "resign", "draw", "flip", "settings",
              "fullscreen", "sound", "review", "save", "load")
    scale = max(0.25, min(1.0, screen_height / HEIGHT))
    panel_left = min(PANEL_LEFT, screen_width)
    content_left = min(panel_left + round(22 * scale), max(0, screen_width - 1))
    content_width = max(1, screen_width - content_left - round(22 * scale))
    gap = max(3, round(8 * scale))
    button_width = max(1, (content_width - gap * 2) // 3)
    button_height = max(14, round(30 * scale))
    row_gap = max(1, round(6 * scale))
    row_count = (len(labels) + 2) // 3
    controls_height = row_count * button_height + (row_count - 1) * row_gap
    top = max(0, screen_height - controls_height - round(18 * scale))
    return {name: p.Rect(content_left + (index % 3) * (button_width + gap),
                         top + (index // 3) * (button_height + row_gap),
                         button_width, button_height)
            for index, name in enumerate(labels)}


def control_action(position: tuple[int, int], screen_size: tuple[int, int] = (WIDTH, HEIGHT)) -> str | None:
    return next((name for name, rect in _controls(*screen_size).items() if rect.collidepoint(position)), None)


def review_control_action(position: tuple[int, int]) -> str | None:
    return next((name for name, rect in REVIEW_BUTTONS.items() if rect.collidepoint(position)), None)


def _review_position(source: chess.Board | None, ply: int) -> chess.Board | None:
    return board_at_ply(source, ply) if source is not None else None


def _review_orientation(return_state: str, session: GameSession | None, settings: UISettings) -> chess.Color:
    return _orientation(session, settings) if return_state == "game" and session is not None else chess.WHITE


def _save_board_dialog(board: chess.Board | None, headers: dict[str, str] | None = None) -> str | None:
    if board is None:
        return None
    try:
        path = _choose_pgn_path(save=True)
        if path:
            save_pgn(path, board, headers)
            return ""
    except Exception as exc:
        return str(exc)
    return None


def _choose_pgn_path(save: bool) -> str | None:
    # Native Windows dialogs are used only on explicit user action.
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if save:
            return filedialog.asksaveasfilename(
                title="Save local chess game", defaultextension=".pgn",
                filetypes=(("PGN chess games", "*.pgn"), ("All files", "*.*")),
            ) or None
        return filedialog.askopenfilename(
            title="Load local chess game", filetypes=(("PGN chess games", "*.pgn"), ("All files", "*.*")),
        ) or None
    finally:
        root.destroy()


def settings_action(position: tuple[int, int]) -> str | None:
    choices = {
        "board": p.Rect(330, 260, 520, 54), "pieces": p.Rect(330, 330, 520, 54),
        "sound": p.Rect(330, 400, 520, 54), "flip": p.Rect(330, 470, 520, 54),
        "fullscreen": p.Rect(330, 540, 520, 54), "back": p.Rect(450, 630, 280, 54),
    }
    return next((name for name, rect in choices.items() if rect.collidepoint(position)), None)


def drawGameState(screen: p.Surface, session: GameSession, selected_square=None, valid_moves=None,
                  pending_promotion=None, search_result=None, ai_thinking=False, ai_error=None,
                  settings: UISettings | None = None, animation: MoveAnimation | None = None,
                  resigned_by=None, agreed_draw=False, notice="") -> None:
    settings = settings or UISettings()
    orientation = _orientation(session, settings)
    board = session.game.board
    screen.fill(p.Color("#171a1f"))
    drawBoard(screen, orientation, settings.theme)
    if board.move_stack:
        last = board.peek()
        drawSquareHighlight(screen, last.from_square, orientation, settings.theme.last_move)
        drawSquareHighlight(screen, last.to_square, orientation, settings.theme.last_move)
    if board.is_check():
        king = board.king(board.turn)
        if king is not None:
            drawSquareHighlight(screen, king, orientation, settings.theme.check)
    if selected_square is not None:
        drawSquareHighlight(screen, selected_square, orientation, settings.theme.selected)
        for move in valid_moves or ():
            if move.from_square == selected_square:
                drawMoveHint(screen, move.to_square, orientation, move.to_square in board.piece_map())
    drawPieces(screen, board, orientation, settings, animation)
    _drawCoordinates(screen, orientation)
    drawStatus(screen, session, pending_promotion or (), search_result, ai_thinking, ai_error,
               settings, resigned_by, agreed_draw, notice)
    if pending_promotion:
        _drawPromotionPicker(screen, pending_promotion)
    if _game_finished(session, resigned_by, agreed_draw):
        _drawResultOverlay(screen, session, resigned_by, agreed_draw)


def drawReview(screen, source, selected_ply, review, progress, explore_board, explore_base_ply,
               selected_square, position_analysis, position_thinking, headers, orientation, settings,
               depth=3, time_limit=0.2, notice="") -> None:
    """Render a review board and graph without changing the original game."""
    screen.fill(p.Color("#171a1f"))
    board = explore_board or _review_position(source, selected_ply)
    if board is None:
        return
    drawBoard(screen, orientation, settings.theme)
    if board.move_stack:
        drawSquareHighlight(screen, board.peek().from_square, orientation, settings.theme.last_move)
        drawSquareHighlight(screen, board.peek().to_square, orientation, settings.theme.last_move)
    if selected_square is not None:
        drawSquareHighlight(screen, selected_square, orientation, settings.theme.selected)
        for move in board.legal_moves:
            if move.from_square == selected_square:
                drawMoveHint(screen, move.to_square, orientation, board.is_capture(move))
    drawPieces(screen, board, orientation, settings)
    _drawCoordinates(screen, orientation)
    p.draw.rect(screen, p.Color("#20252b"), (PANEL_LEFT, 0, WIDTH - PANEL_LEFT, HEIGHT))
    p.draw.line(screen, p.Color("#3c444d"), (PANEL_LEFT, 0), (PANEL_LEFT, HEIGHT), 2)
    x = PANEL_LEFT + 22
    title, body, small = p.font.Font(None, 32), p.font.Font(None, 24), p.font.Font(None, 19)
    screen.blit(title.render("GAME REVIEW", True, p.Color("#f1f3f5")), (x, 18))
    if review is None:
        status = "Analyzing positions…"
        if progress is not None:
            status = f"Analyzing position {progress.completed_positions}/{progress.total_positions}"
        screen.blit(body.render(status, True, p.Color("#f4d35e")), (x, 59))
        screen.blit(small.render("Custom engine · depth 1–6 · A analyzes selected position", True,
                                 p.Color("#bcc5cd")), (x, 89))
    else:
        white_name = headers.get("White", "White") or "White"
        black_name = headers.get("Black", "Black") or "Black"
        screen.blit(small.render(f"{white_name}: {review.white_accuracy:.1f}%   {black_name}: {review.black_accuracy:.1f}%",
                                 True, p.Color("#dce1e6")), (x, 58))
        counts = {name: sum(item.classification == name for item in review.moves)
                  for name in ("Brilliant", "Great", "Best", "Excellent", "Good", "Inaccuracy", "Mistake", "Blunder")}
        summary = "Moves: " + " · ".join(f"{name} {counts[name]}" for name in ("Brilliant", "Great", "Best", "Excellent"))
        screen.blit(small.render(summary, True, p.Color("#aeb9c2")), (x, 82))
        remainder = "Errors: " + " · ".join(f"{name} {counts[name]}" for name in ("Good", "Inaccuracy", "Mistake", "Blunder"))
        screen.blit(small.render(remainder, True, p.Color("#aeb9c2")), (x, 103))
        swing_text = "Major swings: " + (", ".join(f"ply {ply} {cp / 100:+.1f}" for ply, cp in review.major_swings[:3]) or "none")
        screen.blit(small.render(swing_text[:55], True, p.Color("#aeb9c2")), (x, 123))
        drawEvaluationGraph(screen, review, selected_ply)
        label = move_label_at_ply(review, selected_ply)
        screen.blit(small.render(label[:48], True, p.Color("#f4d35e")), (x, 300))
        if explore_board is not None:
            screen.blit(small.render(f"Variation: {len(explore_board.move_stack) - explore_base_ply} ply · V returns to game line · Backspace takes back",
                                     True, p.Color("#aab5c0")), (x, 323))
        analysis = position_analysis if explore_board is not None else (
            review.positions[selected_ply] if selected_ply < len(review.positions) else None
        )
        if analysis is not None:
            white_score = analysis.score_white_cp / 100
            screen.blit(body.render(f"Position eval: {white_score:+.2f} White", True, p.Color("#e4e8eb")), (x, 350))
            best = analysis.best_move.uci() if analysis.best_move is not None else "—"
            pv = _format_pv(board, analysis.principal_variation)
            screen.blit(small.render(f"Best move: {best} · depth {analysis.depth} · {analysis.nodes:,} nodes"[:52],
                                     True, p.Color("#c7d0d8")), (x, 382))
            screen.blit(small.render(f"PV: {pv}"[:52], True, p.Color("#c7d0d8")), (x, 405))
            if analysis.best_move is not None:
                _drawBestMoveArrow(screen, analysis.best_move, orientation)
        elif position_thinking:
            screen.blit(body.render("Analyzing selected variation…", True, p.Color("#f4d35e")), (x, 350))
        # Recent move labels keep review useful even when the full PGN is long.
        if review.moves:
            screen.blit(small.render("RECENT MOVES", True, p.Color("#aab5c0")), (x, 438))
            for offset, item in enumerate(review.moves[max(0, selected_ply - 5):selected_ply][-5:]):
                text = f"{item.ply:>3}. {item.san:<8} {item.classification:<11} {item.centipawn_loss:>4} cp"
                screen.blit(small.render(text, True, p.Color("#d7dce1")), (x, 461 + offset * 23))
        screen.blit(small.render(f"Analysis: depth {depth}, {time_limit:.1f}s/position · 1–6 depth · +/- time · R reruns", True,
                                 p.Color("#9da7b3")), (x, 590))
    labels = {"start": "|<", "previous": "<", "next": ">", "end": ">|",
              "analyze": "Analyze (A)", "save": "Save PGN", "return": "Return", "reanalyze": "Re-run full review (R)"}
    for name, rect in REVIEW_BUTTONS.items():
        _drawButton(screen, rect, labels[name], rect.collidepoint(p.mouse.get_pos()), name == "analyze", small)
    if notice:
        screen.blit(small.render(notice[:52], True, p.Color("#9ed49c")), (x, HEIGHT - 39))


def drawEvaluationGraph(screen, review: GameReview, selected_ply: int) -> None:
    rect = REVIEW_GRAPH_RECT
    p.draw.rect(screen, p.Color("#171b20"), rect, border_radius=6)
    center = rect.centery
    scale = rect.height / 2
    zero_y = center
    white_win_y = round(center - 500 / 1000 * scale)
    black_win_y = round(center + 500 / 1000 * scale)
    equal_half = round(150 / 1000 * scale)
    p.draw.rect(screen, p.Color("#27362b"), (rect.left + 1, rect.top + 1, rect.width - 2, white_win_y - rect.top))
    p.draw.rect(screen, p.Color("#392a2b"), (rect.left + 1, black_win_y, rect.width - 2, rect.bottom - black_win_y - 1))
    p.draw.rect(screen, p.Color("#26342c"), (rect.left + 1, center - equal_half, rect.width - 2, equal_half * 2))
    legend = p.font.Font(None, 16)
    screen.blit(legend.render("WHITE WINNING", True, p.Color("#c8d7c9")), (rect.left + 8, rect.top + 4))
    screen.blit(legend.render("EQUAL", True, p.Color("#9bc5a0")), (rect.left + 8, center - 9))
    screen.blit(legend.render("BLACK WINNING", True, p.Color("#d7c6c7")), (rect.left + 8, rect.bottom - 18))
    p.draw.line(screen, p.Color("#8c959d"), (rect.left, zero_y), (rect.right, zero_y), 1)
    for threshold in (-500, 500):
        y = round(center - threshold / 1000 * scale)
        p.draw.line(screen, p.Color("#454d55"), (rect.left, y), (rect.right, y), 1)
    points = []
    data = evaluation_graph_data(review)
    for ply, score in data:
        px = graph_x_for_ply(ply, len(data), rect.left + 3, rect.width - 6)
        py = round(center - max(-1000, min(1000, score)) / 1000 * scale)
        points.append((px, py))
    if len(points) > 1:
        p.draw.lines(screen, p.Color("#e7c66b"), False, points, 3)
    for ply, point in enumerate(points):
        color = {"white-winning": "#f4f4f4", "black-winning": "#b8c0c8", "equal": "#92c697",
                 "white-edge": "#dfe5e9", "black-edge": "#aeb8c1"}[evaluation_band(data[ply][1])]
        p.draw.circle(screen, p.Color(color), point, 5 if ply == selected_ply else 3)
    p.draw.rect(screen, p.Color("#606970"), rect, 1, border_radius=6)


def _drawBestMoveArrow(screen, move, orientation) -> None:
    start_col, start_row = square_cell(move.from_square, orientation == chess.BLACK)
    end_col, end_row = square_cell(move.to_square, orientation == chess.BLACK)
    start = (BOARD_LEFT + start_col * SQ_SIZE + SQ_SIZE // 2, BOARD_TOP + start_row * SQ_SIZE + SQ_SIZE // 2)
    end = (BOARD_LEFT + end_col * SQ_SIZE + SQ_SIZE // 2, BOARD_TOP + end_row * SQ_SIZE + SQ_SIZE // 2)
    p.draw.line(screen, p.Color("#27a7b8"), start, end, 7)
    direction = p.Vector2(end) - p.Vector2(start)
    if direction.length_squared():
        direction = direction.normalize()
        perpendicular = p.Vector2(-direction.y, direction.x)
        tip = p.Vector2(end)
        base = tip - direction * 22
        points = (tip, base + perpendicular * 10, base - perpendicular * 10)
        p.draw.polygon(screen, p.Color("#27a7b8"), points)


def _format_pv(board: chess.Board, variation) -> str:
    replay = board.copy(stack=True)
    san_moves = []
    for move in variation[:6]:
        if move not in replay.legal_moves:
            break
        san_moves.append(replay.san(move))
        replay.push(move)
    return " ".join(san_moves) or "—"


def drawBoard(screen: p.Surface, orientation=chess.WHITE, theme=None) -> None:
    theme = theme or BOARD_THEMES[0]
    for row in range(8):
        for col in range(8):
            file_index, rank_index = (col, 7 - row) if orientation == chess.WHITE else (7 - col, row)
            color = theme.light if (file_index + rank_index) % 2 == 0 else theme.dark
            p.draw.rect(screen, color, (BOARD_LEFT + col * SQ_SIZE, BOARD_TOP + row * SQ_SIZE, SQ_SIZE, SQ_SIZE))


def drawSquareHighlight(screen, square, orientation, color) -> None:
    col, row = square_cell(square, orientation == chess.BLACK)
    overlay = p.Surface((SQ_SIZE, SQ_SIZE), p.SRCALPHA)
    overlay.fill(color)
    screen.blit(overlay, (BOARD_LEFT + col * SQ_SIZE, BOARD_TOP + row * SQ_SIZE))


def drawMoveHint(screen, square, orientation, capture=False) -> None:
    col, row = square_cell(square, orientation == chess.BLACK)
    rect = p.Rect(BOARD_LEFT + col * SQ_SIZE, BOARD_TOP + row * SQ_SIZE, SQ_SIZE, SQ_SIZE)
    if capture:
        p.draw.circle(screen, (40, 40, 40, 125), rect.center, SQ_SIZE // 2 - 4, 4)
    else:
        p.draw.circle(screen, (40, 40, 40, 110), rect.center, SQ_SIZE // 7)


def drawPieces(screen, board, orientation=chess.WHITE, settings=None, animation=None) -> None:
    settings = settings or UISettings()
    active = animation if animation and animation.progress() < 1 else None
    animated_square = active.move.to_square if active else None
    for square, piece in board.piece_map().items():
        if square == animated_square:
            continue
        col, row = square_cell(square, orientation == chess.BLACK)
        key = ("w" if piece.color else "b") + PIECE_CODES[piece.piece_type]
        screen.blit(TINTED_IMAGES.get((settings.piece_theme_name, key), IMAGES[key]), (BOARD_LEFT + col * SQ_SIZE, BOARD_TOP + row * SQ_SIZE))
    if active:
        move = active.move
        piece = board.piece_at(move.to_square)
        if piece:
            start = square_cell(move.from_square, orientation == chess.BLACK)
            end = square_cell(move.to_square, orientation == chess.BLACK)
            t = active.progress()
            x = BOARD_LEFT + (start[0] + (end[0] - start[0]) * t) * SQ_SIZE
            y = BOARD_TOP + (start[1] + (end[1] - start[1]) * t) * SQ_SIZE
            key = ("w" if piece.color else "b") + PIECE_CODES[piece.piece_type]
            screen.blit(TINTED_IMAGES.get((settings.piece_theme_name, key), IMAGES[key]), (round(x), round(y)))


def _drawCoordinates(screen, orientation) -> None:
    font = p.font.Font(None, 18)
    for col in range(8):
        file_name = chr(ord("a") + (col if orientation == chess.WHITE else 7 - col))
        screen.blit(font.render(file_name, True, p.Color("#e6e9ed")), (BOARD_LEFT + col * SQ_SIZE + SQ_SIZE - 12, BOARD_TOP + BOARD_SIZE + 4))
    for row in range(8):
        rank_name = str(8 - row if orientation == chess.WHITE else row + 1)
        screen.blit(font.render(rank_name, True, p.Color("#e6e9ed")), (BOARD_LEFT - 17, BOARD_TOP + row * SQ_SIZE + 3))


def _drawButton(screen, rect, label, hovered=False, active=False, font=None) -> None:
    font = font or p.font.Font(None, 23)
    fill = p.Color("#55744e" if active else "#45505a" if hovered else "#303740")
    p.draw.rect(screen, fill, rect, border_radius=7)
    p.draw.rect(screen, p.Color("#94ac91" if active else "#56616d"), rect, 1, border_radius=7)
    text = font.render(label, True, p.Color("#ffffff" if hovered or active else "#d5dbe1"))
    screen.blit(text, text.get_rect(center=rect.center))


def _fit_status_text(text: str, font: p.font.Font, max_width: int, color) -> p.Surface:
    """Keep panel text within its available width, even in compact layouts."""
    if max_width <= 0:
        return font.render("", True, color)
    value = text
    while value and font.size(value)[0] > max_width:
        value = value[:-1]
    if value != text and value:
        value = value[:-1] + "…"
    return font.render(value, True, color)


def drawStatus(screen, session, pending_promotion, search_result=None, ai_thinking=False,
               ai_error=None, settings=None, resigned_by=None, agreed_draw=False, notice="") -> None:
    settings = settings or UISettings()
    board = session.game.board
    screen_width, screen_height = screen.get_size()
    scale = max(0.25, min(1.0, screen_height / HEIGHT))
    panel_left = min(PANEL_LEFT, screen_width)
    panel_width = max(0, screen_width - panel_left)
    x = min(panel_left + round(22 * scale), max(0, screen_width - 1))
    content_width = max(0, screen_width - x - round(22 * scale))
    screen.fill(p.Color("#20252b"), (panel_left, 0, panel_width, screen_height))
    p.draw.line(screen, p.Color("#3c444d"), (panel_left, 0), (panel_left, screen_height), 2)
    title = p.font.Font(None, max(15, round(34 * scale)))
    body = p.font.Font(None, max(12, round(23 * scale)))
    small = p.font.Font(None, max(11, round(20 * scale)))
    screen.blit(title.render("MATCH", True, p.Color("#f0f2f4")), (x, round(18 * scale)))
    ai_name = f"Custom AI · {session.difficulty.value}"
    cards = ((chess.WHITE, "You" if session.player_color else ai_name),
             (chess.BLACK, "You" if session.player_color == chess.BLACK else ai_name))
    y = round(62 * scale)
    card_height = max(22, round(58 * scale))
    card_step = max(card_height + 2, round(67 * scale))
    for color, label in cards:
        active = session.clock.turn == color and not session.is_game_over and resigned_by is None and not agreed_draw
        rect = p.Rect(x, y, content_width, card_height)
        p.draw.rect(screen, p.Color("#3e5844" if active else "#2c3239"), rect, border_radius=8)
        p.draw.circle(screen, p.Color("#f4f4f4" if color else "#16191d"), (x + round(18 * scale), y + round(19 * scale)), max(2, round(8 * scale)))
        card_text_width = max(0, content_width - round(46 * scale))
        screen.blit(_fit_status_text(label, body, card_text_width, p.Color("#e7eaed")),
                    (x + round(34 * scale), y + round(6 * scale)))
        value = format_clock(session.clock.remaining(color))
        screen.blit(title.render(value, True, p.Color("#ffffff")), (x + round(34 * scale), y + round(28 * scale)))
        y += card_step
    y += round(5 * scale)
    # Evaluation bar uses the last completed search score, normalized to White's side.
    bar = p.Rect(x, y, content_width, max(4, round(15 * scale)))
    p.draw.rect(screen, p.Color("#090b0d"), bar, border_radius=5)
    white_share = 0.5
    if search_result is not None:
        cp = evaluation_white_centipawns(search_result.score, session.ai_color)
        white_share = evaluation_white_share(cp)
    fill_width = round(bar.width * white_share)
    p.draw.rect(screen, p.Color("#e7e7e7"), (bar.x, bar.y, fill_width, bar.height), border_radius=5)
    p.draw.rect(screen, p.Color("#60656b"), bar, 1, border_radius=5)
    y += round(25 * scale)
    screen.blit(small.render("MOVE HISTORY", True, p.Color("#aab5c0")), (x, y))
    y += round(22 * scale)
    rows = move_history_rows(board)
    history_height = max(16, round(86 * scale))
    history_rect = p.Rect(x, y, content_width, history_height)
    p.draw.rect(screen, p.Color("#191d22"), history_rect, border_radius=6)
    history_line_height = max(small.get_linesize(), round(24 * scale))
    history_layout = layout_history_rows(rows, y + max(3, round(7 * scale)),
                                         history_height - max(6, round(14 * scale)),
                                         max_rows=3, line_height=history_line_height)
    for (number, white, black), line_y in history_layout:
        line = f"{number:>2}.  {white:<8} {black}"
        screen.blit(small.render(line[:34], True, p.Color("#d7dce1")), (x + round(10 * scale), line_y))
    y += round(94 * scale)
    white_caps, black_caps = captured_pieces(board)
    screen.blit(small.render("Captured", True, p.Color("#aab5c0")), (x, y))
    if content_width < round(360 * scale):
        captured_rows = ((chess.WHITE, white_caps), (chess.BLACK, black_caps))
        for row_index, (captured_by, types) in enumerate(captured_rows):
            label = "White: " if captured_by else "Black: "
            material = " ".join(chess.piece_symbol(kind).upper() for kind in types) or "—"
            screen.blit(small.render(label + material, True, p.Color("#e1e5e9")), (x, y + round(21 * scale) + row_index * small.get_linesize()))
        y += round(46 * scale) + small.get_linesize()
    else:
        for captured_by, types in ((chess.WHITE, white_caps), (chess.BLACK, black_caps)):
            label = "White: " if captured_by else "Black: "
            material = " ".join(chess.piece_symbol(kind).upper() for kind in types) or "—"
            screen.blit(small.render(label + material, True, p.Color("#e1e5e9")), (x + (0 if captured_by else round(180 * scale)), y + round(21 * scale)))
        y += round(46 * scale)
    if pending_promotion:
        status = "Choose a promotion piece"
    elif resigned_by is not None:
        status = "You resigned" if resigned_by == session.player_color else "AI resigned"
    elif agreed_draw:
        status = "Draw by agreement"
    elif session.outcome:
        outcome = session.outcome
        if outcome.winner is None:
            status = "Draw"
        else:
            winner = "White" if outcome.winner == chess.WHITE else "Black"
            status = f"{winner} wins"
        reason = getattr(outcome, "reason", None)
        if reason is None and getattr(outcome, "termination", None) is not None:
            reason = outcome.termination.name.replace("_", " ").title()
        if reason:
            status += f" · {reason}"
    else:
        status = "Your turn" if session.is_human_turn else ("AI thinking…" if ai_thinking else "AI to move")
        if board.is_check():
            status += " · Check"
    status_lines = [status]
    if ai_error:
        status_lines.append(f"Engine error: {ai_error[:28]}")
    if search_result is not None:
        diagnostics = search_result.diagnostics
        nps = int(diagnostics.nodes_per_second) if diagnostics.nodes_per_second else 0
        cp_white = evaluation_white_centipawns(search_result.score, session.ai_color)
        budget = diagnostics.requested_time_budget
        budget_text = f"{search_result.elapsed_seconds:.2f}/{budget:.1f}s" if budget is not None else f"{search_result.elapsed_seconds:.2f}s"
        stop_reason = "time" if diagnostics.stopped_by_deadline else (
            "cancelled" if diagnostics.stopped_by_cancellation else "complete"
        )
        status_lines.extend((
            f"AI depth {search_result.depth} · {search_result.nodes:,} nodes · {diagnostics.quiescence_nodes:,} Q",
            f"{nps:,} NPS · eval {cp_white / 100:+.2f} · {budget_text} · {stop_reason}",
        ))
    elif ai_thinking:
        status_lines.append("Searching for a move…")
    if notice:
        status_lines.append(notice[:42])
    controls = _controls(screen_width, screen_height)
    controls_top = min((rect.top for rect in controls.values()), default=screen_height)
    text_top = y + round(3 * scale)
    text_bottom = min(screen_height, controls_top - round(5 * scale))
    line_height = max(small.get_linesize() + 1, round(21 * scale))
    laid_out = layout_panel_text(tuple(status_lines), text_top, text_bottom, line_height=line_height)
    for index, (line, line_y) in enumerate(laid_out):
        color = p.Color("#f4d35e") if index == 0 else (
            p.Color("#ff7777") if line.startswith("Engine error:") else
            p.Color("#9ed49c") if line == notice else p.Color("#bfc8d1")
        )
        screen.blit(_fit_status_text(line, small, content_width, color), (x, line_y))
    control_labels = {"new": "New game", "undo": "Undo", "redo": "Redo", "restart": "Restart",
                      "resign": "Resign", "draw": "Offer draw", "flip": "Flip board",
                      "settings": "Settings", "fullscreen": "Fullscreen", "sound": "Sound On" if settings.sound_enabled else "Sound Off",
                      "review": "Game Review", "save": "Save PGN", "load": "Load PGN"}
    mouse = p.mouse.get_pos()
    for name, rect in controls.items():
        _drawButton(screen, rect, control_labels[name], rect.collidepoint(mouse), name in ("undo", "redo") and not board.move_stack, small)
    if pending_promotion:
        screen.blit(small.render("Choose promotion", True, p.Color("#ffffff")), (BOARD_LEFT + 175, BOARD_TOP + 210))


def _game_finished(session, resigned_by, agreed_draw) -> bool:
    return session.is_game_over or resigned_by is not None or agreed_draw


def _drawResultOverlay(screen, session, resigned_by, agreed_draw) -> None:
    shade = p.Surface((WIDTH, HEIGHT), p.SRCALPHA)
    shade.fill((0, 0, 0, 155))
    screen.blit(shade, (0, 0))
    card = p.Rect(WIDTH // 2 - 250, HEIGHT // 2 - 100, 500, 230)
    p.draw.rect(screen, p.Color("#252c32"), card, border_radius=14)
    p.draw.rect(screen, p.Color("#6e7b85"), card, 1, border_radius=14)
    if agreed_draw:
        result = "Draw by agreement"
    elif resigned_by is not None:
        result = "You resigned" if resigned_by == session.player_color else "AI resigned"
    elif session.outcome.winner is None:
        result = "Draw"
    else:
        winner = "White" if session.outcome.winner == chess.WHITE else "Black"
        result = f"{winner} wins"
    title = p.font.Font(None, 42).render(result, True, p.Color("#f2f4f6"))
    screen.blit(title, title.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 38)))
    _drawButton(screen, RESULT_NEW, "New game", RESULT_NEW.collidepoint(p.mouse.get_pos()), False)
    _drawButton(screen, RESULT_REVIEW, "Game Review", RESULT_REVIEW.collidepoint(p.mouse.get_pos()), False)
    _drawButton(screen, RESULT_RESTART, "Play again", RESULT_RESTART.collidepoint(p.mouse.get_pos()), True)


def _drawPromotionPicker(screen, moves) -> None:
    shade = p.Surface((WIDTH, HEIGHT), p.SRCALPHA)
    shade.fill((0, 0, 0, 145))
    screen.blit(shade, (0, 0))
    ordered = _ordered_promotions(moves)
    left = BOARD_LEFT + BOARD_SIZE // 2 - 136
    top = BOARD_TOP + BOARD_SIZE // 2 - 45
    box = p.Rect(left - 12, top - 36, 296, 116)
    p.draw.rect(screen, p.Color("#242a30"), box, border_radius=10)
    font = p.font.Font(None, 24)
    screen.blit(font.render("Promote pawn", True, p.Color("#ffffff")), (left + 55, top - 30))
    for index, move in enumerate(ordered):
        rect = p.Rect(left + index * 66, top, 62, 62)
        PROMOTION_BUTTONS[index] = rect
        # The destination rank identifies the promoting side.
        key = "w" + PIECE_CODES[move.promotion] if moves[0].to_square // 8 == 7 else "b" + PIECE_CODES[move.promotion]
        image = TINTED_IMAGES.get(("Classic", key), IMAGES.get(key))
        if image:
            screen.blit(image, rect)
        screen.blit(font.render(str(index + 1), True, p.Color("#d3d8de")), (rect.right - 15, rect.bottom - 17))


def drawMenu(screen, selections, settings=None, notice="") -> None:
    settings = settings or UISettings()
    _drawBackground(screen, "#171b20", "#25332d")
    title, heading, button = p.font.Font(None, 66), p.font.Font(None, 30), p.font.Font(None, 25)
    _center_text(screen, title, "CHESS", 85, p.Color("#f2f5f7"))
    _center_text(screen, heading, "A focused local chess experience", 128, p.Color("#aeb8c2"))
    for label, y, rects, options, key, formatter in (
        ("AI difficulty", 158, DIFFICULTY_BUTTONS, list(Difficulty), "difficulty", lambda x: x.value),
        ("Choose your side", 262, SIDE_BUTTONS, list(SideChoice), "side", lambda x: x.value),
        ("Time control", 370, TIME_BUTTONS, list(TIME_CONTROLS), "time_control", lambda x: f"{x.category.value}  {x.name}"),
    ):
        screen.blit(heading.render(label, True, p.Color("#e6e9ed")), (115, y))
        for rect, option in zip(rects, options):
            _drawButton(screen, rect, formatter(option), rect.collidepoint(p.mouse.get_pos()), selections[key] == option, button)
    _drawButton(screen, MENU_START, "Start playing", MENU_START.collidepoint(p.mouse.get_pos()), True, heading)
    _drawButton(screen, MENU_SETTINGS, f"Settings · {settings.theme.name}", MENU_SETTINGS.collidepoint(p.mouse.get_pos()), False, button)
    _drawButton(screen, MENU_LOAD, "Load PGN for review", MENU_LOAD.collidepoint(p.mouse.get_pos()), False, button)
    if notice:
        _center_text(screen, p.font.Font(None, 20), notice[:90], 675, p.Color("#9ed49c"))
    _center_text(screen, p.font.Font(None, 21), "Local play · custom engine · no account required", 710, p.Color("#89949f"))


def drawSettings(screen, settings, return_screen="menu") -> None:
    _drawBackground(screen, "#171b20", "#25332d")
    title, button = p.font.Font(None, 54), p.font.Font(None, 29)
    _center_text(screen, title, "SETTINGS", 115, p.Color("#f2f5f7"))
    labels = (f"Board theme: {settings.theme.name}", f"Piece theme: {settings.piece_theme_name}",
              f"Sound effects: {'On' if settings.sound_enabled else 'Off'}", f"Board orientation: {'Flipped' if settings.flipped else 'Standard'}",
              f"Window mode: {'Fullscreen' if settings.fullscreen else 'Windowed'}")
    for rect, label in zip((p.Rect(330, 260 + index * 70, 520, 54) for index in range(5)), labels):
        _drawButton(screen, rect, label, rect.collidepoint(p.mouse.get_pos()), False, button)
    _drawButton(screen, p.Rect(450, 630, 280, 54), "Back to game" if return_screen == "game" else "Back to menu", False, True, button)


def _drawBackground(screen, top, bottom) -> None:
    screen.fill(p.Color(top))
    color = p.Color(bottom)
    p.draw.rect(screen, color, (0, HEIGHT - 190, WIDTH, 190))


def _center_text(screen, font, text, y, color) -> None:
    rendered = font.render(text, True, color)
    screen.blit(rendered, rendered.get_rect(center=(WIDTH // 2, y)))


def _update_menu_selection(selections, position) -> None:
    for rect, option in zip(DIFFICULTY_BUTTONS, Difficulty):
        if rect.collidepoint(position):
            selections["difficulty"] = option
            return
    for rect, option in zip(SIDE_BUTTONS, SideChoice):
        if rect.collidepoint(position):
            selections["side"] = option
            return
    for rect, option in zip(TIME_BUTTONS, TIME_CONTROLS):
        if rect.collidepoint(position):
            selections["time_control"] = option
            return


def squareToBoardCell(square, orientation=chess.WHITE):
    return square_cell(square, orientation == chess.BLACK)


def screenToSquare(position, orientation=chess.WHITE):
    x, y = position[0] - BOARD_LEFT, position[1] - BOARD_TOP
    if not (0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE):
        return None
    col, row = x // SQ_SIZE, y // SQ_SIZE
    return chess.square(7 - col, row) if orientation == chess.BLACK else chess.square(col, 7 - row)


if __name__ == "__main__":
    main()
