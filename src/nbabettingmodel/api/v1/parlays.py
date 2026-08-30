"""Route handler for uncorrelated multi-leg parlay recommendations.

Reuses parlay_builder.build_slate_leaderboard() + build_parlay() verbatim --
the same greedy, one-leg-per-player construction the CLI tool and
dashboard.py's Parlay Generator tab use.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request

from nbabettingmodel.services.daily_edge_runner import american_odds_to_decimal
from nbabettingmodel.services.parlay_builder import (
    build_parlay,
    build_slate_leaderboard,
    decimal_to_american_odds,
)

router = APIRouter()


@router.get("/parlays")
def get_parlay(
    request: Request,
    game_date: str | None = None,
    target_odds: int = 500,
    num_legs: int = 3,
) -> dict:
    """A greedy parlay built from the slate's highest-EV bets, stopping the
    moment *either* the combined odds clear `target_odds` (American) *or*
    `num_legs` legs have been added -- whichever happens first. "Uncorrelated"
    means at most one leg per player: a player's PTS and REB in the same game
    move together, so pricing them as independent legs the way a parlay's
    combined odds assume would overstate the true combined probability (see
    build_parlay()'s docstring for the full reasoning).

    `reached_target` in the response tells you which stop condition actually
    fired -- False means `num_legs` was hit before the odds target was, not
    that the slate ran out of +EV bets (check `count` against `num_legs` to
    tell those two cases apart).
    """
    if target_odds == 0:
        raise HTTPException(status_code=400, detail="target_odds cannot be 0.")
    if num_legs < 1:
        raise HTTPException(status_code=400, detail="num_legs must be at least 1.")

    target_date = game_date or date.today().strftime("%Y-%m-%d")
    models = getattr(request.app.state, "models", None)

    try:
        leaderboard = build_slate_leaderboard(target_date, models=models)
        target_decimal_odds = american_odds_to_decimal(target_odds)
        parlay = build_parlay(leaderboard, target_decimal_odds, max_legs=num_legs)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response = {
        "game_date": target_date,
        "target_odds": target_odds,
        "num_legs_requested": num_legs,
        "count": len(parlay["legs"]),
        "reached_target": parlay["reached_target"],
        "legs": parlay["legs"],
    }

    if parlay["legs"]:
        response["combined_odds"] = decimal_to_american_odds(parlay["decimal_odds"])
        response["combined_true_probability_pct"] = round(parlay["true_prob"] * 100, 2)
        response["total_ev_pct"] = round(
            (parlay["true_prob"] * parlay["decimal_odds"] - 1) * 100, 2
        )
    else:
        response["combined_odds"] = None
        response["combined_true_probability_pct"] = None
        response["total_ev_pct"] = None

    return response
