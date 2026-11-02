"""Reproducible fixed-depth benchmarks for the local custom chess engine.

Example: python benchmarks/benchmark_engine.py --depth 4 --compare benchmarks/p5_baseline.json
Omit --time-limit for fixed-depth runs. Benchmark values are machine-dependent;
the checked-in baseline records one local run for before/after comparison.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import chess

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from chess_ai import CustomChessEngine, SearchLimits
from chess_ai import SearchFeatures
from opening_book import LocalOpeningBook


POSITIONS_PATH = Path(__file__).with_name("positions.json")


def run_benchmarks(
    depth: int = 4,
    time_limit: float | None = None,
    features: SearchFeatures | None = None,
    use_opening_book: bool = False,
) -> dict:
    features = features or SearchFeatures()
    positions = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
    results = {}
    for name, fen in positions.items():
        board = chess.Board(fen)
        book = None
        if use_opening_book:
            book = LocalOpeningBook.from_json(PROJECT_ROOT / "data" / "opening_book.json")
        result = CustomChessEngine(opening_book=book).search(
            board, SearchLimits(depth, time_limit, features=features)
        )
        results[name] = {
            "nodes": result.nodes,
            "nps": round(result.nodes / result.elapsed_seconds, 1) if result.elapsed_seconds else 0.0,
            "depth": result.depth,
            "elapsed_seconds": round(result.elapsed_seconds, 4),
            "score": result.score,
            "best_move": result.best_move.uci() if result.best_move else None,
            "diagnostics": result.diagnostics.__dict__,
        }
    return {
        "conditions": {"max_depth": depth, "time_limit_seconds": time_limit},
        "feature_configuration": features.__dict__,
        "positions": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--time-limit", type=float, default=None,
                        help="optional seconds per position; omit for fixed-depth runs")
    parser.add_argument("--compare", type=Path, help="JSON benchmark output to compare against")
    for feature in ("pvs", "aspiration-windows", "lmr", "null-move-pruning", "futility-pruning"):
        parser.add_argument(f"--disable-{feature}", action="store_true")
    parser.add_argument("--use-opening-book", action="store_true")
    args = parser.parse_args()
    features = SearchFeatures(
        pvs=not args.disable_pvs,
        aspiration_windows=not args.disable_aspiration_windows,
        lmr=not args.disable_lmr,
        null_move_pruning=not args.disable_null_move_pruning,
        futility_pruning=not args.disable_futility_pruning,
        opening_book=args.use_opening_book,
    )
    current = run_benchmarks(args.depth, args.time_limit, features, args.use_opening_book)
    print(json.dumps(current, indent=2))
    if args.compare:
        reference = json.loads(args.compare.read_text(encoding="utf-8"))
        print("\nComparison (node counts, elapsed time, NPS):")
        for name, value in current["positions"].items():
            old = reference.get("positions", {}).get(name)
            if not old:
                continue
            print(f"{name}: nodes {old['nodes']} -> {value['nodes']}; "
                  f"time {old['elapsed_seconds']:.4f}s -> {value['elapsed_seconds']:.4f}s; "
                  f"NPS {old['nps']:.1f} -> {value['nps']:.1f}; "
                  f"depth {value['depth']}; move {value['best_move']}")


if __name__ == "__main__":
    main()
