/**
 * TypeScript mirrors of every FastAPI response/request payload under
 * /api/v1 (see src/nbabettingmodel/api/v1/*.py in the backend). Field names
 * intentionally match the backend's JSON keys verbatim, including the
 * capitalized/spaced keys `/streaks` returns (it serializes a pandas
 * DataFrame with display-formatted column names, unlike every other
 * endpoint's snake_case dicts) -- reshaping those into snake_case here would
 * silently drift from what the API actually sends.
 */

export type PropType = "PTS" | "AST" | "REB" | "PRA";
export type BetSide = "Over" | "Under";

/** One leaderboard row: a single +EV bet on one side of one prop. Shared
 * shape across /ev-board's `bets`, /top-picks's `picks` (via TopPick), and
 * /parlays's `legs`. */
export interface EVBet {
  player_name: string;
  prop_type: PropType;
  side: BetSide;
  line: number;
  odds: number;
  true_prob_pct: number;
  ev_pct: number;
}

export interface EVBoardResponse {
  game_date: string;
  count: number;
  bets: EVBet[];
}

/** An EVBet with its fractional-Kelly recommended wager attached. */
export interface TopPick extends EVBet {
  recommended_wager: number;
}

export interface TopPicksResponse {
  game_date: string;
  min_edge: number;
  bankroll: number;
  fractional_kelly: number;
  count: number;
  total_recommended_wager: number;
  exceeds_bankroll_if_staked_simultaneously: boolean;
  picks: TopPick[];
}

export interface ParlayTicket {
  game_date: string;
  target_odds: number;
  num_legs_requested: number;
  count: number;
  reached_target: boolean;
  legs: EVBet[];
  combined_odds: number | null;
  combined_true_probability_pct: number | null;
  total_ev_pct: number | null;
}

/** One row of /streaks -- see the module docstring above for why the keys
 * are capitalized/spaced instead of snake_case. */
export interface Streak {
  Player: string;
  Prop: string;
  "Target Line": string;
  "Hit Rate": string;
  Odds: string;
}

export interface StreaksResponse {
  count: number;
  streaks: Streak[];
}

export interface ProjectionVsLine {
  prop: PropType;
  model_projection: number;
  market_line: number;
  delta: number;
}

export interface PlayerMatchup {
  opponent: string;
  opponent_def_rating: number;
  opponent_ast_pct: number;
  opponent_reb_pct: number;
  projections_vs_lines: ProjectionVsLine[];
}

export interface RecentGame {
  GAME_DATE: string;
  PTS: number;
  REB: number;
  AST: number;
}

export interface PlayerProjection {
  player_name: string;
  player_id: number;
  game_date: string;
  matchup: PlayerMatchup | null;
  warning: string | null;
  recent_games: RecentGame[];
}

export interface PnLSummary {
  total_bets: number;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  pending: number;
  win_pct: number;
  total_pnl: number;
  total_wagered: number;
  roi_pct: number;
}

export interface LogBetPayload {
  date: string;
  player_name: string;
  prop: PropType;
  side: BetSide;
  line: number;
  odds: number;
  wager_amount: number;
  is_top_10?: boolean;
}

export interface LogBetResponse {
  id: number;
  status: string;
}

export interface HealthResponse {
  status: string;
  models_loaded: string[];
}
