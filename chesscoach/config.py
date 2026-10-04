import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CHESSCOACH_DATA", PROJECT_ROOT / "data"))
DB_PATH = DATA_DIR / "chesscoach.db"

# Chess.com asks API clients to identify themselves with contact info.
USER_AGENT = os.environ.get(
    "CHESSCOACH_USER_AGENT",
    "ChessCoach/0.1 (+https://github.com/NolanKisser/ChessCoach)",
)


def stockfish_path() -> str:
    """Find a Stockfish binary: $STOCKFISH_PATH, then PATH, then ./tools."""
    env = os.environ.get("STOCKFISH_PATH")
    if env:
        return env
    found = shutil.which("stockfish")
    if found:
        return found
    for candidate in sorted((PROJECT_ROOT / "tools").rglob("stockfish*.exe")) + sorted(
        (PROJECT_ROOT / "tools").rglob("stockfish")
    ):
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "Stockfish not found. Set STOCKFISH_PATH or place the binary under tools/."
    )
