"""Backfill USG_PCT into Player_Stats without re-scraping every player's game log.

Data flow
---------
1. sqlite3 -> ALTER TABLE Player_Stats ADD COLUMN USG_PCT REAL, only if that
   column doesn't already exist (checked via PRAGMA table_info -- SQLite raises
   an error if you ALTER TABLE ADD COLUMN a name that's already there, so a
   naive re-run would crash without this guard).

2. sqlite3 -> SELECT DISTINCT GAME_ID already in Player_Stats. We don't need a
   fresh player-by-player pull for this: the games we care about are exactly the
   ones player_stats.py already collected, one row per player who played in each.

3. `nba_api.stats.endpoints.BoxScoreAdvancedV3` -> one live call per GAME_ID.
   This is the inverse shape of PlayerGameLog (one player, many games): here it's
   one game, every player who appeared in it, with usagePercentage (and other
   advanced box-score metrics we don't need) for each. So one call backfills
   every row in Player_Stats sharing that GAME_ID, instead of one call per row.
   V3, not V2: `BoxScoreAdvancedV2` -- the endpoint originally asked for -- is
   currently broken. Live-tested it directly against stats.nba.com: it returns
   an empty response body for every GAME_ID tried, confirmed both through
   nba_api and a raw HTTP request. NBA appears to have deprecated it in favor of
   V3, which returns the same USG_PCT/`usagePercentage` figure under camelCase
   field names (`personId`, `usagePercentage`) instead of V2's
   `PLAYER_ID`/`USG_PCT`.

4. sqlite3 -> UPDATE Player_Stats SET USG_PCT = ? WHERE GAME_ID = ? AND
   PLAYER_ID = ?, one UPDATE per player in that box score, committed once per
   game so an interrupted run doesn't lose games it already finished.

Usage:
    uv run python -m nbabettingmodel.services.add_usage
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from nba_api.stats.endpoints import boxscoreadvancedv3

from nbabettingmodel.services.player_stats import DB_PATH, REQUEST_TIMEOUT, TABLE_NAME

# Pause between BoxScoreAdvancedV3 calls. stats.nba.com will start throwing
# timeouts/429s under rapid sequential hits, same rationale as the other pulls.
REQUEST_DELAY = 0.6


# --------------------------------------------------------------------------------------
# Step 1: schema migration
# --------------------------------------------------------------------------------------

def ensure_usg_pct_column(conn: sqlite3.Connection, table: str = TABLE_NAME) -> None:
    """Add a USG_PCT REAL column to `table` if it isn't already there."""
    existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if "USG_PCT" in existing_cols:
        print(f"'{table}' already has a USG_PCT column; continuing.")
        return

    conn.execute(f"ALTER TABLE {table} ADD COLUMN USG_PCT REAL")
    conn.commit()
    print(f"Added USG_PCT column to '{table}'.")


# --------------------------------------------------------------------------------------
# Step 2: games to backfill
# --------------------------------------------------------------------------------------

def get_distinct_game_ids(conn: sqlite3.Connection, table: str = TABLE_NAME) -> list[str]:
    rows = conn.execute(f"SELECT DISTINCT GAME_ID FROM {table} ORDER BY GAME_ID").fetchall()
    return [row[0] for row in rows]


def game_already_filled(conn: sqlite3.Connection, game_id: str, table: str = TABLE_NAME) -> bool:
    """True if every row for this GAME_ID already has USG_PCT set.

    Lets a re-run after an interruption resume where it left off instead of
    re-fetching box scores it doesn't need -- cheap, since it's a local query
    with no API call involved.
    """
    (missing,) = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE GAME_ID = ? AND USG_PCT IS NULL", (game_id,)
    ).fetchone()
    return missing == 0


# --------------------------------------------------------------------------------------
# Step 3: fetch one game's usage rates
# --------------------------------------------------------------------------------------

def fetch_usage_by_player(game_id: str) -> dict[int, float]:
    """One BoxScoreAdvancedV3 call: usage rate for every player who appeared in `game_id`.

    V3's PlayerStats result set uses camelCase field names (`personId`,
    `usagePercentage`) rather than V2's `PLAYER_ID`/`USG_PCT` -- mapped back to
    plain floats here so the rest of this script doesn't need to know about it.
    """
    endpoint = boxscoreadvancedv3.BoxScoreAdvancedV3(game_id=game_id, timeout=REQUEST_TIMEOUT)
    df = endpoint.player_stats.get_data_frame()
    return dict(zip(df["personId"], df["usagePercentage"]))


# --------------------------------------------------------------------------------------
# Step 4: backfill
# --------------------------------------------------------------------------------------

def backfill_usage(
    db_path: Path = DB_PATH,
    table: str = TABLE_NAME,
    request_delay: float = REQUEST_DELAY,
) -> None:
    with sqlite3.connect(db_path) as conn:
        ensure_usg_pct_column(conn, table)

        game_ids = get_distinct_game_ids(conn, table)
        total = len(game_ids)
        print(f"Found {total} distinct games in '{table}'.")

        for i, game_id in enumerate(game_ids, start=1):
            if game_already_filled(conn, game_id, table):
                print(f"[{i}/{total}] GAME_ID {game_id}: already filled, skipping.")
                continue

            print(f"[{i}/{total}] GAME_ID {game_id}: fetching advanced box score...")
            try:
                usage_by_player = fetch_usage_by_player(game_id)
            except Exception as exc:
                usage_by_player = None
                print(f"  Failed: {exc!r}")

            # Sleep once per API call attempt, success or failure -- rate limiting
            # cares about requests actually sent, not about outcomes.
            time.sleep(request_delay)

            if not usage_by_player:
                continue

            conn.executemany(
                f"UPDATE {table} SET USG_PCT = ? WHERE GAME_ID = ? AND PLAYER_ID = ?",
                [
                    (usg_pct, game_id, int(player_id))
                    for player_id, usg_pct in usage_by_player.items()
                ],
            )
            conn.commit()

    print("Done.")


if __name__ == "__main__":
    backfill_usage()
