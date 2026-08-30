"""Live NBA player-prop odds client for The Odds API v4, file-cached to
protect a free-tier quota.

Data flow
---------
1. GET /v4/sports/basketball_nba/events -> lists upcoming/live NBA events
   (games), each with an `id`. Player-prop markets are NOT available on the
   top-level /v4/sports/{sport}/odds endpoint -- confirmed while integrating
   this client, that endpoint only serves game-level markets (h2h/spreads/
   totals). Player props only exist on the *per-event* odds endpoint, which
   is why events have to be listed first to get the ids to query individually.

2. GET /v4/sports/basketball_nba/events/{eventId}/odds -> one call per event,
   with markets=player_points,player_rebounds,player_assists,
   player_points_rebounds_assists, regions=us, oddsFormat=american. A full
   refresh costs `1 + len(events)` requests against the API quota, not one --
   which is exactly why this is cached as aggressively as it is.

3. File-based cache (live_odds_cache.json, 30-minute TTL) -> fetch_live_odds()
   only calls the API at all if the cache file is missing or older than 30
   minutes; every other call in that window is a free, instant local read.
   The cache also survives process restarts (unlike an in-memory cache),
   which matters because the Streamlit dashboard and the CLI tools are
   separate processes that both need to share the same quota budget.

4. normalize_player_name() -> a matching key, not a display string: strips
   diacritics (NFKD decompose, drop combining marks), punctuation, and name
   suffixes (Jr./Sr./II-V), then lowercases and collapses whitespace. Used on
   both sides of every lookup -- The Odds API's player names and
   Player_Stats.PLAYER_NAME are normalized the same way before comparing --
   so accBent/suffix mismatches between the two sources don't cause a live
   line to silently go unmatched.

NOTE: implemented against The Odds API's documented events + per-event-odds
schema; not live-tested end-to-end against a real API key. The response
parsing (bookmakers -> markets -> outcomes) matches the same shape already
used successfully for The Odds API's other endpoints elsewhere in this
project, so it should hold, but treat the very first live run as a
verification step.

Usage:
    uv run python -m nbabettingmodel.services.live_odds_client
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# Loaded here, not just in dashboard.py: this module is what actually reads
# ODDS_API_KEY, and it's also used directly by daily_edge_runner.py as a CLI
# entry point (not only via the dashboard), so the .env file needs to be
# available regardless of which one runs first. Idempotent -- safe to also
# call from dashboard.py's own startup.
load_dotenv()

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "basketball_nba"
REGIONS = "us"
ODDS_FORMAT = "american"
PLAYER_PROP_MARKETS = "player_points,player_rebounds,player_assists,player_points_rebounds_assists"

MARKET_TO_PROP = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_points_rebounds_assists": "PRA",
}

REQUEST_TIMEOUT = 30

# Pause between per-event odds calls -- a full refresh is one call per event,
# and stats-style rate-limit politeness applies to any external API, not just
# stats.nba.com.
REQUEST_DELAY = 0.6

CACHE_PATH = Path(__file__).resolve().parents[3] / "live_odds_cache.json"
CACHE_TTL_SECONDS = 30 * 60

# Common name suffixes to drop when normalizing -- comparing "Jaren Jackson"
# to "Jaren Jackson Jr." should match.
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


# --------------------------------------------------------------------------------------
# Name normalization
# --------------------------------------------------------------------------------------

def normalize_player_name(name: str) -> str:
    """Normalize a player name into a matching key for comparing The Odds
    API's names against Player_Stats.PLAYER_NAME.

    Not a display string -- lowercased, accent-stripped, punctuation-stripped,
    and suffix-stripped, so "Nikola JokiÄ‡", "Nikola Jokic", and "NIKOLA JOKIC"
    (or "Jaren Jackson Jr." vs "Jaren Jackson") all normalize to the same key.
    """
    if not name:
        return ""

    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    # Periods and apostrophes (curly or straight) are dropped outright;
    # hyphens are folded to spaces so hyphenated names still tokenize cleanly.
    cleaned = re.sub(r"[.'â€™]", "", without_accents)
    cleaned = re.sub(r"-", " ", cleaned)

    tokens = [tok for tok in cleaned.lower().split() if tok not in NAME_SUFFIXES]
    return " ".join(tokens)


# --------------------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------------------

def _load_cache() -> dict | None:
    """The cached payload, if the file exists and is still within TTL."""
    if not CACHE_PATH.exists():
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    fetched_at = cache.get("fetched_at")
    if fetched_at is None or (time.time() - fetched_at) > CACHE_TTL_SECONDS:
        return None
    return cache


def _write_cache(lines_by_player: dict) -> None:
    payload = {"fetched_at": time.time(), "lines_by_player": lines_by_player}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def get_cache_last_updated() -> datetime | None:
    """When the odds cache was last actually refreshed from the live API
    (regardless of whether it's still within TTL), or None if no cache file
    exists yet. Used by the dashboard's "Odds last updated" indicator.
    """
    if not CACHE_PATH.exists():
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    fetched_at = cache.get("fetched_at")
    return datetime.fromtimestamp(fetched_at, tz=timezone.utc) if fetched_at is not None else None


# --------------------------------------------------------------------------------------
# Live fetch
# --------------------------------------------------------------------------------------

def _fetch_nba_events(api_key: str) -> list[dict]:
    """List upcoming/live NBA events; each `id` is what the per-event odds call needs."""
    response = requests.get(
        f"{ODDS_API_BASE_URL}/sports/{SPORT_KEY}/events",
        params={"apiKey": api_key},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _fetch_event_player_props(api_key: str, event_id: str) -> dict:
    """One event's player-prop markets, across whichever US bookmakers offer them."""
    response = requests.get(
        f"{ODDS_API_BASE_URL}/sports/{SPORT_KEY}/events/{event_id}/odds",
        params={
            "apiKey": api_key,
            "regions": REGIONS,
            "markets": PLAYER_PROP_MARKETS,
            "oddsFormat": ODDS_FORMAT,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _parse_event_odds(event_payload: dict) -> dict[str, dict[str, dict]]:
    """Flatten one event's bookmaker -> market -> outcome nesting into
    {normalized_player_name: {prop_type: {"line", "over_odds", "under_odds"}}}.

    Takes the first bookmaker listed for simplicity; swap for a consensus/
    best-line aggregation across books if that matters later.
    """
    lines_by_player: dict[str, dict[str, dict]] = {}
    bookmakers = event_payload.get("bookmakers") or []
    if not bookmakers:
        return lines_by_player

    for market in bookmakers[0].get("markets", []):
        prop_type = MARKET_TO_PROP.get(market.get("key"))
        if prop_type is None:
            continue
        for outcome in market.get("outcomes", []):
            raw_name = outcome.get("description")
            point = outcome.get("point")
            if not raw_name or point is None:
                continue

            normalized_name = normalize_player_name(raw_name)
            entry = lines_by_player.setdefault(normalized_name, {}).setdefault(
                prop_type, {"line": point, "over_odds": None, "under_odds": None}
            )
            if outcome.get("name") == "Over":
                entry["over_odds"] = outcome.get("price")
            elif outcome.get("name") == "Under":
                entry["under_odds"] = outcome.get("price")
    return lines_by_player


def fetch_live_odds(api_key: str | None = None, force_refresh: bool = False) -> dict | None:
    """Cached, normalized-name-keyed live PTS/AST/REB/PRA odds for the current
    NBA slate: {normalized_player_name: {"PTS": {...}, "AST": {...}, ...}}.

    Returns None -- never raises -- if no API key is configured or the fetch
    fails, so callers can fall back to mock data cleanly instead of crashing
    over a missing key or a third-party outage.

    Hits the real API at most once every CACHE_TTL_SECONDS regardless of call
    volume or how many separate processes (dashboard, CLI tools) ask -- a full
    refresh costs one /events call plus one /events/{id}/odds call per event,
    which adds up fast against a free-tier quota if it ran on every request.
    """
    if not force_refresh:
        cached = _load_cache()
        if cached is not None:
            return cached["lines_by_player"]

    api_key = api_key or os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY not set; no live odds available.")
        return None

    try:
        events = _fetch_nba_events(api_key)
    except (requests.RequestException, ValueError) as exc:
        print(f"Odds API events request failed ({exc!r}); no live odds available.")
        return None

    lines_by_player: dict[str, dict[str, dict]] = {}
    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue
        try:
            event_payload = _fetch_event_player_props(api_key, event_id)
        except (requests.RequestException, ValueError) as exc:
            print(f"  Skipped event {event_id}: {exc!r}")
            time.sleep(REQUEST_DELAY)
            continue

        for player, props in _parse_event_odds(event_payload).items():
            lines_by_player.setdefault(player, {}).update(props)
        time.sleep(REQUEST_DELAY)

    _write_cache(lines_by_player)
    print(f"Fetched live odds for {len(lines_by_player)} players across {len(events)} event(s).")
    return lines_by_player


if __name__ == "__main__":
    odds = fetch_live_odds()
    if odds is None:
        print("No live odds fetched (see message above).")
    else:
        for player, props in list(odds.items())[:5]:
            print(player, props)
