"""Standalone morning data-sync and model-retraining pipeline.

Data flow
---------
1. sync_recent_games(days_back) -> for each of the last `days_back` calendar
   dates, ScoreboardV2 lists which teams had a *completed* ("Final") game.
   Only tracked players whose most recently known team is in that set get
   re-pulled, via player_stats.build_player_stats() -- the same per-player
   PlayerGameLog pipeline the original historical backfill used. Re-pulling a
   player's full season (rather than trying to insert only the new games
   directly) is deliberate: build_player_stats()'s own save_to_sqlite() is
   already idempotent per (PLAYER_ID, SEASON) -- it deletes that player's
   existing rows for the season and reinserts the fresh full pull -- so this
   naturally avoids duplicates while also refreshing that player's rolling
   averages and opponent-context features for their whole season, using
   machinery that's already proven correct rather than a new incremental-
   insert path. add_usage.py's backfill_usage() then fills USG_PCT for
   whatever just got inserted (build_player_stats() never populates it --
   that column only ever comes from BoxScoreAdvancedV3, not PlayerGameLog).

   Scope note: this only refreshes players *already* in Player_Stats. A
   brand-new player who has never been tracked before (a rookie debut, a new
   signing) won't be picked up here, since there's no existing row to infer
   "their team" from without a live call per active player -- that's what
   player_stats.py's full build_league_stats() roster scan is for, a much
   heavier operation not meant for a quick daily sync.

2. retrain_models() -> add_pra.py's backfill_pra_rolling_averages() re-derives
   PRA_AVG_5/10/SEASON across the whole table (cheap and purely local -- no
   API calls, since PRA is already a stored column). Nothing else needs an
   explicit "recompute rolling features" pass: PTS/REB/AST/efficiency rolling
   averages and opponent context for any newly-synced player were already
   freshly recomputed by sync_recent_games()'s re-pull above (or are simply
   unchanged for a player whose team didn't play), and USG_AVG_5/10 is
   computed on the fly at training time by each predict_*.py script directly
   from the raw USG_PCT column, never stored as its own column. Then
   edge_detector.train_models() refits one SGDRegressor per prop -- always
   unconditionally, unlike the load_or_train_models() fast path everything
   else in the project now uses -- and edge_detector.save_models() persists
   each one to disk via joblib, the same save routine load_or_train_models()
   itself falls back to, so both paths write models/ consistently.

3. run_morning_pipeline() -> sync_recent_games(), then
   bet_tracker.auto_grade_pending_bets() (grading against the box scores just
   synced in step 1), then retrain_models(), then live_odds_client's
   fetch_live_odds() to prime live_odds_cache.json for the day -- in that
   order, since each step's output is what the next one needs fresh.

Usage:
    uv run python -m nbabettingmodel.services.data_sync
    uv run python -m nbabettingmodel.services.data_sync --days-back 5
"""

from __future__ import annotations

import argparse
import sqlite3
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import scoreboardv2
from nba_api.stats.static import teams as static_teams

from nbabettingmodel.services import add_pra, add_usage, edge_detector, player_stats
from nbabettingmodel.services.bet_tracker import auto_grade_pending_bets
from nbabettingmodel.services.live_odds_client import fetch_live_odds

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

DB_PATH = player_stats.DB_PATH
TABLE_NAME = player_stats.TABLE_NAME
REQUEST_TIMEOUT = player_stats.REQUEST_TIMEOUT

# Single source of truth for where models live, defined in edge_detector.py
# (load_or_train_models() / save_models()) and reused here so both the
# morning retrain and the fast-load path agree on the same directory.
MODELS_DIR = edge_detector.MODELS_DIR

DEFAULT_DAYS_BACK = 3


# --------------------------------------------------------------------------------------
# Step 1: sync recent games
# --------------------------------------------------------------------------------------

def _season_for_date(target_date: date) -> str:
    """The NBA season `target_date` falls within, e.g. '2026-27'.

    Different from player_stats.most_recent_completed_season(): that helper
    deliberately looks one season *behind* during the offseason (so an
    initial historical backfill doesn't target a season with zero games yet).
    Syncing recent games needs the opposite -- the season actually in
    progress *right now*, even if it started only days ago.
    """
    start_year = target_date.year if target_date.month >= 7 else target_date.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _team_id_to_abbr() -> dict[int, str]:
    """Offline TEAM_ID -> abbreviation lookup (static, no API call)."""
    return {t["id"]: t["abbreviation"] for t in static_teams.get_teams()}


def _completed_team_abbrs_for_date(game_date: str) -> set[str]:
    """Every team abbreviation with a completed ("Final") game on `game_date`.

    Same ScoreboardV2 GameHeader source as daily_edge_runner.get_todays_matchups(),
    but filtered to finished games only -- an in-progress or postponed game
    has no box score to sync yet.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        endpoint = scoreboardv2.ScoreboardV2(game_date=game_date, timeout=REQUEST_TIMEOUT)
    games = endpoint.game_header.get_data_frame()

    finals = games[games["GAME_STATUS_TEXT"] == "Final"]
    team_id_to_abbr = _team_id_to_abbr()

    abbrs: set[str] = set()
    for _, game in finals.iterrows():
        abbrs.add(team_id_to_abbr[int(game["HOME_TEAM_ID"])])
        abbrs.add(team_id_to_abbr[int(game["VISITOR_TEAM_ID"])])

    print(f"{game_date}: {len(finals)} completed game(s) ({len(abbrs)} teams).")
    return abbrs


def _tracked_players_for_teams(
    team_abbrs: set[str], db_path: Path = DB_PATH, table: str = TABLE_NAME
) -> list[str]:
    """Tracked players whose most recently known team is in `team_abbrs`.

    "Most recently known team" is the same derivation used everywhere else in
    this project: the first token of a player's most recent MATCHUP string.
    """
    query = f"SELECT PLAYER_NAME, GAME_DATE, MATCHUP FROM {table} ORDER BY GAME_DATE"
    with sqlite3.connect(db_path) as conn:
        history = pd.read_sql(query, conn, parse_dates=["GAME_DATE"])

    if history.empty:
        return []

    latest_per_player = history.groupby("PLAYER_NAME", as_index=False).tail(1)
    return [
        row["PLAYER_NAME"]
        for _, row in latest_per_player.iterrows()
        if row["MATCHUP"].split()[0] in team_abbrs
    ]


def sync_recent_games(days_back: int = DEFAULT_DAYS_BACK, db_path: Path = DB_PATH) -> list[str]:
    """Refresh every tracked player whose team had a completed game in the
    last `days_back` days. Returns the list of player names successfully synced.
    """
    today = date.today()
    dates_to_check = [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days_back)]

    completed_team_abbrs: set[str] = set()
    for game_date in dates_to_check:
        completed_team_abbrs.update(_completed_team_abbrs_for_date(game_date))

    if not completed_team_abbrs:
        print(f"No completed games found in the last {days_back} day(s); nothing to sync.")
        return []

    affected_players = _tracked_players_for_teams(completed_team_abbrs, db_path)
    print(f"{len(affected_players)} tracked player(s) need a refresh.")

    season = _season_for_date(today)
    team_def_cache: dict = {}
    position_def_cache: dict = {}

    synced: list[str] = []
    for i, player_name in enumerate(affected_players, start=1):
        print(f"[{i}/{len(affected_players)}] Syncing {player_name}...")
        try:
            player_stats.build_player_stats(
                player_name,
                season=season,
                db_path=db_path,
                team_def_cache=team_def_cache,
                position_def_cache=position_def_cache,
            )
            synced.append(player_name)
        except Exception as exc:
            print(f"  Skipped {player_name}: {exc!r}")

    if synced:
        print("Backfilling USG_PCT for newly synced games...")
        add_usage.backfill_usage(db_path=db_path)

    print(f"Synced {len(synced)} of {len(affected_players)} affected player(s).")
    return synced


# --------------------------------------------------------------------------------------
# Step 2: retrain + persist models
# --------------------------------------------------------------------------------------

def retrain_models(models_dir: Path = MODELS_DIR) -> dict[str, object]:
    """Recompute PRA's rolling averages across the full table, refit one
    SGDRegressor per prop on the refreshed dataset, and persist each to disk.

    Calls edge_detector.train_models() directly, not load_or_train_models() --
    this function's whole job is an *unconditional* retrain on today's fresh
    data, not "reuse yesterday's weights if they're still on disk."
    """
    print("Recomputing PRA rolling averages across the full table...")
    add_pra.backfill_pra_rolling_averages()

    print("Refitting SGDRegressor models on the refreshed dataset...")
    models = edge_detector.train_models()
    edge_detector.save_models(models, models_dir)

    return models


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def run_morning_pipeline(days_back: int = DEFAULT_DAYS_BACK) -> None:
    print(f"=== Step 1/4: Sync recent games (last {days_back} day(s)) ===")
    sync_recent_games(days_back=days_back)

    print("\n=== Step 2/4: Auto-grade pending bets ===")
    graded = auto_grade_pending_bets()
    print(f"Graded {graded} bet(s).")

    print("\n=== Step 3/4: Retrain models ===")
    retrain_models()

    print("\n=== Step 4/4: Refresh live odds cache ===")
    fetch_live_odds(force_refresh=True)

    print("\nMorning pipeline complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days-back",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help=f"How many days back to check for completed games (default: {DEFAULT_DAYS_BACK}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_morning_pipeline(days_back=args.days_back)
