"""Train baseline REB-prediction models off the Player_Stats table in nba_betting.db.

Mirrors predict_ast.py's architecture exactly, retargeted for rebounding:

Data flow
---------
1. sqlite3 -> read PLAYER_ID, GAME_DATE, REB, USG_PCT, and the feature columns
   straight out of the `Player_Stats` table. REB_AVG_5/REB_AVG_10, MIN_AVG_5/
   MIN_AVG_10, REST_DAYS, OPP_DEF_RATING, and OPP_REB_PCT are already
   pre-computed columns (player_stats.py rolled them with the same `.shift(1)`
   lag we use below for usage). USG_PCT is the one raw column in the mix --
   add_usage.py backfills it per game, but doesn't roll it.

2. pandas -> roll USG_PCT into USG_AVG_5 / USG_AVG_10 ourselves: `.shift(1)`
   first so a game's features only ever see strictly earlier games, then a
   rolling mean grouped by PLAYER_ID so one player's trailing average never
   blends in another player's usage.

3. pandas -> drop any row with a NaN among the target or features. Those NaNs
   come from the `.shift(1)` lag (a player's first game(s) have no prior games
   to average -- true for both the pre-computed columns and the usage rolling
   averages computed here) and from opponent-defense features being
   unavailable for games played before any team in the league had played yet
   (the season opener).

4. pandas -> sort every player's games together by GAME_DATE and cut the
   *global*, chronologically-sorted table 80/20. This is a time-aware split:
   the entire training set happened before the entire test set, mirroring how
   the model will actually be used (only past games are ever available to
   predict a future one). A random/shuffled split would let the model train on
   a player's April game and get evaluated on that same player's February
   game, which leaks information backwards in time and inflates the apparent
   accuracy.

5. scikit-learn -> fit LinearRegression and SGDRegressor on the training rows,
   predict on the held-out test rows, and score both with MAE and R^2.

Usage:
    uv run python -m nbabettingmodel.services.predict_reb
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
from sklearn.linear_model import LinearRegression, SGDRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

DB_PATH = Path(__file__).resolve().parents[3] / "nba_betting.db"
TABLE_NAME = "Player_Stats"

TARGET_COL = "REB"

# Pre-computed columns that already exist in Player_Stats (built by
# player_stats.py). Kept as its own list -- rather than folded into
# MODEL_FEATURE_COLS below -- because it doubles as the SQL SELECT list in
# load_dataset(), and USG_AVG_5/USG_AVG_10 aren't real table columns to select;
# they're computed locally in Step 2.
FEATURE_COLS = [
    # Rebounding form: trailing 5/10-game rebound averages.
    "REB_AVG_5", "REB_AVG_10",
    # Playing time: trailing 5/10-game minutes averages. More minutes means more
    # possessions to grab rebounds in, almost by construction.
    "MIN_AVG_5", "MIN_AVG_10",
    # Situational / opponent context. OPP_REB_PCT -- the opponent's own share of
    # available rebounds grabbed -- proxies for how well that team controls the
    # glass, i.e. how many boards they tend to leave for the other side.
    "REST_DAYS", "OPP_DEF_RATING", "OPP_REB_PCT",
]

# Rolling windows computed locally (see add_usage_rolling_averages), from the raw
# USG_PCT column add_usage.py backfills.
USAGE_ROLLING_WINDOWS = (5, 10)
USAGE_FEATURE_COLS = [f"USG_AVG_{w}" for w in USAGE_ROLLING_WINDOWS]

# The complete feature matrix used for modeling in this script.
MODEL_FEATURE_COLS = FEATURE_COLS + USAGE_FEATURE_COLS

TRAIN_FRACTION = 0.8


# --------------------------------------------------------------------------------------
# Step 1: load
# --------------------------------------------------------------------------------------

def load_dataset(
    db_path: Path = DB_PATH,
    table: str = TABLE_NAME,
    extra_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Pull GAME_DATE, the target, and the feature columns from SQLite.

    `extra_columns` pulls in anything beyond that default set. main() passes
    `extra_columns=["PLAYER_ID", "USG_PCT"]`: PLAYER_ID and the raw USG_PCT
    aren't modeling features by themselves, but both are needed to compute the
    usage rolling averages in Step 2. A scoped SELECT (rather than `SELECT *`)
    keeps this fast regardless of how many extra columns Player_Stats
    accumulates later.
    """
    columns = ["GAME_DATE", TARGET_COL] + FEATURE_COLS + (extra_columns or [])
    query = f"SELECT {', '.join(columns)} FROM {table}"
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql(query, conn, parse_dates=["GAME_DATE"])
    print(f"Loaded {len(df)} rows from '{table}'.")
    return df


# --------------------------------------------------------------------------------------
# Step 2: usage rolling averages (computed here, not pre-baked by player_stats.py)
# --------------------------------------------------------------------------------------

def add_usage_rolling_averages(
    df: pd.DataFrame, windows: tuple[int, ...] = USAGE_ROLLING_WINDOWS
) -> pd.DataFrame:
    """Attach USG_AVG_5 / USG_AVG_10, mirroring player_stats.py's rolling-average
    logic exactly: `.shift(1)` first so a game's features only ever describe
    strictly earlier games, then `.rolling(window, min_periods=1).mean()`,
    grouped by PLAYER_ID so the trailing average never blends across players.
    """
    df = df.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)

    def _usage_features_for_player(group: pd.DataFrame) -> pd.DataFrame:
        lagged = group["USG_PCT"].shift(1)
        return pd.DataFrame(
            {f"USG_AVG_{w}": lagged.rolling(w, min_periods=1).mean() for w in windows},
            index=group.index,
        )

    features = pd.concat(
        [_usage_features_for_player(group) for _, group in df.groupby("PLAYER_ID", sort=False)]
    ).sort_index()

    return df.join(features)


# --------------------------------------------------------------------------------------
# Step 3: clean
# --------------------------------------------------------------------------------------

def drop_incomplete_rows(
    df: pd.DataFrame, required_cols: list[str] | None = None
) -> pd.DataFrame:
    """Drop rows missing the target or any required column.

    Defaults to the target plus the pre-computed FEATURE_COLS; main() passes
    the full MODEL_FEATURE_COLS (including the usage rolling averages) once
    those have been computed.

    Every row dropped here is a game where at least one rolling/opponent
    feature couldn't be computed yet -- a `.shift(1)`-lagged first game in a
    player's sample (true for both the pre-computed stats and the usage
    rolling averages), or a season-opener played before the league had any
    prior-game data for the opponent-defense pull. Neither case is fixable;
    they're simply games the model has no history to predict from.
    """
    required = required_cols if required_cols is not None else [TARGET_COL] + FEATURE_COLS
    before = len(df)
    df = df.dropna(subset=required).reset_index(drop=True)
    dropped = before - len(df)
    print(f"Dropped {dropped} of {before} rows with missing values ({dropped / before:.1%}).")
    return df


# --------------------------------------------------------------------------------------
# Step 4: time-aware split
# --------------------------------------------------------------------------------------

def time_aware_split(
    df: pd.DataFrame, train_fraction: float = TRAIN_FRACTION
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sort every player's games together by GAME_DATE, then cut 80/20.

    This is a single global cutoff date, not a per-player split -- every row in
    the training set happened before every row in the test set, league-wide.
    """
    df_sorted = df.sort_values("GAME_DATE").reset_index(drop=True)
    split_idx = int(len(df_sorted) * train_fraction)

    train_df = df_sorted.iloc[:split_idx]
    test_df = df_sorted.iloc[split_idx:]

    print(
        f"Train: {len(train_df)} rows "
        f"({train_df['GAME_DATE'].min().date()} to {train_df['GAME_DATE'].max().date()})"
    )
    print(
        f"Test:  {len(test_df)} rows "
        f"({test_df['GAME_DATE'].min().date()} to {test_df['GAME_DATE'].max().date()})"
    )
    return train_df, test_df


# --------------------------------------------------------------------------------------
# Step 5: train + evaluate
# --------------------------------------------------------------------------------------

def build_models() -> dict[str, object]:
    """Two linear baselines, fit two different ways.

    * LinearRegression solves the OLS normal equations directly. It's scale-
      invariant (its coefficients simply rescale to compensate), deterministic,
      and unregularized -- a clean baseline.
    * SGDRegressor fits the same linear model via stochastic gradient descent
      instead of a closed-form solve. Gradient descent is sensitive to feature
      scale -- REST_DAYS (~0-5) and OPP_DEF_RATING (~100-120) sit on wildly
      different scales -- so it's wrapped in a Pipeline with StandardScaler,
      without which it would converge slowly or not at all.
    """
    return {
        "LinearRegression": LinearRegression(),
        "SGDRegressor": make_pipeline(StandardScaler(), SGDRegressor(random_state=42)),
    }


def evaluate(model, X_test: pd.DataFrame, y_test: pd.Series) -> dict[str, float]:
    predictions = model.predict(X_test)
    return {
        "mae": mean_absolute_error(y_test, predictions),
        "r2": r2_score(y_test, predictions),
    }


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def main() -> None:
    df = load_dataset(extra_columns=["PLAYER_ID", "USG_PCT"])
    df = add_usage_rolling_averages(df)
    df = drop_incomplete_rows(df, required_cols=[TARGET_COL] + MODEL_FEATURE_COLS)
    train_df, test_df = time_aware_split(df)

    X_train, y_train = train_df[MODEL_FEATURE_COLS], train_df[TARGET_COL]
    X_test, y_test = test_df[MODEL_FEATURE_COLS], test_df[TARGET_COL]

    print(f"\nTraining on {len(X_train)} rows, evaluating on {len(X_test)} held-out rows.")
    print(f"{'Model':<18}{'MAE':>10}{'R2':>10}")
    for name, model in build_models().items():
        model.fit(X_train, y_train)
        metrics = evaluate(model, X_test, y_test)
        print(f"{name:<18}{metrics['mae']:>10.3f}{metrics['r2']:>10.3f}")


if __name__ == "__main__":
    main()
