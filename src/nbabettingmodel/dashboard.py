"""Streamlit front end for the NBA +EV betting engine.

Data flow
---------
1. streak_analyzer.get_hottest_streaks() -> the market-adjusted hot-streak
   pipeline (Tab 1), reused here rather than reimplemented.

2. parlay_builder.build_slate_leaderboard() -> the same slate-wide +EV scan
   daily_edge_runner.py's batch mode runs and prints at the command line
   (Tab 2), reused here rather than reimplemented. It loads the four
   SGDRegressor pipelines via edge_detector.load_or_train_models() (persisted
   models/*.joblib, refit only if a file is missing or won't load -- see
   data_sync.py, which is what actually keeps them current), hits the live
   NBA schedule endpoint, and pulls real market odds via live_odds_client.py
   (file-cached, 30-minute TTL, falling back to mock lines only if no live
   odds are available at all) -- still network-bound even with models loaded
   from disk, so both tabs' underlying computations are cached for a few
   minutes rather than redone on every widget interaction (a Streamlit script
   reruns top-to-bottom on almost any user action). load_dotenv() runs at the
   top of this file so ODDS_API_KEY from a local .env file is available
   before anything below ever asks for it.

3. parlay_builder.build_parlay() -> the same greedy, one-leg-per-player parlay
   construction the CLI tool uses (Tab 3), fed the *same cached* leaderboard
   from Tab 2's load_ev_board() rather than re-scanning the slate -- clicking
   "Generate Parlay Ticket" is a fast, local operation as long as that date's
   scan is already cached.

4. daily_edge_runner.project_player_live() -> the same real-matchup,
   real-rest-days projection pipeline the batch scan uses per player (Tab 4),
   run here for one player at a time instead of the whole slate.
   get_players_scheduled_today() gates whether the selected player even has a
   game on the selected date before the (slower) projection call runs.

5. Kelly staking (Tab 5) -> the same cached leaderboard from Tab 2's
   load_ev_board(), filtered to the slate's 10 strongest picks (>=3.0% EV)
   and sized with fractional Kelly staking. No new backend module: the Kelly
   math is small enough to live directly in this tab, using only the
   odds/probability fields the leaderboard already carries.

6. bet_tracker.py (Tab 6) -> a plain bet ledger stored in its own
   `bet_history` table, unrelated to the model/backtest pipeline. Not cached:
   it's a small, purely local table and a user's own edits (logging a bet,
   resolving a pending one) need to show up immediately, not sit behind a
   5-minute cache window like the network-bound tabs above.
     * Tab 5's "Log Top 10 Picks to Bet Tracker" button bulk-writes the
       displayed wager table straight into bet_history via
       bet_tracker.log_top_10_picks(), so a strong pick can go from "the
       model found this" to "I'm tracking this bet" in one click.
     * Tab 6's "Auto-Grade Pending Bets with Box Scores" button calls
       bet_tracker.auto_grade_pending_bets(), which compares every Pending
       bet's line/side against the real completed box score in Player_Stats
       -- no manual "did this hit?" judgment needed once a game is over.

Usage:
    uv run streamlit run src/nbabettingmodel/dashboard.py
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from nbabettingmodel.services.bet_tracker import (
    auto_grade_pending_bets,
    calculate_pnl_stats,
    load_bet_history,
    log_bet,
    log_top_10_picks,
    update_bet_result,
)
from nbabettingmodel.services.daily_edge_runner import (
    PlayerNotPlayingTodayError,
    american_odds_to_decimal,
    get_players_scheduled_today,
    get_todays_matchups,
    load_or_train_models,
    project_player_live,
)
from nbabettingmodel.services.edge_detector import PROP_MODULES, resolve_lines_for_player
from nbabettingmodel.services.live_odds_client import get_cache_last_updated
from nbabettingmodel.services.parlay_builder import (
    build_parlay,
    build_slate_leaderboard,
    decimal_to_american_odds,
)
from nbabettingmodel.services.player_stats import DB_PATH, TABLE_NAME
from nbabettingmodel.services.streak_analyzer import get_hottest_streaks

# Belt-and-suspenders: live_odds_client.py also calls load_dotenv() itself
# (it's the module that actually reads ODDS_API_KEY, and it's used directly
# by daily_edge_runner.py's CLI entry point too, not just this dashboard),
# but this app should guarantee its own startup loads the key regardless of
# import order elsewhere. Idempotent -- calling it twice is harmless.
load_dotenv()

st.set_page_config(page_title="NBA +EV Betting Engine", layout="wide")

st.title("NBA +EV Betting Engine")

_odds_last_updated = get_cache_last_updated()
if _odds_last_updated is not None:
    st.caption(
        f"Odds last updated: {_odds_last_updated.strftime('%Y-%m-%d %H:%M UTC')} "
        "(live market data, refreshed at most every 30 minutes)"
    )
else:
    st.caption("Odds last updated: never -- no live odds cache yet, using mock lines as a fallback.")

# Tab 5 configuration.
STRONG_PICK_MIN_EV = 3.0
KELLY_MULTIPLIER_OPTIONS = {
    "Quarter (0.25x)": 0.25,
    "Half (0.50x)": 0.50,
    "Full (1.0x)": 1.0,
}


@st.cache_data(ttl=300)
def load_hottest_streaks() -> pd.DataFrame:
    return get_hottest_streaks()


@st.cache_data(ttl=300)
def load_ev_board(game_date: str) -> pd.DataFrame:
    """Run daily_edge_runner.py's full batch pipeline for `game_date` (via
    parlay_builder.build_slate_leaderboard, which wraps it) and return it as
    a DataFrame. Cached for 5 minutes so adjusting the filters below doesn't
    re-run the whole slate-wide scan.
    """
    leaderboard = build_slate_leaderboard(game_date)
    return pd.DataFrame(leaderboard)


@st.cache_data(ttl=3600)
def load_tracked_players() -> pd.DataFrame:
    """Every player in Player_Stats, for the explorer tab's player picker."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql(
            f"SELECT DISTINCT PLAYER_ID, PLAYER_NAME FROM {TABLE_NAME} ORDER BY PLAYER_NAME", conn
        )


@st.cache_data(ttl=300)
def load_matchups(game_date: str) -> dict[int, int]:
    return get_todays_matchups(game_date)


@st.cache_data(ttl=300)
def load_scheduled_player_ids(game_date: str) -> list[int]:
    return get_players_scheduled_today(load_matchups(game_date))


@st.cache_data(ttl=600)
def load_trained_models() -> dict[str, object]:
    """Load the four persisted model pipelines once per cache window (via
    load_or_train_models(), which itself prefers models/*.joblib over
    refitting), reused across every player looked up in the explorer tab so
    switching players doesn't even repeat that disk read.
    """
    return load_or_train_models()


@st.cache_data(ttl=300)
def load_player_recent_games(player_id: int, lookback: int = 10) -> pd.DataFrame:
    """This player's most recent `lookback` games (PTS/REB/AST), returned
    oldest-first so the trend chart reads left-to-right chronologically.
    """
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql(
            f"SELECT GAME_DATE, PTS, REB, AST FROM {TABLE_NAME} WHERE PLAYER_ID = ? "
            f"ORDER BY GAME_DATE DESC LIMIT ?",
            conn,
            params=(player_id, lookback),
            parse_dates=["GAME_DATE"],
        )
    return df.sort_values("GAME_DATE").reset_index(drop=True)


@st.cache_data(ttl=300)
def load_player_lines(player_id: int, player_name: str, api_key: str | None) -> dict:
    """One player's live market lines (falling back to mock -- see
    edge_detector.resolve_lines_for_player), cached for 5 minutes. The
    underlying fetch_live_odds() call is already file-cached for 30 minutes,
    but this avoids a disk read and an availability check on every rerun in
    between.
    """
    return resolve_lines_for_player(player_id, player_name, api_key)


def render_parlay_ticket(parlay: dict, target_american_odds: int) -> None:
    """Render a finished (or unreachable) parlay -- shared by both the
    initial button click and any later rerun that just redisplays the last
    result from st.session_state.
    """
    if not parlay["reached_target"]:
        if not parlay["legs"]:
            st.warning(
                f"No positive-EV bets were available for this slate; "
                f"target odds of {target_american_odds:+d} is unreachable."
            )
        else:
            reached = decimal_to_american_odds(parlay["decimal_odds"])
            st.warning(
                f"Target odds of {target_american_odds:+d} couldn't be reached with "
                f"today's positive-EV slate -- ran out of independent legs after "
                f"{len(parlay['legs'])}, reaching only {reached:+d}."
            )
        return

    combined_american = decimal_to_american_odds(parlay["decimal_odds"])
    combined_ev_pct = (parlay["true_prob"] * parlay["decimal_odds"] - 1) * 100

    metric_cols = st.columns(3)
    metric_cols[0].metric("Combined Odds", f"{combined_american:+d}")
    metric_cols[1].metric("Combined True Probability", f"{parlay['true_prob'] * 100:.1f}%")
    metric_cols[2].metric("Total Ticket EV%", f"{combined_ev_pct:+.1f}%")

    st.subheader(f"{len(parlay['legs'])}-Leg Ticket")

    legs_df = pd.DataFrame(parlay["legs"])
    legs_display = pd.DataFrame(
        {
            "Player": legs_df["player_name"],
            "Prop": legs_df["prop_type"],
            "Direction & Line": [
                f"{side} {line:.1f}" for side, line in zip(legs_df["side"], legs_df["line"])
            ],
            "Odds": legs_df["odds"],
            "Individual EV%": legs_df["ev_pct"],
        }
    )

    st.dataframe(
        legs_display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Odds": st.column_config.NumberColumn(format="%+d"),
            "Individual EV%": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "Hottest Streaks",
        "Daily +EV Board",
        "Parlay Generator",
        "Player Explorer",
        "Top 10 & Staking",
        "PnL Tracker",
    ]
)

# --------------------------------------------------------------------------------------
# Tab 1: Hottest Market-Adjusted Streaks
# --------------------------------------------------------------------------------------

with tab1:
    st.header("Hottest Market-Adjusted Streaks (Last 10 Games)")
    st.caption(
        "Players hitting a standard prop threshold in 8 of their last 10 games or "
        "better, filtered to lines the market would still let you bet (better than -300)."
    )

    streaks_df = load_hottest_streaks()

    if streaks_df.empty:
        st.info("No market-playable streaks found right now.")
    else:
        st.dataframe(streaks_df, use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------------------
# Tab 2: Daily +EV Board
# --------------------------------------------------------------------------------------

with tab2:
    st.header("Daily +EV Board")
    st.caption(
        "Every prop bet across the slate whose model-implied true probability "
        "beats the sportsbook price, sorted by expected value."
    )

    control_cols = st.columns(3)
    with control_cols[0]:
        # Defaults to a real historical slate -- there's no live NBA action
        # outside the season, so this is the date to test against offline.
        selected_date = st.date_input("Slate date", value=date(2026, 4, 12))
    with control_cols[1]:
        min_ev_pct = st.slider("Minimum EV%", min_value=0.0, max_value=15.0, value=2.0, step=0.5)
    with control_cols[2]:
        selected_props = st.multiselect(
            "Prop types",
            options=["PTS", "AST", "REB", "PRA"],
            default=["PTS", "AST", "REB", "PRA"],
        )

    st.caption(
        "Note: the underlying scan only keeps bets at 2.0% EV or better, so "
        "lowering this slider below 2.0% won't surface additional bets."
    )

    game_date_str = selected_date.strftime("%Y-%m-%d")

    with st.spinner("Scanning slate for +EV bets..."):
        ev_board_df = load_ev_board(game_date_str)

    if ev_board_df.empty:
        st.info(f"No positive-EV bets found for {game_date_str}.")
    else:
        filtered_df = ev_board_df[
            (ev_board_df["ev_pct"] >= min_ev_pct) & (ev_board_df["prop_type"].isin(selected_props))
        ].sort_values("ev_pct", ascending=False)

        if filtered_df.empty:
            st.info("No bets match the current filters.")
        else:
            display_df = filtered_df.rename(
                columns={
                    "player_name": "Player",
                    "prop_type": "Prop",
                    "side": "Side",
                    "line": "Line",
                    "odds": "Odds",
                    "true_prob_pct": "True Prob%",
                    "ev_pct": "EV%",
                }
            )[["Player", "Prop", "Side", "Line", "Odds", "True Prob%", "EV%"]]

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Odds": st.column_config.NumberColumn(format="%+d"),
                    "True Prob%": st.column_config.NumberColumn(format="%.1f%%"),
                    "EV%": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
            st.caption(f"{len(filtered_df)} of {len(ev_board_df)} scanned bets shown.")

# --------------------------------------------------------------------------------------
# Tab 3: Parlay Generator
# --------------------------------------------------------------------------------------

with tab3:
    st.header("Parlay Generator")
    st.caption(
        "Greedily builds a parlay from the highest-EV bets on the slate, one leg per "
        "player, stopping the moment the combined odds clear your target."
    )

    parlay_control_cols = st.columns(2)
    with parlay_control_cols[0]:
        odds_presets = ["+100", "+300", "+500", "+1000", "Custom"]
        preset_choice = st.selectbox("Target Odds", odds_presets, index=3)
        if preset_choice == "Custom":
            target_odds_str = st.text_input("Custom target odds (e.g. +750, -150)", value="+750")
        else:
            target_odds_str = preset_choice
    with parlay_control_cols[1]:
        # A separate widget from Tab 2's date input (different key) -- picking
        # the same date here reuses that tab's cached scan; a different date
        # triggers a fresh one.
        parlay_date = st.date_input("Slate date", value=date(2026, 4, 12), key="parlay_slate_date")

    generate_clicked = st.button("Generate Parlay Ticket", type="primary")

    if generate_clicked:
        try:
            target_american_odds = int(target_odds_str)
            if target_american_odds == 0:
                raise ValueError
            st.session_state["parlay_odds_error"] = None
        except ValueError:
            target_american_odds = None
            st.session_state["parlay_odds_error"] = (
                f"'{target_odds_str}' isn't a valid American odds value (e.g. +500, -150; not 0)."
            )

        if target_american_odds is not None:
            parlay_date_str = parlay_date.strftime("%Y-%m-%d")
            target_decimal_odds = american_odds_to_decimal(target_american_odds)

            with st.spinner("Building parlay ticket..."):
                # Reuses Tab 2's cached leaderboard -- a cache hit if this is
                # the same date already scanned there, a fresh (slow) scan
                # only if it isn't.
                leaderboard_df = load_ev_board(parlay_date_str)
                leaderboard = leaderboard_df.sort_values("ev_pct", ascending=False).to_dict(
                    "records"
                )
                parlay = build_parlay(leaderboard, target_decimal_odds)

            st.session_state["parlay_result"] = parlay
            st.session_state["parlay_target_odds"] = target_american_odds

    if st.session_state.get("parlay_odds_error"):
        st.error(st.session_state["parlay_odds_error"])
    elif st.session_state.get("parlay_result") is not None:
        render_parlay_ticket(
            st.session_state["parlay_result"], st.session_state["parlay_target_odds"]
        )
    else:
        st.info("Set a target and click \"Generate Parlay Ticket\" to build a ticket.")

# --------------------------------------------------------------------------------------
# Tab 4: Player Explorer
# --------------------------------------------------------------------------------------

with tab4:
    st.header("Player Explorer")
    st.caption(
        "One player's real matchup context, model projections vs. live market lines, "
        "and recent form heading into a specific game date."
    )

    players_df = load_tracked_players()

    explorer_cols = st.columns(2)
    with explorer_cols[0]:
        selected_player_name = st.selectbox(
            "Player", players_df["PLAYER_NAME"], key="explorer_player"
        )
    with explorer_cols[1]:
        # Its own widget (distinct key) from Tab 2/3's date inputs -- picking
        # the same date still benefits from those tabs' caches where the
        # underlying calls overlap (get_todays_matchups, model training).
        explorer_date = st.date_input(
            "Game date", value=date(2026, 4, 12), key="explorer_game_date"
        )

    selected_player_id = int(
        players_df.loc[players_df["PLAYER_NAME"] == selected_player_name, "PLAYER_ID"].iloc[0]
    )
    explorer_date_str = explorer_date.strftime("%Y-%m-%d")

    with st.spinner(f"Loading {selected_player_name}'s matchup..."):
        scheduled_player_ids = load_scheduled_player_ids(explorer_date_str)

        view = None
        explorer_warning = None
        if selected_player_id not in scheduled_player_ids:
            explorer_warning = (
                f"{selected_player_name} doesn't appear to have a game scheduled "
                f"on {explorer_date_str}."
            )
        else:
            matchups = load_matchups(explorer_date_str)
            models = load_trained_models()
            try:
                view = project_player_live(selected_player_id, models, matchups, explorer_date_str)
            except (PlayerNotPlayingTodayError, ValueError) as exc:
                # Belt-and-suspenders: load_scheduled_player_ids already checked
                # this above, but project_player_live does its own independent
                # resolution (and can also fail if this player has zero games
                # recorded on or before the selected date), so both are handled.
                explorer_warning = str(exc)

    if explorer_warning:
        st.warning(explorer_warning)
    elif view is not None:
        st.subheader(f"{view['player_name']} vs. {view['opponent_abbr']}  ({explorer_date_str})")

        st.markdown("**Opponent Context**")
        context_cols = st.columns(3)
        context_cols[0].metric("Opponent Def Rating", f"{view['opp_def_rating']:.1f}")
        context_cols[1].metric("Opponent Reb%", f"{view['opp_reb_pct'] * 100:.1f}%")
        context_cols[2].metric("Opponent Ast%", f"{view['opp_ast_pct'] * 100:.1f}%")

        st.markdown("**Projections vs. Live Market Lines**")
        api_key = os.environ.get("ODDS_API_KEY")
        player_lines = load_player_lines(selected_player_id, view["player_name"], api_key)
        comparison_df = pd.DataFrame(
            [
                {
                    "Prop": prop_type,
                    "Model Projection": round(view["projections"][prop_type], 1),
                    "Market Line": player_lines[prop_type]["line"],
                    "Delta": round(view["projections"][prop_type] - player_lines[prop_type]["line"], 1),
                }
                for prop_type in PROP_MODULES
            ]
        )
        st.dataframe(comparison_df, use_container_width=True, hide_index=True)

        st.markdown("**Last 10 Games -- Actual PTS / REB / AST**")
        recent_games_df = load_player_recent_games(selected_player_id, lookback=10)
        if recent_games_df.empty:
            st.info("No recent game log available for this player.")
        else:
            chart_df = recent_games_df.set_index("GAME_DATE")[["PTS", "REB", "AST"]]
            st.line_chart(chart_df)

# --------------------------------------------------------------------------------------
# Tab 5: Top 10 & Staking
# --------------------------------------------------------------------------------------

with tab5:
    st.header("Top 10 & Staking")
    st.caption(
        f"The slate's 10 highest-EV bets (at least {STRONG_PICK_MIN_EV:.1f}% EV), sized with "
        "fractional Kelly staking against your bankroll."
    )
    st.caption(
        "Each row's stake is sized as if it were the *only* bet placed -- Kelly sizing "
        "assumes one independent wager against your full bankroll. Placing all 10 at "
        "the sizes shown simultaneously will very likely commit more than 100% of your "
        "bankroll; watch the total below before staking multiple picks at once."
    )

    staking_cols = st.columns(3)
    with staking_cols[0]:
        top10_date = st.date_input("Slate date", value=date(2026, 4, 12), key="top10_slate_date")
    with staking_cols[1]:
        bankroll = st.number_input(
            "Current Bankroll ($)", min_value=0.0, value=1000.0, step=100.0
        )
    with staking_cols[2]:
        kelly_choice = st.selectbox(
            "Kelly Multiplier", list(KELLY_MULTIPLIER_OPTIONS.keys()), index=0
        )
    kelly_multiplier = KELLY_MULTIPLIER_OPTIONS[kelly_choice]

    top10_date_str = top10_date.strftime("%Y-%m-%d")

    with st.spinner("Scanning slate for strong +EV picks..."):
        ev_board_df = load_ev_board(top10_date_str)

    strong_picks_df = (
        ev_board_df[ev_board_df["ev_pct"] >= STRONG_PICK_MIN_EV]
        .sort_values("ev_pct", ascending=False)
        .head(10)
    )

    if strong_picks_df.empty:
        st.info(f"No strong picks met the {STRONG_PICK_MIN_EV:.1f}% EV threshold today.")
    else:
        wager_rows = []
        for _, row in strong_picks_df.iterrows():
            decimal_odds = american_odds_to_decimal(int(row["odds"]))
            p = row["true_prob_pct"] / 100
            q = 1 - p
            b = decimal_odds - 1
            f_star = (b * p - q) / b

            # A genuinely +EV bet's full Kelly fraction is mathematically
            # equal to EV_fraction / b, so it stays positive here by
            # construction (EV% >= 3.0 was already required above) -- this
            # clamp is a sanity ceiling, not a normal-path correction, so a
            # heavily-juiced edge case never recommends staking more than the
            # whole bankroll on a single leg.
            stake_fraction = min(max(f_star * kelly_multiplier, 0.0), 1.0)
            recommended_wager = stake_fraction * bankroll

            wager_rows.append(
                {
                    "Player": row["player_name"],
                    "Prop": row["prop_type"],
                    "Side": row["side"],
                    "Line": row["line"],
                    "Odds": row["odds"],
                    "EV%": row["ev_pct"],
                    "Recommended Wager ($)": recommended_wager,
                }
            )

        wager_df = pd.DataFrame(wager_rows)

        st.dataframe(
            wager_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Odds": st.column_config.NumberColumn(format="%+d"),
                "EV%": st.column_config.NumberColumn(format="%.1f%%"),
                "Recommended Wager ($)": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
        total_recommended = wager_df["Recommended Wager ($)"].sum()
        total_summary = (
            f"Kelly multiplier: {kelly_choice} | Bankroll: ${bankroll:,.2f} | "
            f"Total recommended across all picks: ${total_recommended:,.2f}"
        )
        if total_recommended > bankroll:
            st.warning(f"{total_summary} -- exceeds your full bankroll if staked simultaneously.")
        else:
            st.caption(total_summary)

        if st.button(
            "Log Top 10 Picks to Bet Tracker", type="primary", key="log_top10_button"
        ):
            inserted = log_top_10_picks(wager_df, top10_date_str)
            st.success(
                f"Logged {inserted} Top 10 pick(s) to the Bet Tracker (Tab 6) as Pending, "
                f"using the wager amounts shown above."
            )

# --------------------------------------------------------------------------------------
# Tab 6: PnL Tracker
# --------------------------------------------------------------------------------------

with tab6:
    st.header("PnL Tracker")
    st.caption(
        "A plain ledger of bets you've actually logged -- separate from the model/backtest "
        "pipeline above. Not cached, so logging a bet or resolving one updates immediately."
    )

    if st.button(
        "Auto-Grade Pending Bets with Box Scores", type="primary", key="auto_grade_button"
    ):
        with st.spinner("Grading pending bets against completed box scores..."):
            graded_count = auto_grade_pending_bets()
        st.success(f"Graded {graded_count} bet(s). Bets whose game hasn't been played yet stay Pending.")
        st.rerun()

    with st.expander("Log a new bet"):
        st.caption(
            "Player name must match Player_Stats exactly (including accents, e.g. "
            "'Nikola Jokić') for auto-grading to find the completed box score later."
        )
        with st.form("log_bet_form", clear_on_submit=True):
            log_cols = st.columns(4)
            with log_cols[0]:
                new_bet_date = st.date_input("Date", value=date(2026, 4, 12), key="new_bet_date")
                new_bet_player = st.text_input("Player name", key="new_bet_player")
            with log_cols[1]:
                new_bet_prop = st.selectbox("Prop", list(PROP_MODULES), key="new_bet_prop")
                new_bet_side = st.selectbox("Side", ["Over", "Under"], key="new_bet_side")
            with log_cols[2]:
                new_bet_line = st.number_input("Line", value=0.0, step=0.5, key="new_bet_line")
                new_bet_odds = st.number_input(
                    "Odds (American)", value=-110, step=5, key="new_bet_odds"
                )
            with log_cols[3]:
                new_bet_wager = st.number_input(
                    "Wager ($)", min_value=0.0, value=50.0, step=10.0, key="new_bet_wager"
                )
                new_bet_is_top10 = st.checkbox("This was a Top 10 pick", key="new_bet_is_top10")

            if st.form_submit_button("Log Bet"):
                if not new_bet_player.strip():
                    st.error("Player name is required.")
                else:
                    log_bet(
                        date=new_bet_date.strftime("%Y-%m-%d"),
                        player_name=new_bet_player.strip(),
                        prop=new_bet_prop,
                        side=new_bet_side,
                        line=new_bet_line,
                        odds=int(new_bet_odds),
                        wager_amount=new_bet_wager,
                        is_top_10=new_bet_is_top10,
                    )
                    st.success(f"Logged {new_bet_player} {new_bet_side} {new_bet_prop} {new_bet_line}.")
                    st.rerun()

    bet_history_df = load_bet_history()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Top 10 Picks")
        top10_bets_df = bet_history_df[bet_history_df["is_top_10"]]
        top10_stats = calculate_pnl_stats(top10_bets_df)

        top10_metric_cols = st.columns(2)
        top10_metric_cols[0].metric("Win/Loss %", f"{top10_stats['win_pct']:.1f}%")
        top10_metric_cols[1].metric(
            "Bankroll Up/Down ($)", f"${top10_stats['total_pnl']:+,.2f}"
        )
        st.caption(
            f"{top10_stats['wins']}W - {top10_stats['losses']}L - {top10_stats['pushes']}P - "
            f"{top10_stats['voids']}V - {top10_stats['pending']} pending "
            f"({top10_stats['total_bets']} total)"
        )

    with col2:
        st.subheader("Overall Bets")
        overall_stats = calculate_pnl_stats(bet_history_df)

        overall_metric_cols = st.columns(2)
        overall_metric_cols[0].metric("Win/Loss %", f"{overall_stats['win_pct']:.1f}%")
        overall_metric_cols[1].metric(
            "Bankroll Up/Down ($)", f"${overall_stats['total_pnl']:+,.2f}"
        )
        st.caption(
            f"{overall_stats['wins']}W - {overall_stats['losses']}L - {overall_stats['pushes']}P - "
            f"{overall_stats['voids']}V - {overall_stats['pending']} pending "
            f"({overall_stats['total_bets']} total)"
        )

    st.markdown("**Bet Ledger**")
    if bet_history_df.empty:
        st.info("No bets logged yet -- use \"Log a new bet\" above to get started.")
    else:
        ledger_display = bet_history_df.rename(
            columns={
                "id": "ID",
                "date": "Date",
                "player_name": "Player",
                "prop": "Prop",
                "side": "Side",
                "line": "Line",
                "odds": "Odds",
                "wager_amount": "Wager ($)",
                "is_top_10": "Top 10 Pick",
                "result": "Result",
                "pnl": "PnL ($)",
            }
        )
        st.dataframe(
            ledger_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Odds": st.column_config.NumberColumn(format="%+d"),
                "Wager ($)": st.column_config.NumberColumn(format="$%.2f"),
                "PnL ($)": st.column_config.NumberColumn(format="$%+.2f"),
            },
        )

        st.markdown("**Resolve a Pending Bet**")
        pending_df = bet_history_df[bet_history_df["result"] == "Pending"]
        if pending_df.empty:
            st.caption("No pending bets to resolve.")
        else:
            pending_options = {
                f"#{row.id} - {row.player_name} {row.side} {row.prop} {row.line} ({row.date})": row.id
                for row in pending_df.itertuples()
            }
            resolve_cols = st.columns(3)
            with resolve_cols[0]:
                selected_bet_label = st.selectbox(
                    "Pending bet", list(pending_options.keys()), key="resolve_bet_select"
                )
            with resolve_cols[1]:
                selected_result = st.selectbox(
                    "Result", ["Win", "Loss", "Push", "Void"], key="resolve_bet_result"
                )
            with resolve_cols[2]:
                st.write("")  # vertical spacer so the button aligns with the selectboxes
                if st.button("Update Result", key="resolve_bet_button"):
                    update_bet_result(pending_options[selected_bet_label], selected_result)
                    st.success(f"Marked {selected_bet_label} as {selected_result}.")
                    st.rerun()
