"""Compare model PTS/AST/REB/PRA projections against sportsbook lines to flag edges.

Data flow
---------
1. sqlite3 -> for a given PLAYER_ID, pull that player's full game log from
   Player_Stats (built by player_stats.py / add_usage.py) and compute their
   *current* rolling averages -- i.e. trailing windows through their most
   recent completed game, not the `.shift(1)`-lagged versions predict_pts.py
   etc. use for training. Training lags by one game because row N's features
   must not see game N's own result; here we want the opposite -- the most
   current form available heading into whatever the player's next game is.

2. scikit-learn -> load_or_train_models() prefers `joblib.load()`-ing the four
   persisted SGDRegressor pipelines from models/ (written by data_sync.py's
   morning retrain, or by this same function the first time it has to train
   from scratch) over actually refitting them. Loading four small files is
   near-instant; train_models() itself re-reads and re-fits on the entire
   Player_Stats history every time it runs, which is what daily_edge_runner.py
   and dashboard.py were paying on every CLI invocation / page load before
   this. train_models() reuses predict_pts.py / predict_ast.py /
   predict_reb.py's own load_dataset / add_usage_rolling_averages /
   drop_incomplete_rows / MODEL_FEATURE_COLS so the live model matches the
   backtested one exactly, fit on the *entire* available history (no 80/20
   split -- there's no held-out set to protect once this is live inference
   rather than evaluation).

3. resolve_lines_for_player() -> real market odds via
   live_odds_client.fetch_live_odds() (file-cached, 30-minute TTL), matched to
   this player by live_odds_client.normalize_player_name() on both sides.
   Falls back to get_mock_sportsbook_lines() -- entirely if no live odds are
   available at all (no ODDS_API_KEY configured, a fetch failure, or nothing
   cached yet), or per-prop if this player was found but not every market was
   offered on them. Mirrors daily_edge_runner.py's resolve_lines_for_player
   exactly, so the two odds sources behave identically whether a player is
   looked up here (single-player, placeholder opponent context) or through
   the production batch pipeline (real matchup context).

4. Edge calculation -> model projection minus sportsbook line. A delta beyond
   +/-EDGE_THRESHOLD prints a BET OVER / BET UNDER call; anything inside that
   band is treated as noise, not a real signal.

Usage:
    uv run python -m nbabettingmodel.services.edge_detector
    uv run python -m nbabettingmodel.services.edge_detector --player "Nikola Jokic"
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
from pathlib import Path

import joblib
import pandas as pd

from nbabettingmodel.services import predict_ast, predict_pra, predict_pts, predict_reb
from nbabettingmodel.services.live_odds_client import fetch_live_odds, normalize_player_name
from nbabettingmodel.services.player_stats import DB_PATH, TABLE_NAME, get_player_id

# live_odds_client.py calls load_dotenv() at import time (it's the module that
# actually reads ODDS_API_KEY), so the import above already guarantees a local
# .env file is loaded before main() reads the key below -- no separate call
# needed here.

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# One entry per prop type. Each module exposes an identical interface --
# load_dataset(), add_usage_rolling_averages(), drop_incomplete_rows(),
# MODEL_FEATURE_COLS, TARGET_COL, build_models() -- so everything below loops
# over this dict instead of hardcoding PTS/AST/REB/PRA-specific logic four
# times. PRA is added last (not alphabetically) so the mock-odds jitter draws
# for PTS/AST/REB in get_mock_sportsbook_lines() below stay exactly as they
# were before PRA existed -- dict iteration order is insertion order, and
# per-prop random draws happen in that order.
PROP_MODULES = {
    "PTS": predict_pts,
    "AST": predict_ast,
    "REB": predict_reb,
    "PRA": predict_pra,
}

# Raw per-game stat columns that get rolled into *_AVG_5 / *_AVG_10 /
# *_AVG_SEASON features across the four prop models (USG_PCT is handled
# separately below -- its rolling columns are named USG_AVG_*, not
# USG_PCT_AVG_*, matching the *_FEATURE_COLS convention in predict_pts.py).
# PRA (Points + Rebounds + Assists) is already its own stored column in
# Player_Stats -- see player_stats.py's clean_game_log() -- so it rolls the
# same generic way as PTS/REB/AST rather than needing separate derivation here.
ROLLING_STAT_COLS = ["PTS", "REB", "AST", "MIN", "FG_PCT", "FG3_PCT", "FT_PCT", "PRA"]
ROLLING_WINDOWS = (5, 10, "SEASON")
USAGE_ROLLING_WINDOWS = (5, 10)

# Opponent/situational columns carried straight over from the player's most
# recent game (see get_latest_features for why).
CONTEXT_COLS = ["REST_DAYS", "OPP_DEF_RATING", "OPP_AST_PCT", "OPP_REB_PCT", "OPP_FG_PCT_VS_POSITION"]

EDGE_THRESHOLD = 1.5

# No real sportsbook posts a non-positive prop line. A low-usage bench player's
# season average plus a negative jitter can otherwise land at 0.0 or below.
MIN_MOCK_LINE = 0.5

# Where persisted model pipelines live -- shared with data_sync.py's morning
# retrain, which is what actually keeps these fresh day to day. Lives inside
# the package now (src/nbabettingmodel/models/), not the project root.
MODELS_DIR = Path(__file__).resolve().parents[1] / "models"


# --------------------------------------------------------------------------------------
# Step 1: current features for one player
# --------------------------------------------------------------------------------------

def get_latest_features(player_id: int, db_path: Path = DB_PATH, table: str = TABLE_NAME) -> dict:
    """Build the current feature vector for `player_id` heading into their next game.

    Rolling averages here are computed over the player's most recent 5/10
    games (or full season) *through* their last completed game -- unlike
    training, there's no extra `.shift(1)` lag, since we want form as of right
    now, not form as of one game before now.

    REST_DAYS / OPP_DEF_RATING / OPP_AST_PCT / OPP_REB_PCT / OPP_FG_PCT_VS_POSITION
    are carried over from the player's most recent game as a placeholder
    snapshot -- without a schedule feed we don't yet know the real next
    opponent or rest gap. Swap this block for a real "upcoming opponent"
    lookup once a schedule source exists; everything downstream only cares
    about the returned dict's keys, not where the values came from.
    """
    query = f"""
        SELECT PLAYER_ID, PLAYER_NAME, GAME_DATE,
               PTS, REB, AST, MIN, FG_PCT, FG3_PCT, FT_PCT, PRA, USG_PCT,
               REST_DAYS, OPP_DEF_RATING, OPP_AST_PCT, OPP_REB_PCT, OPP_FG_PCT_VS_POSITION
        FROM {table}
        WHERE PLAYER_ID = ?
        ORDER BY GAME_DATE
    """
    with sqlite3.connect(db_path) as conn:
        history = pd.read_sql(query, conn, params=(player_id,), parse_dates=["GAME_DATE"])

    if history.empty:
        raise ValueError(f"No games found for PLAYER_ID={player_id} in '{table}'.")

    latest = history.iloc[-1]
    features = {
        "PLAYER_ID": player_id,
        "PLAYER_NAME": latest["PLAYER_NAME"],
        "AS_OF_GAME_DATE": latest["GAME_DATE"],
    }

    for stat in ROLLING_STAT_COLS:
        for window in ROLLING_WINDOWS:
            tail = history[stat] if window == "SEASON" else history[stat].tail(window)
            features[f"{stat}_AVG_{window}"] = tail.mean()

    for window in USAGE_ROLLING_WINDOWS:
        features[f"USG_AVG_{window}"] = history["USG_PCT"].tail(window).mean()

    for col in CONTEXT_COLS:
        features[col] = latest[col]

    return features


# --------------------------------------------------------------------------------------
# Step 2: model inference
# --------------------------------------------------------------------------------------

def train_models() -> dict[str, object]:
    """Train one SGDRegressor per prop type on the entire available history.

    Reuses each prop module's own data-prep pipeline so the live model is
    identical to the one predict_pts.py / predict_ast.py / predict_reb.py
    backtest -- same features, same cleaning. There's no train/test split
    here: for live inference we want every available row, since the 80/20 cut
    those scripts use exists only to leave something held out for evaluation.

    Always retrains from scratch -- never touches models/. Callers that want
    the fast path (load persisted weights, only retrain if necessary) should
    use load_or_train_models() instead; data_sync.py's morning pipeline calls
    this directly because retraining unconditionally is exactly the point of
    that step.
    """
    models = {}
    for prop_type, module in PROP_MODULES.items():
        df = module.load_dataset(extra_columns=["PLAYER_ID", "USG_PCT"])
        df = module.add_usage_rolling_averages(df)
        df = module.drop_incomplete_rows(
            df, required_cols=[module.TARGET_COL] + module.MODEL_FEATURE_COLS
        )
        X, y = df[module.MODEL_FEATURE_COLS], df[module.TARGET_COL]

        model = module.build_models()["SGDRegressor"]
        model.fit(X, y)
        models[prop_type] = model
        print(f"Trained SGDRegressor for {prop_type} on {len(X)} rows.")
    return models


def _model_path(prop_type: str, models_dir: Path = MODELS_DIR) -> Path:
    return models_dir / f"{prop_type.lower()}_sgd.joblib"


def save_models(models: dict[str, object], models_dir: Path = MODELS_DIR) -> None:
    """Persist each prop's fitted pipeline to `models_dir` as a .joblib file.

    Shared by load_or_train_models() (saving after a forced fallback retrain)
    and data_sync.py's retrain_models() (saving after the morning pipeline's
    unconditional retrain) -- one save path, so both stay consistent.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    for prop_type, model in models.items():
        model_path = _model_path(prop_type, models_dir)
        joblib.dump(model, model_path)
        print(f"Saved {prop_type} model to {model_path}")


def load_or_train_models(models_dir: Path = MODELS_DIR) -> dict[str, object]:
    """Load the four persisted SGDRegressor pipelines from `models_dir` if
    they're all present and load cleanly; otherwise train fresh
    (train_models()) and persist the result so the next call doesn't have to.

    This is the low-latency path daily_edge_runner.py, parlay_builder.py, and
    dashboard.py use instead of calling train_models() directly: loading four
    small files from disk is close to instant, where train_models() re-reads
    and re-fits on the entire Player_Stats history (tens of thousands of
    rows, four separate model fits) every time it's called. Falls back to a
    full retrain -- discarding any models already loaded this call, not a
    partial mix -- the moment any one file is missing or fails to load, so
    every prop is always trained on the same underlying data snapshot rather
    than four models fit at different, inconsistent points in time.
    """
    models: dict[str, object] = {}
    for prop_type in PROP_MODULES:
        model_path = _model_path(prop_type, models_dir)
        if not model_path.exists():
            print(f"No persisted model at {model_path}; training fresh.")
            models = train_models()
            save_models(models, models_dir)
            return models
        try:
            models[prop_type] = joblib.load(model_path)
        except Exception as exc:
            print(f"Persisted model at {model_path} failed to load ({exc!r}); training fresh.")
            models = train_models()
            save_models(models, models_dir)
            return models

    print(f"Loaded {len(models)} persisted model(s) from {models_dir}.")
    return models


def project_player(player_id: int, models: dict[str, object]) -> dict:
    """Run each prop's SGDRegressor on `player_id`'s current feature vector."""
    features = get_latest_features(player_id)

    projections = {}
    for prop_type, module in PROP_MODULES.items():
        X_row = pd.DataFrame([{col: features[col] for col in module.MODEL_FEATURE_COLS}])
        projections[prop_type] = float(models[prop_type].predict(X_row)[0])

    return {
        "player_id": player_id,
        "player_name": features["PLAYER_NAME"],
        "as_of_game_date": features["AS_OF_GAME_DATE"],
        "projections": projections,
    }


# --------------------------------------------------------------------------------------
# Step 3: real market odds, with a last-resort fallback to the mock generator
# --------------------------------------------------------------------------------------

def _snap_to_half_point(value: float, floor: float = MIN_MOCK_LINE) -> float:
    """Round to the nearest 0.5 (how real prop lines are quoted), then clamp to `floor`."""
    return max(floor, round(value * 2) / 2)


def get_mock_sportsbook_lines(player_id: int, db_path: Path = DB_PATH) -> dict[str, dict]:
    """Simulated Over/Under lines, standing in for a real odds feed during the offseason.

    Every line is seeded off the player's own season-average stats (real
    historical data) plus a small deterministic jitter, so the numbers look
    plausible without being tautologically close to (or copied from) the
    model's own rolling-average-based projection. Deterministic per player_id
    so repeated runs return the same mock line rather than a new random one
    each time.

    Swap this for a real sportsbook/Odds API call once the season starts --
    the only contract the rest of this module relies on is the return shape:
    {"PTS": {"line": float, "over_odds": int, "under_odds": int}, ...}.

    PRA's mock base is the *sum of the other three props' own season
    averages* (pts_avg + reb_avg + ast_avg), not an independently queried PRA
    column -- simpler, and numerically identical anyway since
    mean(PTS) + mean(REB) + mean(AST) == mean(PTS + REB + AST) over the same rows.
    """
    with sqlite3.connect(db_path) as conn:
        history = pd.read_sql(
            "SELECT PTS, REB, AST FROM Player_Stats WHERE PLAYER_ID = ?",
            conn,
            params=(player_id,),
        )

    rng = random.Random(player_id)
    odds_choices = [-110, -105, -115, -120]

    base_by_prop = {
        prop: (history[prop].mean() if not history.empty else 10.0)
        for prop in ("PTS", "REB", "AST")
    }
    base_by_prop["PRA"] = sum(base_by_prop[prop] for prop in ("PTS", "REB", "AST"))

    lines = {}
    for prop_type in PROP_MODULES:
        jittered = base_by_prop[prop_type] + rng.uniform(-1.5, 1.5)
        lines[prop_type] = {
            "line": _snap_to_half_point(jittered),
            "over_odds": rng.choice(odds_choices),
            "under_odds": rng.choice(odds_choices),
        }
    return lines


def resolve_lines_for_player(
    player_id: int, player_name: str, api_key: str | None = None
) -> dict[str, dict]:
    """Prefer real market odds for this player; fall back to
    get_mock_sportsbook_lines() -- entirely if no live odds are available at
    all (no key, a fetch failure, nothing cached yet), or per-prop if this
    player was found but not every market was offered on them.

    Mirrors daily_edge_runner.py's resolve_lines_for_player exactly, down to
    the normalize_player_name() lookup -- the only difference is this one
    fetches live odds itself (fetch_live_odds() is cache-backed, so calling
    it here per player is cheap after the first call within the 30-minute
    TTL), where daily_edge_runner.py's batch loop fetches once up front and
    passes the result to every player.
    """
    live_odds = fetch_live_odds(api_key)
    mock_lines = get_mock_sportsbook_lines(player_id)
    if live_odds is None:
        return mock_lines

    player_live = live_odds.get(normalize_player_name(player_name))
    if not player_live:
        print(f"  No live odds found for {player_name}; using mock lines.")
        return mock_lines

    merged = dict(mock_lines)
    merged.update(player_live)
    missing = [prop for prop in PROP_MODULES if prop not in player_live]
    if missing:
        print(f"  Live odds missing {', '.join(missing)} for {player_name}; filled with mock lines.")
    return merged


# --------------------------------------------------------------------------------------
# Step 4: edge calculation + output
# --------------------------------------------------------------------------------------

def compute_edges(projection_result: dict, lines: dict[str, dict]) -> list[dict]:
    """Compare each prop's model projection against its sportsbook line."""
    rows = []
    for prop_type, projection in projection_result["projections"].items():
        line_info = lines[prop_type]
        delta = projection - line_info["line"]

        if delta > EDGE_THRESHOLD:
            recommendation = "BET OVER"
        elif delta < -EDGE_THRESHOLD:
            recommendation = "BET UNDER"
        else:
            recommendation = "NO EDGE"

        rows.append(
            {
                "player_name": projection_result["player_name"],
                "prop_type": prop_type,
                "line": line_info["line"],
                "projection": projection,
                "delta": delta,
                "recommendation": recommendation,
            }
        )
    return rows


def print_edge_table(rows: list[dict]) -> None:
    header = f"{'Player':<20}{'Prop':<6}{'Line':>8}{'Proj':>8}{'Delta':>8}  {'Recommendation'}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['player_name']:<20}{row['prop_type']:<6}{row['line']:>8.1f}"
            f"{row['projection']:>8.1f}{row['delta']:>+8.1f}  {row['recommendation']}"
        )


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def find_edges_for_player(
    player_name: str, models: dict[str, object], api_key: str | None = None
) -> None:
    player_id = get_player_id(player_name)
    projection_result = project_player(player_id, models)
    lines = resolve_lines_for_player(player_id, projection_result["player_name"], api_key)
    rows = compute_edges(projection_result, lines)

    as_of = projection_result["as_of_game_date"].date()
    print(f"\n{projection_result['player_name']} -- projections as of last game ({as_of}):")
    print_edge_table(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--player", default="LeBron James", help="Full player name.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    models = load_or_train_models()
    api_key = os.environ.get("ODDS_API_KEY")
    find_edges_for_player(args.player, models, api_key)


if __name__ == "__main__":
    main()
