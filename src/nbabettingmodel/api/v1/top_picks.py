"""Route handler for the Top 10 Kelly-sized picks board.

Pure HTTP plumbing -- the slate scan reuses parlay_builder.build_slate_leaderboard()
(same as ev_board.py) and the Kelly math reuses services.kelly.build_top_picks(),
the same logic dashboard.py's Top 10 & Staking tab uses.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request

from nbabettingmodel.services.kelly import build_top_picks
from nbabettingmodel.services.parlay_builder import build_slate_leaderboard

router = APIRouter()


@router.get("/top-picks")
def get_top_picks(
    request: Request,
    game_date: str | None = None,
    min_edge: float = 3.0,
    bankroll: float = 1000.0,
    fractional_kelly: float = 0.25,
) -> dict:
    """The slate's top 10 highest-EV bets (>= `min_edge`% EV), each sized with
    fractional Kelly staking against `bankroll`.

    Deliberately a plain `def`, not `async def` -- see ev_board.py's
    get_ev_board() docstring: build_slate_leaderboard() is blocking I/O with
    nothing to `await`, so FastAPI runs it in a worker thread instead of
    stalling the event loop for other concurrent requests.

    Each pick's `recommended_wager` assumes it's the *only* bet placed --
    Kelly sizing is per-bet, not portfolio-aware. Staking all 10 picks shown
    here simultaneously will often commit far more than 100% of `bankroll`;
    a frontend should surface that the same way dashboard.py's tab does.
    """
    target_date = game_date or date.today().strftime("%Y-%m-%d")
    models = getattr(request.app.state, "models", None)

    try:
        leaderboard = build_slate_leaderboard(target_date, models=models)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    picks = build_top_picks(
        leaderboard, min_edge=min_edge, bankroll=bankroll, fractional_kelly=fractional_kelly
    )
    total_recommended = round(sum(pick["recommended_wager"] for pick in picks), 2)

    return {
        "game_date": target_date,
        "min_edge": min_edge,
        "bankroll": bankroll,
        "fractional_kelly": fractional_kelly,
        "count": len(picks),
        "total_recommended_wager": total_recommended,
        "exceeds_bankroll_if_staked_simultaneously": total_recommended > bankroll,
        "picks": picks,
    }
