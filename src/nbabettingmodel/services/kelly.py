"""Fractional Kelly staking math for sizing +EV bets against a bankroll.

Extracted as its own service function (rather than left inline, the way
dashboard.py's Top 10 & Staking tab still has it) so the /api/v1/top-picks
endpoint can reuse the exact same math without duplicating it. dashboard.py's
own copy is untouched -- consolidating it onto this module would be a
reasonable follow-up, not done here to keep this change scoped to the API.

Usage:
    uv run python -m nbabettingmodel.services.kelly
"""

from __future__ import annotations

from nbabettingmodel.services.daily_edge_runner import american_odds_to_decimal


def calculate_kelly_stake(
    true_prob_pct: float, odds: int, bankroll: float, fractional_kelly: float
) -> float:
    """Recommended wager in dollars for one bet, sized via fractional Kelly.

    Full Kelly fraction is f_star = (b*p - q) / b, where b = decimal_odds - 1,
    p = true probability, q = 1 - p. Multiplying by `fractional_kelly` (e.g.
    0.25 for quarter-Kelly) scales that down before applying it to `bankroll`.
    Clamped to [0, 1] as a fraction of bankroll: a genuinely +EV bet's full
    Kelly fraction is mathematically positive by construction, but a heavily
    -juiced edge case (very short odds, near-certain outcome) could otherwise
    push it past 100% of bankroll, which is never a sane recommendation for a
    single leg.
    """
    decimal_odds = american_odds_to_decimal(odds)
    p = true_prob_pct / 100
    q = 1 - p
    b = decimal_odds - 1
    f_star = (b * p - q) / b

    stake_fraction = min(max(f_star * fractional_kelly, 0.0), 1.0)
    return stake_fraction * bankroll


def build_top_picks(
    leaderboard: list[dict],
    min_edge: float = 3.0,
    bankroll: float = 1000.0,
    fractional_kelly: float = 0.25,
    top_n: int = 10,
) -> list[dict]:
    """The `top_n` highest-EV bets on `leaderboard` with at least `min_edge`%
    EV, each annotated with a `recommended_wager` sized via fractional Kelly.

    Each row's stake is sized as if it were the *only* bet placed -- Kelly
    sizing assumes one independent wager against the full bankroll. Staking
    all `top_n` picks simultaneously at the sizes shown will often commit far
    more than 100% of the bankroll; a caller displaying this list should
    surface that (see dashboard.py's Top 10 & Staking tab for the exact
    warning wording).
    """
    strong_picks = [bet for bet in leaderboard if bet["ev_pct"] >= min_edge]
    strong_picks.sort(key=lambda bet: bet["ev_pct"], reverse=True)
    strong_picks = strong_picks[:top_n]

    picks = []
    for bet in strong_picks:
        wager = calculate_kelly_stake(bet["true_prob_pct"], bet["odds"], bankroll, fractional_kelly)
        picks.append({**bet, "recommended_wager": round(wager, 2)})
    return picks
