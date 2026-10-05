"""Run Stockfish over every position in a game and classify each move."""
import io
import math
from typing import NamedTuple, Optional

import chess
import chess.engine
import chess.pgn

EVAL_CLIP = 1000          # centipawns; mate scores and huge evals are clipped to this
MATE_CP = 100_000

# Win% drop thresholds (0-100 scale), matching Lichess's 0.1/0.2/0.3 winning-chance deltas.
THRESHOLDS = (("blunder", 15.0), ("mistake", 10.0), ("inaccuracy", 5.0))

PIECE_VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
ENDGAME_MATERIAL = 26     # non-pawn material for both sides combined (start = 62)
OPENING_LAST_MOVE = 12


def win_pct(cp: int) -> float:
    """Lichess's centipawn -> win% curve."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def classify(win_drop: float) -> Optional[str]:
    for label, threshold in THRESHOLDS:
        if win_drop >= threshold:
            return label
    return None


def game_phase(board: chess.Board) -> str:
    material = sum(
        len(board.pieces(piece, color)) * value
        for piece, value in PIECE_VALUES.items()
        for color in chess.COLORS
    )
    if material <= ENDGAME_MATERIAL:
        return "endgame"
    if board.fullmove_number <= OPENING_LAST_MOVE:
        return "opening"
    return "middlegame"


def _clip(cp: int) -> int:
    return max(-EVAL_CLIP, min(EVAL_CLIP, cp))


class Eval(NamedTuple):
    cp: int                          # white POV, mate = +-MATE_CP
    best: Optional[chess.Move]
    mate: Optional[int] = None       # white POV forced mate distance in moves, if any
    mate_pv: Optional[str] = None    # the mating line in UCI, when mate is set


def mate_line(board: chess.Board, pv: list[chess.Move]) -> str:
    """The PV as UCI, cut off at the checkmating move."""
    board = board.copy(stack=False)
    line = []
    for move in pv:
        line.append(move.uci())
        board.push(move)
        if board.is_checkmate():
            break
    return " ".join(line)


def evaluate(engine: chess.engine.SimpleEngine, board: chess.Board,
             limit: chess.engine.Limit) -> Eval:
    """Evaluate a position: white-POV centipawns, best move, and any forced mate."""
    if board.is_checkmate():
        return Eval(-MATE_CP if board.turn == chess.WHITE else MATE_CP, None)
    if board.is_game_over(claim_draw=False):
        return Eval(0, None)
    info = engine.analyse(board, limit)
    score = info["score"].white()
    pv = info.get("pv") or []
    mate = score.mate()
    return Eval(score.score(mate_score=MATE_CP), pv[0] if pv else None,
                mate, mate_line(board, pv) if mate else None)


def find_mates(engine: chess.engine.SimpleEngine, positions: list, depth: int) -> dict[int, tuple[int, str]]:
    """Re-check (ply, fen_before) positions for forced mates: ply -> (mate, mate_pv)."""
    limit = chess.engine.Limit(depth=depth)
    found = {}
    for ply, fen in positions:
        e = evaluate(engine, chess.Board(fen), limit)
        if e.mate:
            found[ply] = (e.mate, e.mate_pv)
    return found


def analyze_game(engine: chess.engine.SimpleEngine, pgn: str, user_color: str,
                 depth: int = 14) -> list[dict]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return []
    limit = chess.engine.Limit(depth=depth)
    user = chess.WHITE if user_color == "white" else chess.BLACK

    board = game.board()
    nodes = list(game.mainline())
    results, snapshots = [], []
    for node in nodes:
        results.append(evaluate(engine, board, limit))
        snapshots.append((board.fen(), board.turn, board.san(node.move), game_phase(board)))
        board.push(node.move)
    evals = [r.cp for r in results] + [evaluate(engine, board, limit).cp]

    moves = []
    for i, node in enumerate(nodes):
        fen, turn, san, phase = snapshots[i]
        sign = 1 if turn == chess.WHITE else -1
        before, after = _clip(evals[i]) * sign, _clip(evals[i + 1]) * sign
        best = results[i].best
        if best is not None and best == node.move:
            cp_loss, drop = 0, 0.0
        else:
            cp_loss = max(0, before - after)
            drop = max(0.0, win_pct(before) - win_pct(after))
        best_san = chess.Board(fen).san(best) if best is not None else None
        moves.append({
            "ply": i + 1,
            "color": "white" if turn == chess.WHITE else "black",
            "is_user": int(turn == user),
            "san": san,
            "best_san": best_san,
            "fen_before": fen,
            "eval_before": _clip(evals[i]),
            "eval_after": _clip(evals[i + 1]),
            "cp_loss": cp_loss,
            "win_drop": round(drop, 2),
            "classification": classify(drop),
            "phase": phase,
            "clock": node.clock(),
            "mate": results[i].mate,
            "mate_pv": results[i].mate_pv,
        })
    return moves
