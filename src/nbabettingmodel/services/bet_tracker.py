"""Track logged bets and their outcomes in nba_betting.db.

Data flow
---------
1. sqlite3 -> a `bet_history` table alongside Player_Stats: one row per bet a
   user has actually logged. This table has nothing to do with the model or
   backtest pipeline -- it's a plain bet ledger the PnL tab reads and writes,
   independent of everything else in this project.

2. Helper functions handle the whole lifecycle:
     * log_bet() / log_top_10_picks() record new wagers as 'Pending' -- one
       at a time (the dashboard's manual-entry form) or in bulk (Tab 5's
       "Log Top 10 Picks to Bet Tracker" button, straight from the EV board).
     * auto_grade_pending_bets() resolves every Pending bet whose game has
       actually been played, by comparing the real completed box score
       (Player_Stats) against the bet's line and side -- no manual "was this
       a win?" judgment needed once the game is over.
     * update_bet_result() is the shared primitive both the manual "Resolve a
       Pending Bet" form and auto-grading call to actually write a result and
       its dollar pnl.
     * calculate_pnl_stats() aggregates win rate and total PnL over any
       subset of rows (the dashboard calls this once for Top 10 picks, once
       for everything).

Usage:
    uv run python -m nbabettingmodel.services.bet_tracker
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from nbabettingmodel.services.daily_edge_runner import american_odds_to_decimal
from nbabettingmodel.services.player_stats import DB_PATH
from nbabettingmodel.services.player_stats import TABLE_NAME as PLAYER_STATS_TABLE

BET_TABLE_NAME = "bet_history"

VALID_RESULTS = ("Win", "Loss", "Push", "Void", "Pending")

# Maps a bet's `prop` value to how to read it off a completed Player_Stats row.
PROP_TO_STAT_COLS = {
    "PTS": ("PTS",),
    "AST": ("AST",),
    "REB": ("REB",),
    "PRA": ("PTS", "REB", "AST"),
}


# --------------------------------------------------------------------------------------
# Step 1: schema
# --------------------------------------------------------------------------------------

def init_bet_history_table(db_path: Path = DB_PATH, table: str = BET_TABLE_NAME) -> None:
    """Create the bet ledger table if it doesn't already exist, and migrate an
    older copy (created before the `side` column existed) up to the current
    schema.

    SQLite has no native boolean type -- is_top_10 is stored as 0/1 (INTEGER)
    and translated to/from Python bool at the boundary in the functions below.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                player_name TEXT NOT NULL,
                prop TEXT NOT NULL,
                side TEXT NOT NULL DEFAULT 'Over',
                line REAL NOT NULL,
                odds INTEGER NOT NULL,
                wager_amount REAL NOT NULL,
                is_top_10 INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT 'Pending',
                pnl REAL NOT NULL DEFAULT 0.0
            )
            """
        )

        existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "side" not in existing_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN side TEXT NOT NULL DEFAULT 'Over'")

        conn.commit()


# --------------------------------------------------------------------------------------
# Step 2: log + resolve bets
# --------------------------------------------------------------------------------------

def log_bet(
    date: str,
    player_name: str,
    prop: str,
    side: str,
    line: float,
    odds: int,
    wager_amount: float,
    is_top_10: bool = False,
    db_path: Path = DB_PATH,
    table: str = BET_TABLE_NAME,
) -> int:
    """Record a new bet as 'Pending' (pnl starts at 0.0 until resolved). Returns the new row's id.

    `side` ('Over' or 'Under') is required, not just cosmetic: auto_grade_pending_bets()
    needs it to know which direction of the line counts as a win.
    """
    init_bet_history_table(db_path, table)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            f"""
            INSERT INTO {table}
                (date, player_name, prop, side, line, odds, wager_amount, is_top_10, result, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', 0.0)
            """,
            (date, player_name, prop, side, line, odds, wager_amount, int(is_top_10)),
        )
        conn.commit()
        return cursor.lastrowid


def log_top_10_picks(
    top_10_df: pd.DataFrame,
    game_date: str,
    db_path: Path = DB_PATH,
    table: str = BET_TABLE_NAME,
) -> int:
    """Bulk-log every row of a Top 10 picks DataFrame (as built by dashboard.py's
    Top 10 & Staking tab) into the bet ledger, all as is_top_10=1 and
    result='Pending'. Returns the number of rows inserted.

    Expects columns: Player, Prop, Side, Line, Odds, Recommended Wager ($) --
    the same shape that tab's own display table already uses, so the caller
    doesn't need to reshape anything first. EV% isn't stored: the ledger
    tracks bets actually placed and their real outcomes, not the model's
    confidence at the moment they were logged.
    """
    init_bet_history_table(db_path, table)
    if top_10_df.empty:
        return 0

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            f"""
            INSERT INTO {table}
                (date, player_name, prop, side, line, odds, wager_amount, is_top_10, result, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'Pending', 0.0)
            """,
            [
                (
                    game_date,
                    row["Player"],
                    row["Prop"],
                    row["Side"],
                    float(row["Line"]),
                    int(row["Odds"]),
                    float(row["Recommended Wager ($)"]),
                )
                for _, row in top_10_df.iterrows()
            ],
        )
        conn.commit()
    return len(top_10_df)


def _compute_pnl(result: str, odds: int, wager_amount: float) -> float:
    """Dollar profit/loss for one resolved bet.

    A win's pnl is the *profit* alone (not stake + profit) -- this column
    answers "how much richer/poorer did this bet make me," not "how much
    cash came back," which is what calculate_pnl_stats sums for the
    Bankroll Up/Down figure.
    """
    if result == "Win":
        decimal_odds = american_odds_to_decimal(odds)
        return wager_amount * (decimal_odds - 1)
    if result == "Loss":
        return -wager_amount
    # Push (stake refunded), Void (the bet never had a real outcome), or
    # Pending (no outcome yet) -- none of these move the bankroll.
    return 0.0


def update_bet_result(
    bet_id: int, result: str, db_path: Path = DB_PATH, table: str = BET_TABLE_NAME
) -> None:
    """Resolve (or reset) a bet's result and recompute its pnl to match."""
    if result not in VALID_RESULTS:
        raise ValueError(f"result must be one of {VALID_RESULTS}, got {result!r}.")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(f"SELECT odds, wager_amount FROM {table} WHERE id = ?", (bet_id,)).fetchone()
        if row is None:
            raise ValueError(f"No bet found with id={bet_id} in '{table}'.")

        odds, wager_amount = row
        pnl = _compute_pnl(result, odds, wager_amount)

        conn.execute(f"UPDATE {table} SET result = ?, pnl = ? WHERE id = ?", (result, pnl, bet_id))
        conn.commit()


def _team_abbr_for_player_as_of(
    player_name: str, as_of_date: str, player_stats_table: str, conn: sqlite3.Connection
) -> str | None:
    """A player's team, inferred from their most recent game on or before
    `as_of_date`. MATCHUP is always "<TEAM> @/vs. <OPPONENT>" (see
    player_stats.py), so the team is the first token. Returns None if this
    player has no recorded games on or before that date at all.
    """
    row = conn.execute(
        f"SELECT MATCHUP FROM {player_stats_table} WHERE PLAYER_NAME = ? AND GAME_DATE <= ? "
        f"ORDER BY GAME_DATE DESC LIMIT 1",
        (player_name, as_of_date),
    ).fetchone()
    return row[0].split()[0] if row else None


def _team_game_completed(
    team_abbr: str, game_date: str, player_stats_table: str, conn: sqlite3.Connection
) -> bool:
    """True if any player on `team_abbr` has a recorded box-score row for
    `game_date` -- i.e. that team's game has been played and collected,
    regardless of whether one specific player appears in it.
    """
    row = conn.execute(
        f"SELECT 1 FROM {player_stats_table} WHERE GAME_DATE = ? AND MATCHUP LIKE ? LIMIT 1",
        (game_date, f"{team_abbr} %"),
    ).fetchone()
    return row is not None


def auto_grade_pending_bets(
    db_path: Path = DB_PATH,
    table: str = BET_TABLE_NAME,
    player_stats_table: str = PLAYER_STATS_TABLE,
) -> int:
    """Grade every Pending bet whose game has actually been played, by
    comparing the real completed box score against the bet's line and side.
    Returns the number of bets graded (Win/Loss/Push/Void).

    A bet with no matching (player_name, date) row in Player_Stats splits
    into two cases, since a full DNP (inactive/did-not-dress) never gets a
    row in a player's own game log at all -- there's nothing here to tell
    "hasn't played yet" apart from "played and was scratched" except by
    checking whether that player's *team* has a completed game recorded for
    that date at all (via a teammate's row):
      * team's game not yet recorded -> stays Pending (normal, expected).
      * team's game recorded, but this player has no row in it, or is in it
        with 0 minutes -> graded 'Void' (pnl 0.0): the game happened, but
        there was never a real over/under outcome to grade.
    The same "no data to work with" caveat as before still applies to a
    manually-typed player_name that doesn't exactly match Player_Stats
    (accents, nicknames, typos): grading can't recover from that either, and
    it's indistinguishable here from a player whose team hasn't played yet,
    so it also just stays Pending for a human to resolve by hand.
    """
    with sqlite3.connect(db_path) as conn:
        pending = pd.read_sql(f"SELECT * FROM {table} WHERE result = 'Pending'", conn)

    graded_count = 0
    for row in pending.itertuples():
        with sqlite3.connect(db_path) as conn:
            box_score = conn.execute(
                f"SELECT PTS, REB, AST, MIN FROM {player_stats_table} "
                f"WHERE PLAYER_NAME = ? AND GAME_DATE = ?",
                (row.player_name, row.date),
            ).fetchone()

            if box_score is None:
                team_abbr = _team_abbr_for_player_as_of(
                    row.player_name, row.date, player_stats_table, conn
                )
                team_played = team_abbr is not None and _team_game_completed(
                    team_abbr, row.date, player_stats_table, conn
                )
                if not team_played:
                    continue  # game hasn't happened yet (or name didn't match) -- stays Pending

                update_bet_result(row.id, "Void", db_path, table)
                graded_count += 1
                continue

            pts, reb, ast, minutes = box_score

        if minutes is not None and minutes <= 0:
            update_bet_result(row.id, "Void", db_path, table)
            graded_count += 1
            continue

        stat_cols = PROP_TO_STAT_COLS.get(row.prop)
        if stat_cols is None:
            continue  # unrecognized prop; nothing we know how to grade it against

        actual_by_col = {"PTS": pts, "REB": reb, "AST": ast}
        actual_value = sum(actual_by_col[col] for col in stat_cols)

        if actual_value == row.line:
            result = "Push"
        elif row.side == "Over":
            result = "Win" if actual_value > row.line else "Loss"
        else:
            result = "Win" if actual_value < row.line else "Loss"

        update_bet_result(row.id, result, db_path, table)
        graded_count += 1

    return graded_count


# --------------------------------------------------------------------------------------
# Step 3: read + aggregate
# --------------------------------------------------------------------------------------

def load_bet_history(db_path: Path = DB_PATH, table: str = BET_TABLE_NAME) -> pd.DataFrame:
    """The full bet ledger, most recently logged first.

    Safe to call before any bet has ever been logged -- it creates the table
    if needed and returns an empty (but correctly-columned) DataFrame rather
    than erroring on a missing table.
    """
    init_bet_history_table(db_path, table)
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql(f"SELECT * FROM {table} ORDER BY id DESC", conn)
    df["is_top_10"] = df["is_top_10"].astype(bool)
    return df


def calculate_pnl_stats(bets_df: pd.DataFrame) -> dict:
    """Win rate and total PnL over `bets_df` -- already filtered by the caller
    to whatever subset matters (Top 10 picks only, everything, etc.).

    Win rate is computed over *decided* bets only (Win + Loss). Pending bets
    haven't happened yet; Push and Void bets are both graded outcomes that
    are neither a win nor a loss (a push refunds the stake, a void bet never
    had a real outcome to grade at all) -- counting either in the
    denominator would dilute the rate toward a result that didn't happen.
    """
    decided = bets_df[bets_df["result"].isin(["Win", "Loss"])]
    wins = int((decided["result"] == "Win").sum())
    losses = int((decided["result"] == "Loss").sum())
    pushes = int((bets_df["result"] == "Push").sum())
    voids = int((bets_df["result"] == "Void").sum())
    pending = int((bets_df["result"] == "Pending").sum())

    win_pct = (wins / len(decided) * 100) if len(decided) else 0.0
    total_pnl = float(bets_df["pnl"].sum())

    # ROI over *settled* wagers (Win/Loss/Push/Void -- money that was actually
    # staked and resolved one way or another), not Pending ones that haven't
    # risked an outcome yet.
    settled = bets_df[bets_df["result"] != "Pending"]
    total_wagered = float(settled["wager_amount"].sum())
    roi_pct = (total_pnl / total_wagered * 100) if total_wagered else 0.0

    return {
        "total_bets": len(bets_df),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": voids,
        "pending": pending,
        "win_pct": win_pct,
        "total_pnl": total_pnl,
        "total_wagered": total_wagered,
        "roi_pct": roi_pct,
    }


if __name__ == "__main__":
    init_bet_history_table()
    history = load_bet_history()
    print(f"{len(history)} bets logged.")
    print(calculate_pnl_stats(history))
