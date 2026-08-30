"""Greedily build a parlay ticket from the daily +EV leaderboard, targeting a
given American odds payout, with a one-leg-per-player independence constraint.

Data flow
---------
1. Reuses daily_edge_runner.py's existing batch pipeline verbatim (schedule
   matchups, model loading via load_or_train_models(), live/mock odds,
   per-player EV calculation) to build the exact same slate-wide +EV
   leaderboard that script prints -- this module only adds parlay
   construction on top of it, it doesn't reimplement any of the underlying
   odds/EV logic. load_or_train_models() (not a raw retrain) is what keeps a
   full-slate scan fast: it loads the four persisted model pipelines from
   models/ instead of refitting them from scratch on every call.

2. Greedy construction -> walk the leaderboard (already sorted by EV%
   descending) and add each leg to the parlay unless that player already has
   a leg in it. One prop per player keeps legs statistically independent --
   a player's PTS and REB in the same game are correlated (a big offensive
   night tends to lift both), and multiplying correlated probabilities
   together the way a parlay's combined odds assume independence would
   overstate the true combined probability. Combined decimal odds and
   combined true probability are running products, updated leg by leg; the
   loop stops the instant the combined odds clear the target.

3. Output -> the finished ticket's legs, combined American odds (converted
   back from the running decimal product), combined true probability, and
   the ticket's own EV -- the same EV = true_probability * decimal_payout - 1
   formula every individual leg already uses, just applied to the parlay as
   a whole instead of to one bet.

Usage:
    uv run python -m nbabettingmodel.services.parlay_builder --target-odds +500
    uv run python -m nbabettingmodel.services.parlay_builder --target-odds +1000 --game-date 2026-04-12
"""

from __future__ import annotations

import argparse
import os
from datetime import date

from nbabettingmodel.services.daily_edge_runner import (
    american_odds_to_decimal,
    get_players_scheduled_today,
    get_todays_matchups,
    load_or_train_models,
    process_player,
)
from nbabettingmodel.services.live_odds_client import fetch_live_odds

# --------------------------------------------------------------------------------------
# Odds conversion
# --------------------------------------------------------------------------------------

def decimal_to_american_odds(decimal_odds: float) -> int:
    """Inverse of daily_edge_runner.american_odds_to_decimal."""
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1) * 100)
    return round(-100 / (decimal_odds - 1))


# --------------------------------------------------------------------------------------
# Step 1: fetch the slate-wide +EV leaderboard
# --------------------------------------------------------------------------------------

def build_slate_leaderboard(
    game_date: str, models: dict[str, object] | None = None
) -> list[dict]:
    """Run daily_edge_runner.py's batch pipeline for `game_date` and return the
    full +EV leaderboard, sorted by EV% descending -- identical to what that
    script prints, just returned here instead of printed so a parlay can be
    built from it.

    `models` lets a caller that already has the four prop models loaded (e.g.
    the FastAPI app, which loads them once at startup into app.state) pass
    them straight through instead of this function loading its own copy via
    load_or_train_models() -- still the default when `models` is omitted, so
    every existing caller (the CLI tools, the dashboard) is unaffected.
    """
    matchups = get_todays_matchups(game_date)
    if models is None:
        models = load_or_train_models()

    api_key = os.environ.get("ODDS_API_KEY")
    live_odds = fetch_live_odds(api_key)

    player_ids = get_players_scheduled_today(matchups)

    leaderboard: list[dict] = []
    for i, player_id in enumerate(player_ids, start=1):
        print(f"[{i}/{len(player_ids)}] Processing PLAYER_ID={player_id}...")
        leaderboard.extend(process_player(player_id, models, matchups, game_date, live_odds))

    leaderboard.sort(key=lambda row: row["ev_pct"], reverse=True)
    return leaderboard


# --------------------------------------------------------------------------------------
# Step 2: greedy parlay construction
# --------------------------------------------------------------------------------------

def build_parlay(
    leaderboard: list[dict], target_decimal_odds: float, max_legs: int | None = None
) -> dict:
    """Greedily add the highest-EV bets to a parlay -- at most one leg per
    player, to keep legs independent -- until the combined decimal odds clear
    `target_decimal_odds`, the leaderboard runs out, or `max_legs` legs have
    been added, whichever comes first. `max_legs=None` (the default, and the
    only behavior before this parameter existed) means no cap -- every
    existing caller is unaffected.

    `leaderboard` is assumed already sorted by EV% descending (see
    build_slate_leaderboard); this function doesn't re-sort, so passing an
    unsorted list would produce a non-greedy result.
    """
    legs: list[dict] = []
    used_players: set[str] = set()
    current_decimal_odds = 1.0
    current_true_prob = 1.0

    for bet in leaderboard:
        if bet["player_name"] in used_players:
            continue  # one leg per player -- keep legs independent

        current_decimal_odds *= american_odds_to_decimal(bet["odds"])
        current_true_prob *= bet["true_prob_pct"] / 100
        legs.append(bet)
        used_players.add(bet["player_name"])

        if current_decimal_odds >= target_decimal_odds:
            break
        if max_legs is not None and len(legs) >= max_legs:
            break

    return {
        "legs": legs,
        "decimal_odds": current_decimal_odds,
        "true_prob": current_true_prob,
        "reached_target": current_decimal_odds >= target_decimal_odds,
    }


# --------------------------------------------------------------------------------------
# Step 3: output
# --------------------------------------------------------------------------------------

def print_parlay_ticket(parlay: dict, target_american_odds: int) -> None:
    if not parlay["reached_target"]:
        if not parlay["legs"]:
            print(
                f"\nNo positive-EV bets were available on this slate; "
                f"target odds of {target_american_odds:+d} is unreachable."
            )
        else:
            reached = decimal_to_american_odds(parlay["decimal_odds"])
            print(
                f"\nTarget odds of {target_american_odds:+d} is unreachable with today's "
                f"positive-EV slate -- ran out of independent legs after "
                f"{len(parlay['legs'])}, reaching only {reached:+d}."
            )
        return

    print(f"\n=== Parlay Ticket (target {target_american_odds:+d}) ===")
    print(f"{'#':<3}{'Player':<20}{'Prop':<12}{'Line':>7}{'Odds':>7}{'True Prob%':>12}")
    print("-" * 61)
    for i, leg in enumerate(parlay["legs"], start=1):
        prop_label = f"{leg['prop_type']} {leg['side']}"
        print(
            f"{i:<3}{leg['player_name']:<20}{prop_label:<12}{leg['line']:>7.1f}"
            f"{leg['odds']:>+7}{leg['true_prob_pct']:>11.1f}%"
        )

    combined_american = decimal_to_american_odds(parlay["decimal_odds"])
    combined_ev_pct = (parlay["true_prob"] * parlay["decimal_odds"] - 1) * 100

    print(f"\nLegs:                     {len(parlay['legs'])}")
    print(f"Final Combined Odds:      {combined_american:+d}  (decimal {parlay['decimal_odds']:.2f})")
    print(f"Final Combined True Prob: {parlay['true_prob'] * 100:.1f}%")
    print(f"Total Expected Value:     {combined_ev_pct:+.1f}%")


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target-odds",
        required=True,
        help="Target American odds for the parlay payout, e.g. +100 or +1000.",
    )
    parser.add_argument(
        "--game-date",
        default=None,
        help=(
            "Slate date 'YYYY-MM-DD' (defaults to today; there's no live NBA "
            "action outside the season, so pass a past date with real games to test)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    game_date = args.game_date or date.today().strftime("%Y-%m-%d")

    target_american_odds = int(args.target_odds)
    if target_american_odds == 0:
        raise ValueError("--target-odds cannot be 0; American odds are never quoted as 0.")
    target_decimal_odds = american_odds_to_decimal(target_american_odds)

    leaderboard = build_slate_leaderboard(game_date)
    print(f"\n{len(leaderboard)} +EV bets available on {game_date}.")

    parlay = build_parlay(leaderboard, target_decimal_odds)
    print_parlay_ticket(parlay, target_american_odds)


if __name__ == "__main__":
    main()
