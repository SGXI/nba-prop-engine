"""Backfill PRA_AVG_5 / PRA_AVG_10 / PRA_AVG_SEASON into the already-populated
Player_Stats table.

Unlike add_usage.py, this needs no live API calls. PRA (= PTS + REB + AST) has
been stored in Player_Stats since player_stats.py's clean_game_log() started
computing it, so every input this backfill needs already lives in the table --
player_stats.py has since been updated to roll PRA the same way as every other
stat (see ROLLING_STAT_COLS) for any newly pulled player, but the roster
already sitting in nba_betting.db needs this one-time catch-up.

Data flow
---------
1. sqlite3 -> ALTER TABLE Player_Stats ADD COLUMN PRA_AVG_5/10/SEASON REAL,
   only for whichever of the three don't already exist.
2. sqlite3 -> read PLAYER_ID, GAME_ID, GAME_DATE, PRA for every row.
3. player_stats.add_rolling_averages() -> the exact same `.shift(1)`-lagged
   rolling-average logic used for every other stat in this table, reused here
   rather than reimplemented, so PRA's rolling averages have identical
   semantics to PTS_AVG_5, REB_AVG_10, etc.
4. sqlite3 -> UPDATE Player_Stats SET PRA_AVG_5 = ?, PRA_AVG_10 = ?,
   PRA_AVG_SEASON = ? WHERE PLAYER_ID = ? AND GAME_ID = ?, one UPDATE per row.

Usage:
    uv run python -m nbabettingmodel.services.add_pra
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from nbabettingmodel.services.player_stats import DB_PATH, TABLE_NAME, add_rolling_averages

PRA_ROLLING_COLS = ["PRA_AVG_5", "PRA_AVG_10", "PRA_AVG_SEASON"]


def ensure_pra_rolling_columns(conn: sqlite3.Connection, table: str = TABLE_NAME) -> None:
    """Add whichever of the three PRA rolling columns aren't already there."""
    existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    missing = [col for col in PRA_ROLLING_COLS if col not in existing_cols]

    if not missing:
        print(f"'{table}' already has all PRA rolling columns; continuing.")
        return

    for col in missing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL")
    conn.commit()
    print(f"Added columns to '{table}': {', '.join(missing)}.")


def backfill_pra_rolling_averages(db_path: Path = DB_PATH, table: str = TABLE_NAME) -> None:
    with sqlite3.connect(db_path) as conn:
        ensure_pra_rolling_columns(conn, table)

        df = pd.read_sql(
            f"SELECT PLAYER_ID, GAME_ID, GAME_DATE, PRA FROM {table}",
            conn,
            parse_dates=["GAME_DATE"],
        )
        print(f"Loaded {len(df)} rows from '{table}'.")

        featured = add_rolling_averages(df, stat_cols=["PRA"])

        conn.executemany(
            f"""
            UPDATE {table}
            SET PRA_AVG_5 = ?, PRA_AVG_10 = ?, PRA_AVG_SEASON = ?
            WHERE PLAYER_ID = ? AND GAME_ID = ?
            """,
            [
                (
                    row.PRA_AVG_5,
                    row.PRA_AVG_10,
                    row.PRA_AVG_SEASON,
                    int(row.PLAYER_ID),
                    row.GAME_ID,
                )
                for row in featured.itertuples(index=False)
            ],
        )
        conn.commit()

    print(f"Backfilled PRA rolling averages for {len(featured)} rows.")


if __name__ == "__main__":
    backfill_pra_rolling_averages()
