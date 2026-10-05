"""Tag the user's mistakes with tactical motifs, from data already stored by analyze.

For an error at ply p we know the position, the move played, the engine's best move, and
(from row p+1) the engine's best reply for the opponent. The engine having chosen a move is
what makes a motif real: we only ask *what kind* of move it was, never whether it works.

- missed_*  : the user's best move was a mate / fork / winning capture, and they didn't play it
- allowed_* / hung_piece / back_rank : the opponent's best reply exploits the move played

Mates up to MAX_MATE moves come from the engine's stored mate distances (moves.mate), so a
missed mate is tagged even when the move kept a winning eval and wasn't classified an error.
"""
import chess

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9,
          chess.KING: 100}
MIN_PIECE_VALUE = 3   # "hanging" and "winning capture" are about pieces, not pawns

MAX_MATE = 3          # longest forced mate tagged, in moves


def _captured_value(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return VALUES[chess.PAWN]
    victim = board.piece_at(move.to_square)
    return VALUES[victim.piece_type] if victim else 0


def wins_material(board: chess.Board, move: chess.Move) -> bool:
    """A capture of a piece that is undefended or worth more than the capturer."""
    if not board.is_capture(move) or board.is_en_passant(move):
        return False
    victim = board.piece_at(move.to_square)
    attacker = board.piece_at(move.from_square)
    if VALUES[victim.piece_type] < MIN_PIECE_VALUE:
        return False
    if VALUES[attacker.piece_type] < VALUES[victim.piece_type]:
        return True
    after = board.copy(stack=False)
    after.push(move)
    return not after.attackers(victim.color, move.to_square)


def is_fork(board: chess.Board, move: chess.Move) -> bool:
    """The moved piece then attacks 2+ targets that each matter: the king, a more valuable
    piece, or an undefended piece."""
    after = board.copy(stack=False)
    after.push(move)
    piece = after.piece_at(move.to_square)
    enemy = not piece.color
    targets = 0
    for sq in after.attacks(move.to_square) & after.occupied_co[enemy]:
        target = after.piece_at(sq)
        if (target.piece_type == chess.KING
                or VALUES[target.piece_type] > VALUES[piece.piece_type]
                or (VALUES[target.piece_type] >= MIN_PIECE_VALUE and not after.attackers(enemy, sq))):
            targets += 1
    return targets >= 2


def is_back_rank_mate(board: chess.Board, move: chess.Move) -> bool:
    """Rook/queen mates on the back rank a king boxed in by its own pieces in front of it."""
    piece = board.piece_at(move.from_square)
    victim = not board.turn
    back_rank = 0 if victim == chess.WHITE else 7
    king = board.king(victim)
    if (piece.piece_type not in (chess.ROOK, chess.QUEEN)
            or chess.square_rank(move.to_square) != back_rank
            or king is None or chess.square_rank(king) != back_rank):
        return False
    forward = 1 if victim == chess.WHITE else -1
    file = chess.square_file(king)
    in_front = [chess.square(f, back_rank + forward) for f in (file - 1, file, file + 1) if 0 <= f <= 7]
    return all(board.color_at(sq) == victim for sq in in_front)


def missed_mate(mate_before: int | None, mate_after: int | None, san: str,
                best_san: str | None, has_reply: bool) -> int | None:
    """N if the user had a forced mate in N <= MAX_MATE and their move gave it up.

    Mates are user POV (+N = user mates). None means no mate stored; for games analyzed before
    mates were stored, a '#' best move still reveals a mate in 1.
    """
    n = mate_before if mate_before is not None else (1 if best_san and best_san.endswith("#") else None)
    if not n or n < 1 or n > MAX_MATE or san.endswith("#"):
        return None
    if not has_reply and n > 1:
        return None  # game ended (resign/timeout): can't tell whether the mate was kept
    if mate_after is not None and mate_after > 0:
        return None  # still mating, just by a different or slower route
    return n


def allowed_mate(mate_before: int | None, mate_after: int | None, reply_san: str | None) -> int | None:
    """N if the user's move let the opponent force mate in N <= MAX_MATE."""
    n = -mate_after if mate_after is not None else (1 if reply_san and reply_san.endswith("#") else None)
    if not n or n < 1 or n > MAX_MATE:
        return None
    if mate_before is not None and mate_before < 0:
        return None  # was already being mated
    return n


def _ends_in_back_rank_mate(board: chess.Board, line: list[chess.Move]) -> bool:
    board = board.copy(stack=False)
    for move in line[:-1]:
        board.push(move)
    if not is_back_rank_mate(board, line[-1]):
        return False
    board.push(line[-1])
    return board.is_checkmate()  # a truncated PV may stop short of the mate


def tag_error(fen_before: str, san: str, best_san: str | None, reply_san: str | None,
              mate_before: int | None = None, mate_after: int | None = None,
              reply_mate_pv: str | None = None, is_error: bool = True) -> list[str]:
    """Motifs for one user move.

    reply_san is the engine's best reply after `san` (None if the game ended). mate_before /
    mate_after are user-POV forced-mate distances before and after the move, and reply_mate_pv
    the opponent's mating line (UCI) when mate_after < 0. Mate motifs are tagged on any move;
    the rest only when is_error, since on a good move they'd be sacrifices, not mistakes.
    """
    board = chess.Board(fen_before)
    played = board.parse_san(san)
    tags = []

    n = missed_mate(mate_before, mate_after, san, best_san, reply_san is not None)
    if n:
        tags.append(f"missed_mate_in_{n}")
    if is_error and best_san:
        best = board.parse_san(best_san)
        if best != played:
            if wins_material(board, best):
                tags.append("missed_capture")
            if is_fork(board, best):
                tags.append("missed_fork")

    if reply_san:
        gained = _captured_value(board, played)
        board.push(played)
        n = allowed_mate(mate_before, mate_after, reply_san)
        if n:
            tags.append(f"allowed_mate_in_{n}")
            line = ([chess.Move.from_uci(u) for u in reply_mate_pv.split()] if reply_mate_pv
                    else [board.parse_san(reply_san)])
            if _ends_in_back_rank_mate(board, line):
                tags.append("back_rank")
        if is_error:
            reply = board.parse_san(reply_san)
            # Losing a piece worth no more than the one just captured is a trade, not a hung piece.
            if wins_material(board, reply) and _captured_value(board, reply) > gained:
                tags.append("hung_piece")
            if is_fork(board, reply):
                tags.append("allowed_fork")
    return tags
