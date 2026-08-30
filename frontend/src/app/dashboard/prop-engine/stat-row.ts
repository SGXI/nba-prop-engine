import type { PlayerProjection } from "@/types/models";

export type StatCategory = "Points" | "Rebounds" | "Assists" | "3PM";

/** One row of the Prop Engine table: a single stat category for a single
 * player. A `PlayerProjection` bundles all four categories together, so this
 * augments each category's bare `StatProjection` (line/projected_value/
 * edge_pct/confidence_score) with enough player identity to render, sort,
 * and search a flat multi-player, multi-stat table. */
export interface StatRow {
  player_id: string;
  player_name: string;
  team: string;
  matchup: string;
  category: StatCategory;
  line: number;
  projected_value: number;
  edge_pct: number;
  confidence_score: number;
}

const CATEGORY_KEYS = [
  ["points", "Points"],
  ["rebounds", "Rebounds"],
  ["assists", "Assists"],
  ["three_pointers_made", "3PM"],
] as const satisfies ReadonlyArray<[keyof PlayerProjection, StatCategory]>;

export function flattenProjections(projections: PlayerProjection[]): StatRow[] {
  return projections.flatMap((player) =>
    CATEGORY_KEYS.map(([key, category]) => {
      const stat = player[key] as PlayerProjection["points"];
      return {
        player_id: player.player_id,
        player_name: player.player_name,
        team: player.team,
        matchup: player.matchup,
        category,
        line: stat.line,
        projected_value: stat.projected_value,
        edge_pct: stat.edge_pct,
        confidence_score: stat.confidence_score,
      };
    })
  );
}
