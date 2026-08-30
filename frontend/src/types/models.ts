/**
 * Types for the internal ML predictive sports model's props endpoint
 * (ML_BACKEND_URL, proxied through src/app/api/props/route.ts). This is a
 * distinct service from this project's own FastAPI betting engine -- see
 * src/lib/types.ts for that one, which happens to also export a
 * `PlayerProjection` type. Same name, different module, different service,
 * different shape: kept apart deliberately rather than merged, since they
 * describe two unrelated APIs that just happen to share a concept name.
 *
 * Field names assume the ML backend responds with the same snake_case
 * convention as this project's own Python services, since the actual wire
 * format of that external service isn't known here -- adjust to match once
 * the real service is reachable.
 */

/** A single stat category's sportsbook line plus the model's prediction
 * against it. */
export interface StatProjection {
  /** Standard sportsbook line for this stat. */
  line: number;
  /** The model's projected value for this stat. */
  projected_value: number;
  /** Projected value vs. the sportsbook line, expressed as a percentage edge. */
  edge_pct: number;
  /** The model's confidence in this projection, 0-100. */
  confidence_score: number;
}

/** One past game's actual points scored vs. what the model would have
 * projected for it, for the trend chart on the player detail page. */
export interface GameLogEntry {
  game_date: string;
  actual_points: number;
  projected_points: number;
}

export interface PlayerProjection {
  player_id: string;
  player_name: string;
  team: string;
  /** e.g. "LAL @ BOS" */
  matchup: string;
  game_date: string;
  points: StatProjection;
  rebounds: StatProjection;
  assists: StatProjection;
  three_pointers_made: StatProjection;
  /** Last 10 games, oldest first -- assumed shape, same caveat as the
   * module docstring: the real ML backend's response isn't known yet, so
   * this is a forward-looking guess at what a "historical game logs" field
   * would look like, scoped to points since that's the only stat the
   * detail page's chart plots. */
  recent_games: GameLogEntry[];
}

export interface PlayerProjectionsResponse {
  count: number;
  projections: PlayerProjection[];
}
