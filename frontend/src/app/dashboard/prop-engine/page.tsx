import { fetchPlayerProjections } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Gauge, TrendingUp, Users } from "lucide-react";
import { columns } from "./columns";
import { DataTable } from "./data-table";
import { flattenProjections, type StatRow } from "./stat-row";

function topEdgeRow(rows: StatRow[]): StatRow | null {
  return rows.reduce<StatRow | null>(
    (best, row) => (!best || Math.abs(row.edge_pct) > Math.abs(best.edge_pct) ? row : best),
    null
  );
}

export default async function PropEnginePage() {
  let rows: StatRow[] = [];
  let error: string | null = null;

  try {
    const { projections } = await fetchPlayerProjections();
    rows = flattenProjections(projections);
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load projections.";
  }

  const topEdge = topEdgeRow(rows);
  const playerCount = new Set(rows.map((row) => row.player_id)).size;
  const avgConfidence = rows.length
    ? rows.reduce((sum, row) => sum + row.confidence_score, 0) / rows.length
    : 0;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">SG Prop Engine Pro</h1>
        <p className="text-sm text-muted-foreground">
          Model-driven player prop projections vs. sportsbook lines, powered by the internal
          ML prediction service.
        </p>
      </div>

      {error ? (
        <Card>
          <CardContent className="text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardDescription className="flex items-center gap-1.5">
                  <Users className="h-3.5 w-3.5" />
                  Players Tracked
                </CardDescription>
                <CardTitle className="text-2xl">{playerCount}</CardTitle>
              </CardHeader>
            </Card>

            <Card>
              <CardHeader>
                <CardDescription className="flex items-center gap-1.5">
                  <TrendingUp className="h-3.5 w-3.5" />
                  Top Edge Found
                </CardDescription>
                <CardTitle
                  className={
                    topEdge && topEdge.edge_pct < 0
                      ? "text-2xl text-rose-500"
                      : "text-2xl text-emerald-500"
                  }
                >
                  {topEdge ? `${topEdge.edge_pct > 0 ? "+" : ""}${topEdge.edge_pct.toFixed(1)}%` : "--"}
                </CardTitle>
              </CardHeader>
              {topEdge && (
                <CardContent className="text-xs text-muted-foreground">
                  {topEdge.player_name} &middot; {topEdge.category}
                </CardContent>
              )}
            </Card>

            <Card>
              <CardHeader>
                <CardDescription className="flex items-center gap-1.5">
                  <Gauge className="h-3.5 w-3.5" />
                  Overall Model Confidence
                </CardDescription>
                <CardTitle className="text-2xl">{avgConfidence.toFixed(0)}%</CardTitle>
              </CardHeader>
            </Card>
          </div>

          <DataTable columns={columns} data={rows} />
        </>
      )}
    </div>
  );
}
