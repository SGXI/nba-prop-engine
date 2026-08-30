"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { getStreaks } from "@/lib/api";
import type { Streak } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";

/** "8/10" -> { hits: 8, games: 10, pct: 80 }. The backend serializes hit
 * rate as this display-ready string (see Streak in lib/types.ts). */
function parseHitRate(hitRate: string): { hits: number; games: number; pct: number } {
  const [hits, games] = hitRate.split("/").map(Number);
  return { hits, games, pct: games ? (hits / games) * 100 : 0 };
}

export default function StreaksPage() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["streaks"],
    queryFn: getStreaks,
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Streaks</h1>
        <p className="text-sm text-muted-foreground">
          Players clearing a standard prop threshold in 80%+ of their last 10 games, at
          odds a book would still let you bet.
        </p>
      </div>

      {isPending && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      )}

      {isError && (
        <p className="text-sm text-destructive">
          {error instanceof Error ? error.message : "Failed to load streaks."}
        </p>
      )}

      {!isPending && !isError && data && data.streaks.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No market-playable hot streaks right now.
        </p>
      )}

      {!isPending && !isError && data && data.streaks.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.streaks.map((streak: Streak, i: number) => {
            const { hits, games, pct } = parseHitRate(streak["Hit Rate"]);

            return (
              <Card key={`${streak.Player}-${streak.Prop}-${i}`}>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between text-base">
                    {streak.Player}
                    <Badge variant="outline">{streak.Odds}</Badge>
                  </CardTitle>
                  <CardDescription>
                    {streak.Prop} {streak["Target Line"]}
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Last {games} games</span>
                    <span className="font-medium">
                      {hits}/{games} ({pct.toFixed(0)}%)
                    </span>
                  </div>
                  <Progress value={pct} />
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
