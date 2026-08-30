"""Route handler for a single player's real matchup, projections, and recent form.

Mirrors dashboard.py's Player Explorer tab: daily_edge_runner.project_player_live()
for real matchup context, edge_detector.resolve_lines_for_player() for market
(or mock-fallback) lines, and player_stats.get_recent_games() for the
historical distribution view.
"""

from __future__ import annotations

import os
from datetime import date

from fastapi import APIRouter, HTTPException, Request

from nbabettingmodel.services.daily_edge_runner import (
    PlayerNotPlayingTodayError,
    get_players_scheduled_today,
    get_todays_matchups,
    project_player_live,
)
from nbabettingmodel.services.edge_detector import (
    PROP_MODULES,
    load_or_train_models,
    resolve_lines_for_player,
)
from nbabettingmodel.services.player_stats import get_player_id, get_recent_games

router = APIRouter()


@router.get("/player/{player_name}")
def get_player_view(request: Request, player_name: str, game_date: str | None = None) -> dict:
    """Real matchup context + model projections vs. market lines for one
    player, plus their last 10 games' actual PTS/REB/AST for a distribution
    view.

    `recent_games` is always returned -- it's a pure read of already-collected
    history, independent of today's schedule. `matchup` is null (with
    `warning` explaining why) if this player has no game scheduled on
    `game_date`, or if grading/projecting hit a resolvable data gap (e.g. no
    games recorded for them at all yet); a genuinely unknown player name
    (never tracked in Player_Stats) is a 404 instead, since there's nothing
    at all to return in that case.
    """
    target_date = game_date or date.today().strftime("%Y-%m-%d")

    try:
        player_id = get_player_id(player_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    recent_games_df = get_recent_games(player_id, lookback=10)
    recent_games = [
        {**row, "GAME_DATE": row["GAME_DATE"].strftime("%Y-%m-%d")}
        for row in recent_games_df.to_dict(orient="records")
    ]

    models = getattr(request.app.state, "models", None)
    if models is None:
        models = load_or_train_models()

    matchup = None
    warning = None
    try:
        matchups = get_todays_matchups(target_date)
        scheduled_ids = get_players_scheduled_today(matchups)

        if player_id not in scheduled_ids:
            warning = f"{player_name} doesn't appear to have a game scheduled on {target_date}."
        else:
            view = project_player_live(player_id, models, matchups, target_date)
            api_key = os.environ.get("ODDS_API_KEY")
            lines = resolve_lines_for_player(player_id, view["player_name"], api_key)

            projections_vs_lines = [
                {
                    "prop": prop_type,
                    "model_projection": round(view["projections"][prop_type], 1),
                    "market_line": lines[prop_type]["line"],
                    "delta": round(
                        view["projections"][prop_type] - lines[prop_type]["line"], 1
                    ),
                }
                for prop_type in PROP_MODULES
            ]
            matchup = {
                "opponent": view["opponent_abbr"],
                "opponent_def_rating": view["opp_def_rating"],
                "opponent_ast_pct": view["opp_ast_pct"],
                "opponent_reb_pct": view["opp_reb_pct"],
                "projections_vs_lines": projections_vs_lines,
            }
    except (PlayerNotPlayingTodayError, ValueError) as exc:
        warning = str(exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "player_name": player_name,
        "player_id": player_id,
        "game_date": target_date,
        "matchup": matchup,
        "warning": warning,
        "recent_games": recent_games,
    }
