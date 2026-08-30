"""Route handlers for the daily +EV board.

Pure HTTP plumbing -- all the actual business logic (schedule lookup, model
inference, live/mock odds resolution, EV math) lives in the services layer
and is reused verbatim via parlay_builder.build_slate_leaderboard(), the same
function the CLI tools and the Streamlit dashboard already call.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request

from nbabettingmodel.services.parlay_builder import build_slate_leaderboard

router = APIRouter()


@router.get("/ev-board")
def get_ev_board(request: Request, game_date: str | None = None) -> dict:
    """Slate-wide +EV betting board for `game_date` ('YYYY-MM-DD'; defaults to today).

    Deliberately a plain `def`, not `async def`: build_slate_leaderboard() does
    blocking I/O (ScoreboardV2, SQLite, the Odds API) with nothing to `await`,
    and FastAPI runs synchronous route functions in a worker thread
    automatically -- making this `async def` instead would run that blocking
    work directly on the event loop and stall every other concurrent request
    for as long as this one takes.

    Reads the model pipelines from `request.app.state.models` -- populated
    once at startup by main.py's lifespan handler -- instead of importing
    them from main.py directly, which would create a circular import (main.py
    imports this router to mount it; this module can't import back from
    main.py without that import trying to run before main.py finishes
    defining `app`). Falls back to None (build_slate_leaderboard() loads its
    own copy via load_or_train_models()) if state.models isn't set for any
    reason, so this endpoint still works even if the lifespan hasn't run.
    """
    target_date = game_date or date.today().strftime("%Y-%m-%d")
    models = getattr(request.app.state, "models", None)

    try:
        leaderboard = build_slate_leaderboard(target_date, models=models)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"game_date": target_date, "count": len(leaderboard), "bets": leaderboard}
