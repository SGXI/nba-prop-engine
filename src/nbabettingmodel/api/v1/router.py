"""Aggregates every v1 route module into one router, so main.py only has to
mount a single router under the /api/v1 prefix instead of one per module.
"""

from __future__ import annotations

from fastapi import APIRouter

from nbabettingmodel.api.v1 import ev_board, parlays, player, streaks, top_picks, tracker

api_router = APIRouter()
api_router.include_router(ev_board.router)
api_router.include_router(top_picks.router)
api_router.include_router(parlays.router)
api_router.include_router(streaks.router)
api_router.include_router(player.router)
api_router.include_router(tracker.router)
