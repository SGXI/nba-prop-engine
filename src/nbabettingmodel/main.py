"""FastAPI application entrypoint.

Data flow
---------
1. lifespan() -> runs once when the server process starts (and once more,
   the teardown half, when it shuts down) -- not per-request, not per-worker-
   thread. It loads the four persisted SGDRegressor pipelines (PTS/AST/REB/
   PRA) via services.edge_detector.load_or_train_models() exactly once, into
   the module-level `ml_models` dict, then attaches that same dict object to
   `app.state.models` so route handlers can read it without importing this
   module directly (see api/v1/ev_board.py for why that matters -- a router
   importing back from main.py would be a circular import).

2. CORSMiddleware -> scoped to http://localhost:3000, the future Next.js
   frontend's local dev origin. Nothing else is allowed to call this API
   cross-origin yet; widen this list as real deployment origins exist.

3. api/v1/router.py's api_router -> aggregates every route module
   (ev_board, top_picks, parlays, streaks, player, tracker) into one router,
   mounted here under /api/v1 -- e.g. ev_board's /ev-board route is reachable
   at /api/v1/ev-board.

Usage:
    uv run uvicorn nbabettingmodel.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nbabettingmodel.api.v1.router import api_router
from nbabettingmodel.services.edge_detector import load_or_train_models

# The "global dictionary" the lifespan handler populates once at startup.
# Mutated in place (.update() / .clear()), never reassigned, so app.state.models
# below and this name keep pointing at the exact same dict object throughout
# the process's life.
ml_models: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Loading persisted models (PTS/AST/REB/PRA)...")
    ml_models.update(load_or_train_models())
    app.state.models = ml_models
    print(f"Loaded {len(ml_models)} model(s): {list(ml_models.keys())}")

    yield

    ml_models.clear()
    print("Cleared in-memory models on shutdown.")


app = FastAPI(title="NBA +EV Betting Engine API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/")
def root() -> dict:
    return {"status": "ok", "models_loaded": list(ml_models.keys())}
