"""Download games from Chess.com (PubAPI) and Lichess and normalize them."""
import io
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

import chess.pgn
import requests

from .config import USER_AGENT

CHESSCOM_ARCHIVES = "https://api.chess.com/pub/player/{user}/games/archives"
LICHESS_GAMES = "https://lichess.org/api/games/user/{user}"

CHESSCOM_DRAWS = {
    "agreed", "repetition", "stalemate", "insufficient",
    "50move", "timevsinsufficient",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET with backoff on 429. Both APIs want requests made serially."""
    for attempt in range(5):
        resp = session.get(url, timeout=60, **kwargs)
        if resp.status_code == 429:
            time.sleep(60 if "lichess" in url else 2 ** attempt)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def _opening_from_eco_url(eco_url: Optional[str]) -> Optional[str]:
    # e.g. https://www.chess.com/openings/Caro-Kann-Defense-Advance-Variation-3...Bf5
    if not eco_url:
        return None
    slug = eco_url.rstrip("/").rsplit("/", 1)[-1]
    # Drop trailing move sequences like "-3...Bf5" to keep the family/variation name.
    words = []
    for part in slug.split("-"):
        if part[:1].isdigit():
            break
        words.append(part)
    return " ".join(words) or slug


def normalize_chesscom(g: dict, username: str) -> Optional[dict]:
    if g.get("rules") != "chess" or "pgn" not in g:
        return None
    user = username.lower()
    if g["white"]["username"].lower() == user:
        color, me, opp = "white", g["white"], g["black"]
    elif g["black"]["username"].lower() == user:
        color, me, opp = "black", g["black"], g["white"]
    else:
        return None

    if me["result"] == "win":
        result = "win"
    elif me["result"] in CHESSCOM_DRAWS:
        result = "draw"
    else:
        result = "loss"

    headers = chess.pgn.read_headers(io.StringIO(g["pgn"]))
    return {
        "id": "chesscom:" + g["url"].rstrip("/").rsplit("/", 1)[-1],
        "source": "chesscom",
        "url": g["url"],
        "pgn": g["pgn"],
        "user_color": color,
        "user_rating": me.get("rating"),
        "opponent": opp["username"],
        "opponent_rating": opp.get("rating"),
        "result": result,
        "time_class": g.get("time_class"),
        "time_control": g.get("time_control"),
        "eco": headers.get("ECO") if headers else None,
        "opening": _opening_from_eco_url(headers.get("ECOUrl") if headers else g.get("eco")),
        "played_at": g.get("end_time"),
    }


def fetch_chesscom(username: str, months: Optional[int] = None) -> Iterator[dict]:
    s = _session()
    archives = _get(s, CHESSCOM_ARCHIVES.format(user=username.lower())).json()["archives"]
    if months:
        archives = archives[-months:]
    for url in archives:
        for g in _get(s, url).json().get("games", []):
            game = normalize_chesscom(g, username)
            if game:
                yield game


def normalize_lichess(g: dict, username: str) -> Optional[dict]:
    if g.get("variant") != "standard" or not g.get("pgn"):
        return None
    if g.get("status") in {"aborted", "noStart", "unknownFinish"}:
        return None
    players = g["players"]
    user = username.lower()

    def name(side):
        return (players[side].get("user") or {}).get("name", "")

    if name("white").lower() == user:
        color, opp_color = "white", "black"
    elif name("black").lower() == user:
        color, opp_color = "black", "white"
    else:
        return None

    winner = g.get("winner")
    result = "draw" if winner is None else ("win" if winner == color else "loss")
    opening = g.get("opening") or {}
    opp = players[opp_color]
    return {
        "id": "lichess:" + g["id"],
        "source": "lichess",
        "url": f"https://lichess.org/{g['id']}",
        "pgn": g["pgn"],
        "user_color": color,
        "user_rating": players[color].get("rating"),
        "opponent": name(opp_color) or ("Stockfish AI" if opp.get("aiLevel") else "anonymous"),
        "opponent_rating": opp.get("rating"),
        "result": result,
        "time_class": g.get("speed"),
        "time_control": None,
        "eco": opening.get("eco"),
        "opening": opening.get("name", "").split(":")[0] or None,
        "played_at": int(g["createdAt"] / 1000) if g.get("createdAt") else None,
    }


def fetch_lichess(username: str, months: Optional[int] = None,
                  max_games: Optional[int] = None) -> Iterator[dict]:
    s = _session()
    params = {"pgnInJson": "true", "opening": "true", "clocks": "true"}
    if max_games:
        params["max"] = max_games
    if months:
        since = datetime.now(timezone.utc) - timedelta(days=30 * months)
        params["since"] = int(since.timestamp() * 1000)
    resp = _get(s, LICHESS_GAMES.format(user=username), params=params,
                headers={"Accept": "application/x-ndjson"}, stream=True)
    for line in resp.iter_lines():
        if not line:
            continue
        game = normalize_lichess(json.loads(line), username)
        if game:
            yield game
