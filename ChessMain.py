
import pygame as p
import ChessEngine
print("ENGINE FILE:", ChessEngine.__file__)
print("HAS MOVE:", hasattr(ChessEngine, "Move"))
print(dir(ChessEngine))

WIDTH = HEIGHT = 512
DIMENSION = 8
SQ_SIZE = HEIGHT // DIMENSION
MAX_FPS = 15

IMAGES = {}

def loadImages():
    pieces = ['wP','wR','wN','wB','wQ','wK','bP','bR','bN','bB','bQ','bK']
    for piece in pieces:
        IMAGES[piece] = p.transform.scale(
            p.image.load("images/" + piece + ".png"),
            (SQ_SIZE, SQ_SIZE)
        )

def main():
    p.init()
    screen = p.display.set_mode((WIDTH, HEIGHT))
    clock = p.time.Clock()

    gs = ChessEngine.GameState()
    validMoves = gs.getValidMoves()
    moveMade = False #flag variable for when a move is made


    loadImages() #only do this once, before the while loop

    running = True
    sqSelected = () #no square is selected, keep track of the last click of the user (tuple: (row, col))
    playerClicks = [] #keep track of player clicks (two tuples: [(6,4), (4,4)])
    while running:
        for e in p.event.get():
            if e.type == p.QUIT:
                running = False

                # mouse handler

            elif e.type == p.MOUSEBUTTONDOWN:
                location = p.mouse.get_pos() #(x,y) location of mouse
                col = location[0] // SQ_SIZE
                row = location[1] // SQ_SIZE

                if sqSelected == (row, col): #user clicked the same square twice
                    sqSelected = () #deselect
                    playerClicks = [] #clear player clicks
                else:
                    sqSelected = (row, col)
                    playerClicks.append(sqSelected) #append for both 1st and 2nd clicks

                if len(playerClicks) == 2: #after 2nd click
                    try:
                        move = ChessEngine.Move(
                            playerClicks[0],
                            playerClicks[1],
                            gs.board
                        )

                        print("MOVE:", move.getChessNotation())
                        if move in validMoves:
                            gs.makeMove(move)
                            moveMade = True

                    except Exception as err:
                        print("Invalid move:", err)

                    playerClicks = []
                    sqSelected = () #reset user clicks

                    # key handler
            elif e.type == p.KEYDOWN:
                if e.key == p.K_z: #undo when 'z' is pressed
                    gs.undoMove() 
                    moveMade = True


        if moveMade:   
            validMoves = gs.getValidMoves()
            moveMade = False         



        drawGameState(screen, gs)
        clock.tick(MAX_FPS)
        p.display.flip()

    p.quit()

def drawGameState(screen, gs):
    drawBoard(screen)
    drawPieces(screen, gs.board)

def drawBoard(screen):
    colors = [p.Color("white"), p.Color("gray")]
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            color = colors[(r + c) % 2]
            p.draw.rect(screen, color, p.Rect(c*SQ_SIZE, r*SQ_SIZE, SQ_SIZE, SQ_SIZE))

def drawPieces(screen, board):
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            piece = board[r][c]
            if piece != "--":
                screen.blit(IMAGES[piece], p.Rect(c*SQ_SIZE, r*SQ_SIZE, SQ_SIZE, SQ_SIZE))

if __name__ == "__main__":
    main()
