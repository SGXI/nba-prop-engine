"""Route handler for active player prop hot streaks.

Reuses streak_analyzer.get_hottest_streaks() verbatim -- the same
market-adjusted streak pipeline dashboard.py's Hottest Streaks tab uses.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nbabettingmodel.services.streak_analyzer import get_hottest_streaks

router = APIRouter()


@router.get("/streaks")
def get_streaks() -> dict:
    """Players hitting a standard prop threshold (15/20/25 PTS, 5/8/10
    AST/REB) in 8 of their last 10 games or better (>=80%), filtered to lines
    the market would still let you bet (priced better than -300).

    See streak_analyzer.py: the 80% hit-rate gate and the market price are
    deliberately computed two different ways (a raw count vs. a smoother
    Normal-distribution model) rather than one derived from the other --
    inverting the raw hit rate directly would price every qualifying streak
    at -400 or worse by mathematical necessity, emptying this endpoint every
    time.
    """
    try:
        streaks_df = get_hottest_streaks()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"count": len(streaks_df), "streaks": streaks_df.to_dict(orient="records")}
