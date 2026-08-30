"""Find players on a market-playable hot streak against standard prop thresholds.

Data flow
---------
1. sqlite3 -> pull every tracked player's most recent LOOKBACK_GAMES rows
   (PTS/REB/AST -- actual, already-played results). This is a backward-looking
   summary of what already happened, not a projection, so none of the
   lookahead-bias concerns the rest of this project cares about apply here.

2. pandas -> for each standard threshold (15/20/25 PTS, 5/8/10 AST, 5/8/10
   REB), count how many of a player's last LOOKBACK_GAMES games cleared it.
   An 80%+ hit rate (8 of 10 or better) qualifies as a "streak" and gates
   whether that (player, prop, threshold) makes it to the next step at all.

3. Market pricing -> for every qualifying streak, price the *same* threshold
   with a smoother model instead of inverting the noisy 10-game count
   directly. Inverting the raw count would be self-defeating: a hit rate of
   exactly 80% always implies fair odds of exactly -400 (that's just what a
   0.8 win probability means in American odds), so every streak that clears
   the 80% gate would automatically fail a -300 market filter, no exceptions
   -- the feature would always come up empty. Instead, each threshold is
   priced off a Normal distribution centered on the player's own recent
   average for that stat, with daily_edge_runner.py's backtested-MAE standard
   deviations -- the same model that script already uses for live EV pricing,
   reused here via true_probability(). That lets a threshold sitting close to
   a player's natural level price near a standard vig even when a noisy
   10-game sample happens to read 8/10, while a threshold deep in a player's
   comfort zone (e.g. a checkpoint far below their average) still prices as
   heavily juiced, the same way it would on a real board.

4. Market filter -> drop anything priced worse than MAX_JUICE (-300): a real
   streak, but not a reasonably playable bet.

5. Returns a pandas DataFrame ready for display: Player, Prop, Target Line,
   Hit Rate, Odds.

Usage:
    uv run python -m nbabettingmodel.services.streak_analyzer
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from nbabettingmodel.services.daily_edge_runner import STD_DEV_BY_PROP, true_probability
from nbabettingmodel.services.parlay_builder import decimal_to_american_odds
from nbabettingmodel.services.player_stats import DB_PATH, TABLE_NAME

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

LOOKBACK_GAMES = 10
MIN_HIT_RATE = 0.8  # 8 of 10 or better

STANDARD_THRESHOLDS = {
    "PTS": [15, 20, 25],
    "AST": [5, 8, 10],
    "REB": [5, 8, 10],
}

# A streak priced worse (more negative) than this is real, but not a
# reasonably playable bet -- too little payout for the risk.
MAX_JUICE = -300

# Sportsbook vig assumed when converting a probability into mock odds.
MOCK_VIG = 0.05


# --------------------------------------------------------------------------------------
# Step 1: last N games per player
# --------------------------------------------------------------------------------------

def load_recent_games(
    lookback: int = LOOKBACK_GAMES, db_path: Path = DB_PATH, table: str = TABLE_NAME
) -> pd.DataFrame:
    """Pull every tracked player's most recent `lookback` games (PTS/REB/AST),
    in one bulk query rather than one query per player.
    """
    query = f"SELECT PLAYER_ID, PLAYER_NAME, GAME_DATE, PTS, REB, AST FROM {table} ORDER BY GAME_DATE"
    with sqlite3.connect(db_path) as conn:
        history = pd.read_sql(query, conn, parse_dates=["GAME_DATE"])
    return history.groupby("PLAYER_ID", as_index=False, group_keys=False).tail(lookback)


# --------------------------------------------------------------------------------------
# Step 2: market pricing for a specific (player, prop, threshold)
# --------------------------------------------------------------------------------------

def probability_to_mock_odds(probability: float, vig: float = MOCK_VIG) -> int:
    """Convert a win probability into mock American odds, with a vig applied
    the way a real book's built-in edge shaves down the fair payout.
    """
    probability = min(max(probability, 1e-6), 1 - 1e-6)  # keep the conversion finite
    fair_decimal = 1 / probability
    vigged_decimal = 1 + (fair_decimal - 1) * (1 - vig)
    return decimal_to_american_odds(vigged_decimal)


def threshold_mock_odds(player_games: pd.DataFrame, prop_type: str, threshold: int) -> int:
    """Mock American 'Over' odds for clearing `threshold` in `prop_type`.

    Priced off a Normal distribution centered on the player's own recent
    average for this stat (mu), with daily_edge_runner.py's backtested-MAE
    standard deviation (sigma) -- the same live-EV pricing model, reused here
    via true_probability(). Passing `threshold - 1` as the "line" makes
    true_probability's Over side (P(X > line + 0.5)) equal P(X > threshold -
    0.5), i.e. P(X >= threshold) for an integer stat.
    """
    mu = player_games[prop_type].mean()
    sigma = STD_DEV_BY_PROP[prop_type]
    probability_over = true_probability(mu, threshold - 1, sigma)["Over"]
    return probability_to_mock_odds(probability_over)


# --------------------------------------------------------------------------------------
# Step 3: find qualifying streaks
# --------------------------------------------------------------------------------------

def find_streaks(
    recent_games: pd.DataFrame,
    thresholds: dict[str, list[int]] = STANDARD_THRESHOLDS,
    lookback: int = LOOKBACK_GAMES,
    min_hit_rate: float = MIN_HIT_RATE,
) -> list[dict]:
    """Every (player, prop, threshold) combination with an 80%+ hit rate over
    a player's last `lookback` games, priced but not yet market-filtered.

    Players with fewer than `lookback` games recorded are skipped entirely --
    "last 10 games" isn't a meaningful streak yet for someone with 4 games played.
    """
    streaks = []
    for player_id, player_games in recent_games.groupby("PLAYER_ID"):
        if len(player_games) < lookback:
            continue

        player_name = player_games["PLAYER_NAME"].iloc[-1]
        for prop_type, prop_thresholds in thresholds.items():
            for threshold in prop_thresholds:
                hits = int((player_games[prop_type] >= threshold).sum())
                hit_rate = hits / lookback
                if hit_rate < min_hit_rate:
                    continue

                streaks.append(
                    {
                        "player_id": int(player_id),
                        "player_name": player_name,
                        "prop_type": prop_type,
                        "threshold": threshold,
                        "hits": hits,
                        "games": lookback,
                        "hit_rate": hit_rate,
                        "odds": threshold_mock_odds(player_games, prop_type, threshold),
                    }
                )
    return streaks


# --------------------------------------------------------------------------------------
# Step 4: market filter + assembly
# --------------------------------------------------------------------------------------

def get_hottest_streaks(
    lookback: int = LOOKBACK_GAMES,
    min_hit_rate: float = MIN_HIT_RATE,
    max_juice: int = MAX_JUICE,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """The full pipeline: load recent games, find every qualifying streak,
    drop anything priced worse than `max_juice`, and return a display-ready
    DataFrame sorted by hit rate (best streaks first).
    """
    recent_games = load_recent_games(lookback, db_path)
    streaks = find_streaks(recent_games, STANDARD_THRESHOLDS, lookback, min_hit_rate)

    # "Worse than -300" means more negative (more juiced): -350 is worse than
    # -300, but -110 or +150 are both better (less juiced) than -300.
    playable = [s for s in streaks if s["odds"] >= max_juice]

    columns = ["Player", "Prop", "Target Line", "Hit Rate", "Odds"]
    if not playable:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(playable).sort_values(
        ["hit_rate", "player_name"], ascending=[False, True]
    )

    return pd.DataFrame(
        {
            "Player": df["player_name"].values,
            "Prop": df["prop_type"].values,
            "Target Line": [f"{t}+" for t in df["threshold"]],
            "Hit Rate": [f"{h}/{g}" for h, g in zip(df["hits"], df["games"])],
            "Odds": [f"{o:+d}" for o in df["odds"]],
        }
    ).reset_index(drop=True)


if __name__ == "__main__":
    result = get_hottest_streaks()
    print(f"Found {len(result)} market-playable hot streaks.")
    print(result.to_string(index=False))
