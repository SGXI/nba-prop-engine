"""Fetch an NBA player's game logs, build rolling-average features, and store them in SQLite.

Data flow
---------
1. `nba_api.stats.static.players`  -> local, offline lookup table of every player
   ever in the NBA. Maps a name like "LeBron James" to the numeric PLAYER_ID that
   every stats.nba.com endpoint requires. No HTTP request is made here.

2. `nba_api.stats.endpoints.PlayerGameLog` -> live call to stats.nba.com. It returns
   one row per game the player appeared in for a given season, with box-score
   columns (MIN, PTS, REB, AST, ...). Every nba_api endpoint object exposes
   `.get_data_frames()`, a list of DataFrames matching the "resultSets" the NBA API
   returned. PlayerGameLog has exactly one result set, so we take index [0].

3. `nba_api.stats.endpoints.CommonPlayerInfo` -> live call, one per player, to
   resolve their primary position (e.g. 'Forward', 'Guard-Forward'). Cheap and
   cached, since it never changes mid-pull.

4. pandas -> sort oldest-to-newest, then compute:
     * trailing 5/10-game and season-to-date averages for both counting stats
       (PTS, REB, AST, MIN) and shooting efficiency (FG_PCT, FG3_PCT, FT_PCT)
     * REST_DAYS, via GAME_DATE.diff()
     * OPP_DEF_RATING / OPP_AST_PCT / OPP_REB_PCT / OPP_FG_PCT_VS_POSITION, all
       as of the day before each game (see `LeagueDashTeamStats` below).

5. `nba_api.stats.endpoints.LeagueDashTeamStats` -> live call to stats.nba.com.
   Unlike PlayerGameLog, this is a *league-wide* endpoint: one call returns every
   team's aggregated stats for a date range in a single result set. Two distinct
   pulls off this same endpoint drive the opponent features:
     * `measure_type_detailed_defense='Advanced'` -> efficiency metrics
       (DEF_RATING, AST_PCT, REB_PCT, PACE, ...) for the opponent as a whole.
     * `measure_type_detailed_defense='Opponent'` + `player_position_abbreviation
       _nullable` -> box-score totals put up *specifically by opposing players at
       one position group* (G / F / C), which is how we isolate "FG% allowed to
       players at LeBron's position" instead of FG% allowed to anyone.
   Setting `date_to_nullable` and leaving `date_from_nullable` blank scopes each
   call to "season start through that date", which is what lets us pull a
   point-in-time snapshot instead of the final, full-season number. Combined with
   `nba_api.stats.static.teams` (another offline lookup, this time abbreviation ->
   TEAM_ID) this maps each game's opponent to their defensive form *at that time*.

6. sqlite3 -> persist to `nba_betting.db`, table `Player_Stats`.

Usage:
    uv run python -m nbabettingmodel.services.player_stats
    uv run python -m nbabettingmodel.services.player_stats --player "Nikola Jokic" --season 2023-24
    uv run python -m nbabettingmodel.services.player_stats --all   # every active player; see build_league_stats()
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import commonplayerinfo, leaguedashteamstats, playergamelog
from nba_api.stats.static import players, teams

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# Counting stats we model. Everything downstream keys off this list.
STAT_COLS = ["PTS", "REB", "AST", "MIN"]

# Shooting-efficiency stats. Rolled the same way as STAT_COLS (mean of the
# per-game percentage, not attempts-weighted) so a 2-for-2 night and a 10-for-10
# night both count as a single "100%" data point in the rolling window.
EFFICIENCY_COLS = ["FG_PCT", "FG3_PCT", "FT_PCT"]

# The combo-prop target (Points + Rebounds + Assists). Computed in
# clean_game_log() before add_rolling_averages() runs, so it's just another
# column to roll -- same shift(1)-lagged logic as everything else here.
COMBO_COLS = ["PRA"]

# Everything add_rolling_averages() computes trailing windows for by default.
ROLLING_STAT_COLS = STAT_COLS + EFFICIENCY_COLS + COMBO_COLS

# Trailing windows for form/recency features.
ROLLING_WINDOWS = (5, 10)

DB_PATH = Path(__file__).resolve().parents[3] / "nba_betting.db"
TABLE_NAME = "Player_Stats"

# stats.nba.com is slow and rate-limits aggressively. A generous timeout plus a
# browser-ish User-Agent (nba_api sets its own headers by default) avoids most
# spurious ReadTimeouts. Bump this if you start batching hundreds of players.
REQUEST_TIMEOUT = 60

# Pause between *new* (non-cached) LeagueDashTeamStats calls when building the
# opponent-defense features, which need calls per unique game date (and, for the
# position-specific pull, per unique date+position). stats.nba.com will start
# throwing timeouts/429s under rapid sequential hits.
OPPONENT_STATS_REQUEST_DELAY = 0.6

# Pause between players when looping over a full roster. Each player triggers at
# least one CommonPlayerInfo call and one PlayerGameLog call that a shared cache
# can't absorb (unlike the opponent-defense pulls), so a multi-hundred-player run
# needs its own throttle on top of OPPONENT_STATS_REQUEST_DELAY.
PLAYER_REQUEST_DELAY = 0.6

# CommonPlayerInfo's free-text POSITION ('Forward', 'Guard-Forward', ...) mapped
# to the single-letter code LeagueDashTeamStats' player_position_abbreviation_nullable
# filter expects. Confirmed live that the endpoint only accepts 'G' / 'F' / 'C' for
# this filter -- the compound codes ('G-F', 'F-C', ...) it also documents throw a
# malformed response. A dual-position player's POSITION string is "Primary-Secondary"
# (e.g. 'Center-Forward' = primarily a center), so we take the first component.
POSITION_TO_ABBR = {
    "Forward": "F",
    "Guard": "G",
    "Center": "C",
}


# --------------------------------------------------------------------------------------
# Season helpers
# --------------------------------------------------------------------------------------

def most_recent_completed_season(today: date | None = None) -> str:
    """Return the last *finished* season in NBA API format, e.g. '2025-26'.

    The NBA season spans two calendar years: it tips off in October and the Finals
    end in June. So:
      * Jul-Dec of year Y  -> the (Y-1)-Y season is the most recent completed one
                              (a new season may be underway, but it isn't finished).
      * Jan-Jun of year Y  -> the (Y-1)-Y season is still in progress, so the most
                              recent completed season is (Y-2)-(Y-1).
    """
    today = today or date.today()
    start_year = today.year - 1 if today.month >= 7 else today.year - 2
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def get_recent_games(
    player_id: int, lookback: int = 10, db_path: Path = DB_PATH, table: str = TABLE_NAME
) -> pd.DataFrame:
    """A player's most recent `lookback` games (GAME_DATE/PTS/REB/AST),
    oldest-first so a trend chart or distribution view reads chronologically.

    Purely a read of already-collected data -- no live API calls -- so it's
    cheap enough to call on every request rather than needing its own cache.
    """
    query = (
        f"SELECT GAME_DATE, PTS, REB, AST FROM {table} "
        f"WHERE PLAYER_ID = ? ORDER BY GAME_DATE DESC LIMIT ?"
    )
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql(query, conn, params=(player_id, lookback), parse_dates=["GAME_DATE"])
    return df.sort_values("GAME_DATE").reset_index(drop=True)


# --------------------------------------------------------------------------------------
# Step 1: name -> player_id
# --------------------------------------------------------------------------------------

def get_player_id(full_name: str) -> int:
    """Resolve a player's name to their NBA PLAYER_ID using nba_api's static index.

    `players.find_players_by_full_name` does a case-insensitive regex match against a
    JSON file bundled with nba_api, so it's instant and works offline. It returns a
    list of dicts: {'id', 'full_name', 'first_name', 'last_name', 'is_active'}.
    """
    matches = players.find_players_by_full_name(full_name)
    if not matches:
        raise ValueError(f"No NBA player found matching {full_name!r}.")

    if len(matches) > 1:
        # Prefer an exact, case-insensitive name match before falling back to active players.
        exact = [m for m in matches if m["full_name"].lower() == full_name.lower()]
        matches = exact or [m for m in matches if m["is_active"]] or matches

    player = matches[0]
    print(f"Resolved {full_name!r} -> {player['full_name']} (PLAYER_ID={player['id']})")
    return int(player["id"])


@lru_cache(maxsize=None)
def get_player_position(player_id: int) -> str:
    """Look up a player's primary position (e.g. 'Forward') via CommonPlayerInfo.

    This is a per-player biographical endpoint (bio, team, height/weight, position,
    draft info, ...); we only need the POSITION field, and it's constant for the
    pull, so `lru_cache` keeps it to one HTTP call per player_id.
    """
    endpoint = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=REQUEST_TIMEOUT)
    df = endpoint.get_data_frames()[0]
    position = df.loc[0, "POSITION"]
    print(f"Position for PLAYER_ID={player_id}: {position!r}")
    return position


def get_active_player_names() -> list[str]:
    """Full names of every currently active NBA player, via nba_api's static index.

    `players.get_active_players()` reads the same bundled JSON as
    `find_players_by_full_name` (filtered to `is_active=True`), so like Step 1's
    name lookup this is instant and offline â€” no HTTP request.
    """
    roster = players.get_active_players()
    return [p["full_name"] for p in roster]


def position_to_abbr(position: str) -> str:
    """Map CommonPlayerInfo's free-text position to LeagueDashTeamStats' filter code.

    Dual-position values ('Center-Forward') are collapsed to their primary
    component ('Center' -> 'C') since the endpoint rejects the compound codes.
    """
    primary = position.split("-")[0].strip()
    try:
        return POSITION_TO_ABBR[primary]
    except KeyError:
        raise ValueError(
            f"Unrecognized position {position!r}; expected one of {sorted(POSITION_TO_ABBR)}."
        ) from None


# --------------------------------------------------------------------------------------
# Step 2: player_id -> game log DataFrame
# --------------------------------------------------------------------------------------

def fetch_game_log(
    player_id: int,
    season: str,
    season_type: str = "Regular Season",
) -> pd.DataFrame:
    """Pull one row per game for a player-season from the PlayerGameLog endpoint.

    Parameters mirror the NBA API's own query string:
      * season           -> 'YYYY-YY' (e.g. '2025-26'); the API rejects other formats.
      * season_type_all_star -> 'Regular Season' | 'Playoffs' | 'Pre Season' | 'All Star'.
        Keep regular season and playoffs in separate pulls; usage patterns differ enough
        that blending them corrupts rolling averages.

    Returns the raw endpoint DataFrame (one result set, hence `get_data_frames()[0]`).
    """
    endpoint = playergamelog.PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star=season_type,
        timeout=REQUEST_TIMEOUT,
    )
    df = endpoint.get_data_frames()[0]

    if df.empty:
        raise ValueError(
            f"PlayerGameLog returned no rows for player_id={player_id}, "
            f"season={season!r}, season_type={season_type!r}. "
            "The player may not have played that season."
        )

    print(f"Fetched {len(df)} games for season {season} ({season_type}).")
    return df


# --------------------------------------------------------------------------------------
# Step 3: clean + feature engineering
# --------------------------------------------------------------------------------------

def _parse_minutes(value) -> float:
    """PlayerGameLog usually returns MIN as an int, but box-score endpoints return
    'MM:SS' (and sometimes None for DNPs). Normalize both to float minutes."""
    if pd.isna(value):
        return float("nan")
    if isinstance(value, str) and ":" in value:
        minutes, _, seconds = value.partition(":")
        return int(minutes) + int(seconds) / 60
    return float(value)


def clean_game_log(
    df: pd.DataFrame, player_name: str, season: str, player_position: str
) -> pd.DataFrame:
    """Trim the raw endpoint response to the columns we care about and normalize types.

    The API returns GAME_DATE as a string like 'OCT 22, 2025' and lists games
    newest-first; we convert to datetime and re-sort oldest-first so that rolling
    windows look *backwards* in time.
    """
    out = pd.DataFrame(
        {
            "PLAYER_ID": df["Player_ID"].astype(int),
            "PLAYER_NAME": player_name,
            "PLAYER_POSITION": player_position,
            "SEASON": season,
            "GAME_ID": df["Game_ID"].astype(str),
            "GAME_DATE": pd.to_datetime(df["GAME_DATE"], format="%b %d, %Y"),
            "MATCHUP": df["MATCHUP"],
            # MATCHUP is 'LAL vs. BOS' for home games and 'LAL @ BOS' for road games.
            "IS_HOME": (~df["MATCHUP"].str.contains("@")).astype(int),
            "WL": df["WL"],
            "MIN": df["MIN"].map(_parse_minutes),
            "PTS": df["PTS"].astype(float),
            "REB": df["REB"].astype(float),
            "AST": df["AST"].astype(float),
            "FG_PCT": df["FG_PCT"].astype(float),
            "FG3_PCT": df["FG3_PCT"].astype(float),
            "FT_PCT": df["FT_PCT"].astype(float),
        }
    )

    # Common combo prop; cheap to carry and useful as a modeling target.
    out["PRA"] = out["PTS"] + out["REB"] + out["AST"]

    out = out.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)
    out["GAME_NUMBER"] = out.groupby("PLAYER_ID").cumcount() + 1

    # Days of rest before each game: GAME_DATE.diff() is a Timedelta, so pull out
    # whole days. Grouping by PLAYER_ID keeps this correct once multiple players
    # share one DataFrame. A player's first game in the sample has no prior game
    # to diff against, so it's left NaN (-> NULL in SQLite) rather than guessed.
    out["REST_DAYS"] = (
        out.groupby("PLAYER_ID")["GAME_DATE"].diff().dt.days
    )

    # Three-letter opponent code, e.g. 'LAL @ BOS' / 'LAL vs. BOS' -> 'BOS'.
    # MATCHUP is always "<TEAM> @/vs. <OPPONENT>", so the opponent is the last token.
    out["OPPONENT_ABBR"] = out["MATCHUP"].str.split().str[-1]

    return out


def _rolling_features_for_player(
    group: pd.DataFrame,
    stat_cols: list[str],
    windows: tuple[int, ...],
    exclude_current: bool,
) -> pd.DataFrame:
    features: dict[str, pd.Series] = {}

    for col in stat_cols:
        # `.shift(1)` is the whole ballgame for a predictive model: row N's features must
        # describe only games 1..N-1. Without it, the 5-game average silently contains the
        # very outcome you're trying to predict and your backtest will look fantastic and
        # be worthless. Set exclude_current=False only for descriptive/reporting output.
        series = group[col].shift(1) if exclude_current else group[col]

        for window in windows:
            # min_periods=1 emits a partial average early in the season instead of NaN.
            # Use min_periods=window instead if you'd rather drop those rows in training.
            features[f"{col}_AVG_{window}"] = series.rolling(window, min_periods=1).mean()

        # Expanding mean = season-to-date average through the prior game.
        features[f"{col}_AVG_SEASON"] = series.expanding(min_periods=1).mean()

    return pd.DataFrame(features, index=group.index)


def add_rolling_averages(
    df: pd.DataFrame,
    stat_cols: list[str] | None = None,
    windows: tuple[int, ...] = ROLLING_WINDOWS,
    exclude_current: bool = True,
) -> pd.DataFrame:
    """Attach trailing N-game and season-to-date averages for each stat.

    Grouping by PLAYER_ID keeps this correct once you loop over a whole roster and
    append everything into one table.
    """
    stat_cols = stat_cols or ROLLING_STAT_COLS
    df = df.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)

    features = pd.concat(
        [
            _rolling_features_for_player(group, stat_cols, windows, exclude_current)
            for _, group in df.groupby("PLAYER_ID", sort=False)
        ]
    ).sort_index()

    return df.join(features.round(3))


# --------------------------------------------------------------------------------------
# Step 3b: opponent defense features (point-in-time, no lookahead)
# --------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _team_abbr_to_id() -> dict[str, int]:
    """Offline abbreviation -> TEAM_ID map from nba_api's static team index."""
    return {t["abbreviation"]: t["id"] for t in teams.get_teams()}


def _fetch_team_advanced_stats(
    date_to: str, season: str, season_type: str
) -> dict[int, dict[str, float]]:
    """One LeagueDashTeamStats call: every team's Advanced-measure-type metrics
    accumulated from the start of `season` through `date_to` (inclusive, 'MM/DD/YYYY').

    MeasureType='Advanced' is what switches the endpoint from basic box-score totals
    to efficiency metrics. We pull three: DEF_RATING (points allowed per 100
    possessions), and AST_PCT / REB_PCT, the opponent's own share-of-baskets-assisted
    and share-of-available-rebounds-grabbed â€” i.e. how much a given opponent tends to
    move the ball and control the glass, a useful proxy for the kind of game they'll
    play against us. Leaving DateFrom blank scopes the call to the whole season, so
    DateTo alone controls the as-of cutoff. If DateTo falls before any games have been
    played, the API returns an empty result set.
    """
    endpoint = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star=season_type,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        date_to_nullable=date_to,
        timeout=REQUEST_TIMEOUT,
    )
    df = endpoint.get_data_frames()[0]
    if df.empty:
        return {}
    df = df.set_index("TEAM_ID")
    return df[["DEF_RATING", "AST_PCT", "REB_PCT"]].to_dict(orient="index")


def _fetch_opp_fg_pct_by_position(
    date_to: str, season: str, season_type: str, position_abbr: str
) -> dict[int, float]:
    """One LeagueDashTeamStats call: each team's opponent FG% allowed, filtered to
    shots taken specifically by opposing players at `position_abbr` (e.g. 'F').

    MeasureType='Opponent' flips the box score to what opponents put up against a
    team (OPP_FGM, OPP_FGA, OPP_FG_PCT, ...); adding
    `player_position_abbreviation_nullable` further restricts that to possessions
    involving an opposing player at the given position, which is what isolates
    "FG% this team allows to forwards" instead of "FG% this team allows to anyone".
    Same DateTo-only, as-of-cutoff behavior as the Advanced pull above.
    """
    endpoint = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star=season_type,
        measure_type_detailed_defense="Opponent",
        per_mode_detailed="PerGame",
        player_position_abbreviation_nullable=position_abbr,
        date_to_nullable=date_to,
        timeout=REQUEST_TIMEOUT,
    )
    df = endpoint.get_data_frames()[0]
    if df.empty:
        return {}
    return dict(zip(df["TEAM_ID"], df["OPP_FG_PCT"]))


def add_opponent_defense_features(
    df: pd.DataFrame,
    season: str,
    player_position_abbr: str,
    season_type: str = "Regular Season",
    team_cache: dict[str, dict[int, dict[str, float]]] | None = None,
    position_cache: dict[tuple[str, str], dict[int, float]] | None = None,
    request_delay: float = OPPONENT_STATS_REQUEST_DELAY,
) -> pd.DataFrame:
    """Attach OPP_DEF_RATING, OPP_AST_PCT, OPP_REB_PCT, and OPP_FG_PCT_VS_POSITION,
    each as of the day *before* each game so no feature leaks that game's own result
    into itself (same no-lookahead principle as the rolling averages).

    Computing these per row would mean one API call per game. Instead we call each
    endpoint once per *unique date* in the sample (all 30 teams come back in a single
    response) and reuse that snapshot for every game played on that date. `team_cache`
    / `position_cache` can be passed in and shared across multiple players/calls so a
    full-roster run doesn't re-fetch a date (or date+position pair) it's already seen.
    """
    team_cache = {} if team_cache is None else team_cache
    position_cache = {} if position_cache is None else position_cache
    df = df.copy()

    as_of = (df["GAME_DATE"] - timedelta(days=1)).dt.strftime("%m/%d/%Y")
    unique_dates = sorted(as_of.unique())

    for date_to in unique_dates:
        if date_to not in team_cache:
            team_cache[date_to] = _fetch_team_advanced_stats(date_to, season, season_type)
            time.sleep(request_delay)

        position_key = (date_to, player_position_abbr)
        if position_key not in position_cache:
            position_cache[position_key] = _fetch_opp_fg_pct_by_position(
                date_to, season, season_type, player_position_abbr
            )
            time.sleep(request_delay)

    abbr_to_id = _team_abbr_to_id()
    team_ids = df["OPPONENT_ABBR"].map(abbr_to_id)

    def_ratings, ast_pcts, reb_pcts, fg_pct_vs_pos = [], [], [], []
    for date_to, team_id in zip(as_of, team_ids):
        team_stats = team_cache[date_to].get(team_id, {})
        def_ratings.append(team_stats.get("DEF_RATING"))
        ast_pcts.append(team_stats.get("AST_PCT"))
        reb_pcts.append(team_stats.get("REB_PCT"))
        fg_pct_vs_pos.append(position_cache[(date_to, player_position_abbr)].get(team_id))

    df["OPP_DEF_RATING"] = def_ratings
    df["OPP_AST_PCT"] = ast_pcts
    df["OPP_REB_PCT"] = reb_pcts
    df["OPP_FG_PCT_VS_POSITION"] = fg_pct_vs_pos
    return df


# --------------------------------------------------------------------------------------
# Step 4: persist to SQLite
# --------------------------------------------------------------------------------------

def save_to_sqlite(df: pd.DataFrame, db_path: Path = DB_PATH, table: str = TABLE_NAME) -> None:
    """Write the DataFrame to SQLite, replacing only this player-season's rows.

    Re-running the script for the same player/season is idempotent, but rows for
    *other* players already in the table are left alone (which `if_exists='replace'`
    would wipe out).
    """
    with sqlite3.connect(db_path) as conn:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()

        if table_exists:
            pairs = df[["PLAYER_ID", "SEASON"]].drop_duplicates().itertuples(index=False)
            conn.executemany(
                f"DELETE FROM {table} WHERE PLAYER_ID = ? AND SEASON = ?",
                [(int(pid), season) for pid, season in pairs],
            )

        # GAME_DATE is stored as ISO 'YYYY-MM-DD' text so SQLite can sort/compare it.
        to_write = df.copy()
        to_write["GAME_DATE"] = to_write["GAME_DATE"].dt.strftime("%Y-%m-%d")
        to_write.to_sql(table, conn, if_exists="append", index=False)

        # Speeds up the per-player lookups you'll do constantly when building features.
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_player_date "
            f"ON {table} (PLAYER_ID, GAME_DATE)"
        )

    print(f"Wrote {len(df)} rows to {db_path} -> table '{table}'.")


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def build_player_stats(
    player_name: str,
    season: str | None = None,
    season_type: str = "Regular Season",
    db_path: Path = DB_PATH,
    team_def_cache: dict[str, dict[int, dict[str, float]]] | None = None,
    position_def_cache: dict[tuple[str, str], dict[int, float]] | None = None,
) -> pd.DataFrame:
    """Run the full pipeline for one player and return the feature DataFrame.

    `team_def_cache` / `position_def_cache` are exposed so a roster loop can pass
    in the same dicts for every player, turning repeat game dates (and, for the
    position cache, repeat date+position pairs) into free cache hits instead of
    redundant LeagueDashTeamStats calls.
    """
    season = season or most_recent_completed_season()

    player_id = get_player_id(player_name)
    position = get_player_position(player_id)
    position_abbr = position_to_abbr(position)

    raw = fetch_game_log(player_id, season, season_type)
    clean = clean_game_log(raw, player_name, season, position)
    featured = add_rolling_averages(clean)
    featured = add_opponent_defense_features(
        featured,
        season,
        position_abbr,
        season_type=season_type,
        team_cache=team_def_cache,
        position_cache=position_def_cache,
    )
    save_to_sqlite(featured, db_path=db_path)
    return featured


def build_league_stats(
    season: str | None = None,
    season_type: str = "Regular Season",
    db_path: Path = DB_PATH,
    player_names: list[str] | None = None,
    player_request_delay: float = PLAYER_REQUEST_DELAY,
) -> None:
    """Run build_player_stats for every active player, one roster-wide pass.

    `team_def_cache` / `position_def_cache` are created once here, *outside* the
    loop, and handed to every `build_player_stats` call â€” that's what turns a game
    date (or date+position pair) already seen for one player into a free lookup
    for the next, instead of re-fetching it from LeagueDashTeamStats. Player-level
    calls (CommonPlayerInfo, PlayerGameLog) can't share a cache the same way since
    they're inherently per-player, so those are throttled by `player_request_delay`
    between iterations on top of the existing OPPONENT_STATS_REQUEST_DELAY.

    A player with no games in `season` (e.g. a rookie who hasn't debuted yet) is
    logged and skipped rather than aborting the whole run â€” with ~450 players in
    one pass, one missing game log shouldn't cost the rest of the pull.
    """
    roster = player_names if player_names is not None else get_active_player_names()
    team_def_cache: dict[str, dict[int, dict[str, float]]] = {}
    position_def_cache: dict[tuple[str, str], dict[int, float]] = {}

    total = len(roster)
    for i, player_name in enumerate(roster, start=1):
        print(f"\nProcessing {i}/{total}: {player_name}...")
        try:
            build_player_stats(
                player_name,
                season=season,
                season_type=season_type,
                db_path=db_path,
                team_def_cache=team_def_cache,
                position_def_cache=position_def_cache,
            )
        except ValueError as exc:
            print(f"  Skipped {player_name}: {exc}")

        time.sleep(player_request_delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--player", default="LeBron James", help="Full player name.")
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Run the pipeline for every currently active NBA player "
            "(nba_api.stats.static.players.get_active_players()) instead of just --player."
        ),
    )
    parser.add_argument(
        "--season",
        default=None,
        help="Season in 'YYYY-YY' format. Defaults to the most recently completed season.",
    )
    parser.add_argument(
        "--season-type",
        default="Regular Season",
        choices=["Regular Season", "Playoffs", "Pre Season", "All Star"],
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to the SQLite file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.all:
        build_league_stats(season=args.season, season_type=args.season_type, db_path=args.db)
        return

    df = build_player_stats(
        player_name=args.player,
        season=args.season,
        season_type=args.season_type,
        db_path=args.db,
    )

    preview_cols = [
        "GAME_DATE", "MATCHUP", "MIN", "PTS", "FG_PCT", "FG_PCT_AVG_5",
        "REST_DAYS", "OPP_DEF_RATING", "OPP_AST_PCT", "OPP_REB_PCT",
        "OPP_FG_PCT_VS_POSITION",
    ]
    with pd.option_context("display.width", 160, "display.max_columns", None):
        print("\nLast 10 games (trailing averages exclude the current game):")
        print(df[preview_cols].tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
