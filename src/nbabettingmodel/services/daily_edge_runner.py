"""Production daily runner: real opponents, real rest days, real market odds.

Upgrades edge_detector.py's offseason prototype in two ways:
  * the "ghost matchup" problem -- edge_detector.py had no schedule to consult,
    so it approximated a player's next-game context by reusing their *last*
    game's opponent/rest days. This script looks up the real one.
  * a real odds source (live_odds_client.py's cached Odds API client), with
    the old mock generator kept only as an explicit last-resort fallback --
    not the everyday path -- for whatever real market data can't cover (no
    API key configured, a third-party outage, or a player simply not offered
    by any bookmaker that night).

Data flow
---------
1. `nba_api.stats.endpoints.ScoreboardV2` -> one live call for the target date's
   full slate. We only read its GameHeader result set (HOME_TEAM_ID /
   VISITOR_TEAM_ID) to build a {TEAM_ID: opponent_TEAM_ID} map for every team
   playing that night. ScoreboardV2 is flagged deprecated in nba_api in favor
   of ScoreboardV3 for the 2025-26 season, due to a known bug in its LineScore
   dataset for games between Oct 22 and Dec 25, 2025 -- but GameHeader, the one
   result set we use, is unaffected. Live-tested against a real game date to
   confirm. Its DeprecationWarning is suppressed since it's noise here.

2. sqlite3 -> for the target player, find their own team (the first token of
   their most recent MATCHUP string, e.g. 'LAL' in 'LAL @ BOS'), look up
   tonight's opponent via the map from Step 1, then pull that opponent's most
   recently *cached* OPP_DEF_RATING / OPP_AST_PCT / OPP_REB_PCT / positional
   defense straight out of Player_Stats -- "cached" meaning reused from data
   player_stats.py already collected, not a fresh LeagueDashTeamStats call.
   REST_DAYS is computed directly as (target date - player's last game date),
   not carried over from a past game the way edge_detector.py's placeholder did.

3. `live_odds_client.fetch_live_odds()` -> the real, file-cached (30-minute
   TTL) market lines for the whole slate in one call, reused here rather than
   reimplemented. Returns None -- never raises -- if no live odds are
   available at all, which resolve_lines_for_player() treats as the signal to
   fall back to edge_detector.py's get_mock_sportsbook_lines() instead of
   crashing the run. Player names are matched via
   live_odds_client.normalize_player_name() on both sides (the Odds API's
   name and Player_Stats.PLAYER_NAME), so accents/suffixes don't cause a real
   line to go unmatched.

4. edge_detector.load_or_train_models() -> loads the four persisted
   SGDRegressor pipelines from models/ (written by data_sync.py's morning
   retrain) instead of refitting them on every run. Falls back to a full
   train_models() + save if any file is missing or fails to load, so this
   still works correctly the first time it's ever run, before data_sync.py
   has produced anything to load.

5. scipy.stats.norm -> a raw point delta ignores the price: +1.5 points of
   "edge" is worthless at -300 and great at +150. So each side of each prop is
   priced properly instead: model projection + our historical backtested MAE
   (as a stand-in standard deviation) define a Normal distribution over the
   actual outcome; norm.cdf turns that into a true win probability for the
   Over and Under; American odds convert to a decimal payout; and
   EV = true_probability * decimal_payout - 1 is the expected return per $1
   staked.

6. Batch mode (the default -- omit --player) -> pandas pulls every tracked
   player's most recent game in one bulk query, derives each one's current
   team the same way the single-player path does, and keeps only those whose
   team is a key in the Step-1 matchup map. Steps 2-5 then run per player
   inside a try/except loop -- one player with a data gap (no odds posted,
   an unresolvable opponent, whatever) is logged and skipped rather than
   aborting the whole scan -- and every qualifying bet across the entire
   slate is collected into one master list, sorted by EV descending, and
   printed as a single slate-wide board instead of one table per player.

Usage:
    uv run python -m nbabettingmodel.services.daily_edge_runner
    uv run python -m nbabettingmodel.services.daily_edge_runner --player "Nikola Jokic"
    # Test against a real past game date (there's no live NBA action right now):
    uv run python -m nbabettingmodel.services.daily_edge_runner --player "LeBron James" --game-date 2026-04-12
"""

from __future__ import annotations

import argparse
import sqlite3
import warnings
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import scoreboardv2
from nba_api.stats.static import teams as static_teams
from scipy.stats import norm

from nbabettingmodel.services.edge_detector import (
    PROP_MODULES,
    ROLLING_STAT_COLS,
    ROLLING_WINDOWS,
    USAGE_ROLLING_WINDOWS,
    get_mock_sportsbook_lines,
    load_or_train_models,
)
from nbabettingmodel.services.live_odds_client import fetch_live_odds, normalize_player_name
from nbabettingmodel.services.player_stats import DB_PATH, REQUEST_TIMEOUT, TABLE_NAME, get_player_id

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# Historical backtested MAE per prop (see predict_pts.py / predict_ast.py /
# predict_reb.py / predict_pra.py), used as a standard deviation for the
# Normal distribution behind the EV model. MAE isn't literally a standard
# deviation, but with no per-prediction uncertainty estimate available from
# these models, it's the best empirically grounded stand-in we have for "how
# wrong the model typically is" -- and that's exactly what sigma needs to
# represent here. STD_DEV_PRA is naturally the largest of the four: it's a sum
# of three noisy quantities, so its own error compounds accordingly.
STD_DEV_PTS = 4.77
STD_DEV_AST = 1.41
STD_DEV_REB = 1.92
STD_DEV_PRA = 6.33
STD_DEV_BY_PROP = {
    "PTS": STD_DEV_PTS,
    "AST": STD_DEV_AST,
    "REB": STD_DEV_REB,
    "PRA": STD_DEV_PRA,
}

# Minimum expected value, as a percent of stake, for a bet to make the
# leaderboard at all.
EV_THRESHOLD_PCT = 2.0


class PlayerNotPlayingTodayError(Exception):
    """Raised when a player's team has no game on the requested date."""


# --------------------------------------------------------------------------------------
# Team ID <-> abbreviation (offline, static lookups)
# --------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _team_abbr_to_id() -> dict[str, int]:
    return {t["abbreviation"]: t["id"] for t in static_teams.get_teams()}


@lru_cache(maxsize=1)
def _team_id_to_abbr() -> dict[int, str]:
    return {t["id"]: t["abbreviation"] for t in static_teams.get_teams()}


# --------------------------------------------------------------------------------------
# Step 1: tonight's schedule
# --------------------------------------------------------------------------------------

def get_todays_matchups(game_date: str) -> dict[int, int]:
    """One ScoreboardV2 call: map every team playing on `game_date` to their opponent's TEAM_ID."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        endpoint = scoreboardv2.ScoreboardV2(game_date=game_date, timeout=REQUEST_TIMEOUT)
    games = endpoint.game_header.get_data_frame()

    matchups: dict[int, int] = {}
    for _, game in games.iterrows():
        home_id, away_id = int(game["HOME_TEAM_ID"]), int(game["VISITOR_TEAM_ID"])
        matchups[home_id] = away_id
        matchups[away_id] = home_id

    print(f"Found {len(games)} games on {game_date} ({len(matchups)} teams in action).")
    return matchups


# --------------------------------------------------------------------------------------
# Step 2: dynamic feature construction
# --------------------------------------------------------------------------------------

def _lookup_cached_opponent_stats(
    player_id: int, opponent_abbr: str, db_path: Path = DB_PATH, table: str = TABLE_NAME
) -> dict:
    """Pull `opponent_abbr`'s most recently recorded defensive snapshot out of
    Player_Stats -- "cached" in the sense that it reuses data player_stats.py
    already collected, rather than issuing a fresh LeagueDashTeamStats call the
    way the original historical backfill did.

    OPP_DEF_RATING / OPP_AST_PCT / OPP_REB_PCT are team-wide, so any row where
    *any* tracked player faced this opponent works. OPP_FG_PCT_VS_POSITION is
    specific to this player's own position, so it's only valid from a row
    where *this exact player* faced this exact opponent. If that never
    happened this season, we fall back to this player's own most recent
    OPP_FG_PCT_VS_POSITION (their positional-matchup figure against whichever
    team they last played) as an approximation rather than leaving it null.
    """
    with sqlite3.connect(db_path) as conn:
        team_snapshot = conn.execute(
            f"""
            SELECT OPP_DEF_RATING, OPP_AST_PCT, OPP_REB_PCT FROM {table}
            WHERE OPPONENT_ABBR = ? ORDER BY GAME_DATE DESC LIMIT 1
            """,
            (opponent_abbr,),
        ).fetchone()

        position_row = conn.execute(
            f"""
            SELECT OPP_FG_PCT_VS_POSITION FROM {table}
            WHERE PLAYER_ID = ? AND OPPONENT_ABBR = ? ORDER BY GAME_DATE DESC LIMIT 1
            """,
            (player_id, opponent_abbr),
        ).fetchone()

        if position_row is None:
            print(
                f"  No prior matchup vs {opponent_abbr} for this player this season; "
                f"using their most recent positional-defense figure as an approximation."
            )
            position_row = conn.execute(
                f"""
                SELECT OPP_FG_PCT_VS_POSITION FROM {table}
                WHERE PLAYER_ID = ? ORDER BY GAME_DATE DESC LIMIT 1
                """,
                (player_id,),
            ).fetchone()

    if team_snapshot is None:
        raise ValueError(f"No cached defensive stats found for opponent '{opponent_abbr}'.")

    opp_def_rating, opp_ast_pct, opp_reb_pct = team_snapshot
    return {
        "OPP_DEF_RATING": opp_def_rating,
        "OPP_AST_PCT": opp_ast_pct,
        "OPP_REB_PCT": opp_reb_pct,
        "OPP_FG_PCT_VS_POSITION": position_row[0] if position_row else None,
    }


def get_live_features(
    player_id: int,
    matchups: dict[int, int],
    game_date: str,
    db_path: Path = DB_PATH,
    table: str = TABLE_NAME,
) -> dict:
    """Build `player_id`'s feature vector for their real game on `game_date`.

    Rolling-average logic (PTS/REB/AST/MIN/shooting-efficiency/usage, each over
    the trailing 5/10 games or season-to-date) is identical to
    edge_detector.py's get_latest_features -- that part only depends on the
    player's own history, which hasn't changed. What's different is
    REST_DAYS and the OPP_* columns: computed from the real schedule and the
    real opponent instead of carried over from the player's last game.

    Raises PlayerNotPlayingTodayError if this player's team isn't in
    `matchups` (i.e. ScoreboardV2 has no game for them on `game_date`).
    """
    query = f"""
        SELECT PLAYER_ID, PLAYER_NAME, GAME_DATE, MATCHUP,
               PTS, REB, AST, MIN, FG_PCT, FG3_PCT, FT_PCT, PRA, USG_PCT
        FROM {table}
        WHERE PLAYER_ID = ?
        ORDER BY GAME_DATE
    """
    with sqlite3.connect(db_path) as conn:
        history = pd.read_sql(query, conn, params=(player_id,), parse_dates=["GAME_DATE"])

    # In real production use this is a no-op -- "today" is always after the
    # player's last completed game, since the DB only holds finished games.
    # It matters for testing with --game-date: without this filter, overriding
    # game_date to an earlier point in an already-collected season would leak
    # later games into the "current form" rolling averages below.
    history = history[history["GAME_DATE"] <= pd.Timestamp(game_date)].reset_index(drop=True)

    if history.empty:
        raise ValueError(f"No games found for PLAYER_ID={player_id} on or before {game_date}.")

    latest = history.iloc[-1]
    player_name = latest["PLAYER_NAME"]

    # MATCHUP is always "<TEAM> @/vs. <OPPONENT>" (see player_stats.py), so the
    # player's own team is the first token of their most recent game.
    own_team_abbr = latest["MATCHUP"].split()[0]
    own_team_id = _team_abbr_to_id().get(own_team_abbr)
    opponent_team_id = matchups.get(own_team_id) if own_team_id is not None else None

    if opponent_team_id is None:
        raise PlayerNotPlayingTodayError(
            f"{player_name} ({own_team_abbr}) has no game scheduled on {game_date}."
        )
    opponent_abbr = _team_id_to_abbr()[opponent_team_id]

    features = {"PLAYER_ID": player_id, "PLAYER_NAME": player_name, "OPPONENT_ABBR": opponent_abbr}

    for stat in ROLLING_STAT_COLS:
        for window in ROLLING_WINDOWS:
            tail = history[stat] if window == "SEASON" else history[stat].tail(window)
            features[f"{stat}_AVG_{window}"] = tail.mean()
    for window in USAGE_ROLLING_WINDOWS:
        features[f"USG_AVG_{window}"] = history["USG_PCT"].tail(window).mean()

    # True rest -- days between the target date and this player's last played
    # game -- not a value carried over from a past game.
    features["REST_DAYS"] = (pd.Timestamp(game_date) - latest["GAME_DATE"]).days

    features.update(_lookup_cached_opponent_stats(player_id, opponent_abbr, db_path, table))
    return features


def project_player_live(
    player_id: int, models: dict[str, object], matchups: dict[int, int], game_date: str
) -> dict:
    """Run each prop's SGDRegressor on `player_id`'s real feature vector for `game_date`.

    Includes the opponent context features alongside the projections
    (OPP_DEF_RATING / OPP_AST_PCT / OPP_REB_PCT) -- existing callers
    (compute_ev_leaderboard, etc.) only ever read specific keys off this dict,
    so the extra ones are additive and don't change anything for them; the
    dashboard's Player Explorer tab is what actually needs them, to display
    tonight's matchup context without a second, duplicate feature lookup.
    """
    features = get_live_features(player_id, matchups, game_date)

    projections = {}
    for prop_type, module in PROP_MODULES.items():
        X_row = pd.DataFrame([{col: features[col] for col in module.MODEL_FEATURE_COLS}])
        projections[prop_type] = float(models[prop_type].predict(X_row)[0])

    return {
        "player_id": player_id,
        "player_name": features["PLAYER_NAME"],
        "opponent_abbr": features["OPPONENT_ABBR"],
        "opp_def_rating": features["OPP_DEF_RATING"],
        "opp_ast_pct": features["OPP_AST_PCT"],
        "opp_reb_pct": features["OPP_REB_PCT"],
        "projections": projections,
    }


# --------------------------------------------------------------------------------------
# Step 3: real market odds, with a last-resort fallback to the mock generator
# --------------------------------------------------------------------------------------

def resolve_lines_for_player(
    player_id: int, player_name: str, live_odds: dict[str, dict] | None
) -> dict[str, dict]:
    """Prefer real market odds for this player; fall back to
    get_mock_sportsbook_lines() only as a last resort -- entirely if no live
    odds were fetched at all (no key, a fetch failure, or nothing cached yet),
    or per-prop if this player was found but not every market was offered on
    them.

    `live_odds` is keyed by normalize_player_name() (see live_odds_client.py),
    so this player's name is normalized the same way before lookup -- an
    accent or a "Jr."/"III" suffix mismatch between our DB and the odds feed
    won't cause a real line to be missed.
    """
    mock_lines = get_mock_sportsbook_lines(player_id)
    if live_odds is None:
        return mock_lines

    player_live = live_odds.get(normalize_player_name(player_name))
    if not player_live:
        print(f"  No live odds found for {player_name}; using mock lines.")
        return mock_lines

    merged = dict(mock_lines)
    merged.update(player_live)
    missing = [prop for prop in PROP_MODULES if prop not in player_live]
    if missing:
        print(f"  Live odds missing {', '.join(missing)} for {player_name}; filled with mock lines.")
    return merged


# --------------------------------------------------------------------------------------
# Step 4: true probability, odds conversion, and expected value
# --------------------------------------------------------------------------------------

def true_probability(projection: float, line: float, std_dev: float) -> dict[str, float]:
    """P(Over) and P(Under) for `line`, modeling the actual stat as Normal
    with mean `projection` and standard deviation `std_dev`.

    PTS/AST/REB are integers, so a whole-number line ("hook") carries real
    push risk right at that value, and even a half-point line deserves a
    little padding so a razor-thin miss doesn't get priced as a sure thing.
    Evaluating the Over at line + 0.5 and the Under at line - 0.5 is a
    continuity correction that keeps both sides honest about that gap
    instead of treating the boundary as knife-edge certain.
    """
    return {
        "Over": float(1 - norm.cdf(line + 0.5, loc=projection, scale=std_dev)),
        "Under": float(norm.cdf(line - 0.5, loc=projection, scale=std_dev)),
    }


def american_odds_to_decimal(odds: int) -> float:
    """Convert American odds (e.g. -110, +130) to a decimal payout multiplier --
    what a $1 stake returns in total (stake back + profit) if the bet wins.
    """
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)


def calculate_ev(true_prob: float, decimal_odds: float) -> float:
    """Expected return per $1 staked, as a fraction: (true_prob * payout) - 1."""
    return (true_prob * decimal_odds) - 1


def compute_ev_leaderboard(projection_result: dict, lines: dict[str, dict]) -> list[dict]:
    """Price every side (Over/Under) of every prop and keep only the bets whose
    EV clears EV_THRESHOLD_PCT, sorted by EV descending -- highest expected
    value first, regardless of how big the raw projection-vs-line gap was.
    """
    rows = []
    for prop_type, projection in projection_result["projections"].items():
        line_info = lines[prop_type]
        line = line_info["line"]
        probabilities = true_probability(projection, line, STD_DEV_BY_PROP[prop_type])

        for side, odds_key in (("Over", "over_odds"), ("Under", "under_odds")):
            odds = line_info.get(odds_key)
            if odds is None:
                continue

            ev_pct = calculate_ev(probabilities[side], american_odds_to_decimal(odds)) * 100
            if ev_pct <= EV_THRESHOLD_PCT:
                continue

            rows.append(
                {
                    "player_name": projection_result["player_name"],
                    "prop_type": prop_type,
                    "side": side,
                    "line": line,
                    "odds": odds,
                    "true_prob_pct": probabilities[side] * 100,
                    "ev_pct": ev_pct,
                }
            )

    rows.sort(key=lambda row: row["ev_pct"], reverse=True)
    return rows


def print_ev_leaderboard(rows: list[dict]) -> None:
    if not rows:
        print(f"No bets clear the {EV_THRESHOLD_PCT:.1f}% EV threshold.")
        return

    header = f"{'Player':<20}{'Prop':<12}{'Line':>7}{'Odds':>7}{'True Prob%':>12}{'EV%':>8}"
    print(header)
    print("-" * len(header))
    for row in rows:
        prop_label = f"{row['prop_type']} {row['side']}"
        print(
            f"{row['player_name']:<20}{prop_label:<12}{row['line']:>7.1f}{row['odds']:>+7}"
            f"{row['true_prob_pct']:>11.1f}%{row['ev_pct']:>7.1f}%"
        )


# --------------------------------------------------------------------------------------
# Step 5: batch player extraction + the slate-wide loop
# --------------------------------------------------------------------------------------

def get_players_scheduled_today(
    matchups: dict[int, int], db_path: Path = DB_PATH, table: str = TABLE_NAME
) -> list[int]:
    """Every tracked PLAYER_ID whose most recently known team is a key in
    `matchups` -- i.e. actively scheduled to play on the target date.

    Pulls every player's history in one bulk query and takes each player's
    last row in pandas, rather than one query per player -- with 500+ tracked
    players, hundreds of round trips would be needless overhead for data this
    small. "Last known team" is the same derivation used everywhere else in
    this module: the first token of a player's most recent MATCHUP string.
    """
    query = f"SELECT PLAYER_ID, GAME_DATE, MATCHUP FROM {table} ORDER BY GAME_DATE"
    with sqlite3.connect(db_path) as conn:
        history = pd.read_sql(query, conn, parse_dates=["GAME_DATE"])

    latest_per_player = history.groupby("PLAYER_ID", as_index=False).tail(1)
    team_abbr_to_id = _team_abbr_to_id()

    scheduled_player_ids = []
    for _, row in latest_per_player.iterrows():
        own_team_id = team_abbr_to_id.get(row["MATCHUP"].split()[0])
        if own_team_id is not None and own_team_id in matchups:
            scheduled_player_ids.append(int(row["PLAYER_ID"]))

    print(f"{len(scheduled_player_ids)} tracked players are on a team scheduled to play today.")
    return scheduled_player_ids


def process_player(
    player_id: int,
    models: dict[str, object],
    matchups: dict[int, int],
    game_date: str,
    live_odds: dict[str, dict] | None,
) -> list[dict]:
    """Run the full pipeline for one player and return their qualifying
    (EV > EV_THRESHOLD_PCT) leaderboard rows -- or an empty list if anything
    about this player couldn't be resolved.

    Never raises: missing rolling-average history, no cached opponent stats,
    no odds available for them (live or mock), or any other per-player gap is
    caught and logged, so one bad player doesn't take down a batch run over
    an entire slate. PlayerNotPlayingTodayError gets its own clean one-line
    message since it's an expected, common case (e.g. an explicit --player
    whose team has the night off); anything else is a genuine data problem
    worth a fuller message.
    """
    try:
        projection_result = project_player_live(player_id, models, matchups, game_date)
    except PlayerNotPlayingTodayError as exc:
        print(f"  Skipped: {exc}")
        return []
    except Exception as exc:
        print(f"  Skipped PLAYER_ID={player_id}: {exc!r}")
        return []

    try:
        lines = resolve_lines_for_player(player_id, projection_result["player_name"], live_odds)
        return compute_ev_leaderboard(projection_result, lines)
    except Exception as exc:
        print(f"  Skipped {projection_result['player_name']}: {exc!r}")
        return []


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--player",
        default=None,
        help="Full player name. Omit to scan every tracked player scheduled to play today.",
    )
    parser.add_argument(
        "--game-date",
        default=None,
        help=(
            "Override 'today' for testing, 'YYYY-MM-DD' (defaults to the real "
            "current date). Useful since there's no live NBA action outside the season."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    game_date = args.game_date or date.today().strftime("%Y-%m-%d")

    matchups = get_todays_matchups(game_date)
    models = load_or_train_models()

    # One (file-cached) live-odds fetch for the whole slate, shared across
    # every player in the loop below -- re-fetching per player would multiply
    # the Odds API call count by however many players are being scanned, on
    # top of what the cache already saves across separate runs of this script.
    live_odds = fetch_live_odds()

    if args.player:
        player_ids = [get_player_id(args.player)]
    else:
        player_ids = get_players_scheduled_today(matchups)

    master_leaderboard: list[dict] = []
    for i, player_id in enumerate(player_ids, start=1):
        if not args.player:
            print(f"[{i}/{len(player_ids)}] Processing PLAYER_ID={player_id}...")
        master_leaderboard.extend(process_player(player_id, models, matchups, game_date, live_odds))

    master_leaderboard.sort(key=lambda row: row["ev_pct"], reverse=True)

    print(f"\nSlate-wide betting board for {game_date} ({len(player_ids)} players scanned):")
    print_ev_leaderboard(master_leaderboard)


if __name__ == "__main__":
    main()
