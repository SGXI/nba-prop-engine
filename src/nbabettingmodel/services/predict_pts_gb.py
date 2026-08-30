"""Train a HistGradientBoostingRegressor for PTS off nba_betting.db.

Reuses predict_pts.py's data loading, null-dropping, and time-aware split
verbatim (same import, not a copy) so this is an apples-to-apples comparison
against the linear baseline -- the only thing that changes is the estimator.

Usage:
    uv run python -m nbabettingmodel.services.predict_pts_gb
"""

from __future__ import annotations

from sklearn.ensemble import HistGradientBoostingRegressor

from nbabettingmodel.services.predict_pts import (
    FEATURE_COLS,
    TARGET_COL,
    drop_incomplete_rows,
    evaluate,
    load_dataset,
    time_aware_split,
)


def build_model() -> HistGradientBoostingRegressor:
    """A histogram-binned gradient-boosted tree ensemble.

    Unlike the linear baselines, it can model non-linear effects and feature
    interactions natively (e.g. rest days mattering more for high-minutes
    players than low-minutes ones) without hand-engineered interaction terms,
    and -- unlike SGDRegressor -- it doesn't need feature scaling. `random_state`
    fixes it for reproducibility; every other hyperparameter is left at
    scikit-learn's default for this first-pass comparison.
    """
    return HistGradientBoostingRegressor(random_state=42)


def main() -> None:
    df = load_dataset()
    df = drop_incomplete_rows(df)
    train_df, test_df = time_aware_split(df)

    X_train, y_train = train_df[FEATURE_COLS], train_df[TARGET_COL]
    X_test, y_test = test_df[FEATURE_COLS], test_df[TARGET_COL]

    model = build_model()
    model.fit(X_train, y_train)
    metrics = evaluate(model, X_test, y_test)

    print(f"\nTraining on {len(X_train)} rows, evaluating on {len(X_test)} held-out rows.")
    print(f"{'Model':<24}{'MAE':>10}{'R2':>10}")
    print(f"{'HistGradientBoosting':<24}{metrics['mae']:>10.3f}{metrics['r2']:>10.3f}")


if __name__ == "__main__":
    main()
