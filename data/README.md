# Local opening book

`opening_book.json` is an optional, local JSON book. Each key is the first four
FEN fields (piece placement, side to move, castling rights, and en-passant
target). Its value is an ordered list of UCI moves. The engine checks each move
against the current legal-move set and uses the first legal entry; missing or
invalid entries fall through to the custom search.

To extend it, add a position key and one or more legal UCI moves, then load it
with `LocalOpeningBook.from_json("data/opening_book.json")`, pass it to
`CustomChessEngine(opening_book=book)`, and enable it with
`SearchFeatures(opening_book=True)`. The book is opt-in and has no network or
runtime dependency.
