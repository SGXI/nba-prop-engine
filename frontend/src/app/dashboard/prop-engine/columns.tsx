import { cn } from "@/lib/utils";
import type { ColumnDef } from "@tanstack/react-table";
import Link from "next/link";
import type { StatRow } from "./stat-row";
import type { TableFeatureSet } from "./table-features";

/** Columns for the Prop Engine data table. A bare `StatProjection` (line/
 * projected_value/edge_pct/confidence_score) has no player identity, so a
 * "Player" column is included beyond the requested four -- without it every
 * row would just read "Points / Rebounds / Assists / 3PM" with no way to
 * tell which player, or anything for the search box in data-table.tsx to
 * filter against. Its accessor concatenates name + matchup so the global
 * filter's "player name or matchup" search works off a single column. */
export const columns: ColumnDef<TableFeatureSet, StatRow>[] = [
  {
    id: "player",
    header: "Player",
    accessorFn: (row) => `${row.player_name} ${row.matchup}`,
    cell: ({ row }) => (
      <div className="flex flex-col">
        <Link
          href={`/dashboard/prop-engine/player/${row.original.player_id}`}
          className="font-medium hover:underline"
        >
          {row.original.player_name}
        </Link>
        <span className="text-xs text-muted-foreground">
          {row.original.team} &middot; {row.original.matchup}
        </span>
      </div>
    ),
  },
  {
    accessorKey: "category",
    header: "Stat Category",
  },
  {
    accessorKey: "line",
    header: "Sportsbook Line",
    cell: ({ getValue }) => getValue<number>().toFixed(1),
  },
  {
    accessorKey: "projected_value",
    header: "Model Projected Value",
    cell: ({ getValue }) => getValue<number>().toFixed(1),
  },
  {
    accessorKey: "edge_pct",
    header: "Edge %",
    cell: ({ getValue }) => {
      const edge = getValue<number>();
      const color =
        edge > 5 ? "text-emerald-500" : edge < -5 ? "text-rose-500" : "text-muted-foreground";
      return (
        <span className={cn("font-medium", color)}>
          {edge > 0 ? "+" : ""}
          {edge.toFixed(1)}%
        </span>
      );
    },
  },
];
