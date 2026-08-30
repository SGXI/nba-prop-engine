"""Route handlers for the bet ledger: aggregate PnL/ROI, and logging new bets.

Reuses bet_tracker.py verbatim -- the same ledger dashboard.py's PnL Tracker
tab reads and writes. Grading (Win/Loss/Push/Void) isn't exposed here as a
POST action: it happens via bet_tracker.auto_grade_pending_bets(), run by
data_sync.py's morning pipeline (or manually from the dashboard).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from nbabettingmodel.services.bet_tracker import calculate_pnl_stats, load_bet_history, log_bet

router = APIRouter()


class BetPayload(BaseModel):
    date: str = Field(..., description="Game date, 'YYYY-MM-DD'.")
    player_name: str
    prop: str = Field(..., description="PTS, AST, REB, or PRA.")
    side: str = Field(..., description="'Over' or 'Under'.")
    line: float
    odds: int = Field(..., description="American odds, e.g. -110 or +130.")
    wager_amount: float
    is_top_10: bool = False


@router.get("/tracker/pnl")
def get_tracker_pnl(top_10_only: bool = False) -> dict:
    """Win rate, total PnL, and ROI across the bet ledger.

    `top_10_only=true` scopes the same stats to just the bets logged from the
    Top 10 picks board, mirroring dashboard.py's PnL Tracker tab, which shows
    both this and the overall figures side by side.
    """
    try:
        history_df = load_bet_history()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if top_10_only:
        history_df = history_df[history_df["is_top_10"]]

    return calculate_pnl_stats(history_df)


@router.post("/tracker/log", status_code=201)
def post_tracker_log(payload: BetPayload) -> dict:
    """Record a new bet as 'Pending'. Its pnl stays 0.0 until it's resolved
    (auto-graded once the game is played, or set manually)."""
    try:
        bet_id = log_bet(
            date=payload.date,
            player_name=payload.player_name,
            prop=payload.prop,
            side=payload.side,
            line=payload.line,
            odds=payload.odds,
            wager_amount=payload.wager_amount,
            is_top_10=payload.is_top_10,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"id": bet_id, "status": "logged"}
