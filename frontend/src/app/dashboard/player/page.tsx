"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { ApiError, getPlayer } from "@/lib/api";
import { todayIso } from "@/lib/format";
import type { RecentGame } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useMemo, useState } from "react";

const STATS = ["PTS", "REB", "AST"] as const;

function average(games: RecentGame[], stat: (typeof STATS)[number]): number | null {
  if (games.length === 0) return null;
  return games.reduce((sum, g) => sum + g[stat], 0) / games.length;
}

export default function PlayerExplorerPage() {
  const [searchInput, setSearchInput] = useState("");
  const [gameDate, setGameDate] = useState(todayIso());
  const debouncedName = useDebouncedValue(searchInput, 400).trim();

  const { data, isPending, isFetching, isError, error } = useQuery({
    queryKey: ["player", debouncedName, gameDate],
    queryFn: () => getPlayer(debouncedName, { game_date: gameDate }),
    enabled: debouncedName.length > 0,
    retry: false,
  });

  const last5 = useMemo(() => data?.recent_games.slice(-5) ?? [], [data]);
  const last10 = data?.recent_games ?? [];

  const is404 = error instanceof ApiError && error.status === 404;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Player Explorer</h1>
        <p className="text-sm text-muted-foreground">
          Model projections vs. live market lines, and recent-game form, for one player.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-6 sm:flex-row sm:items-end">
          <div className="flex flex-1 flex-col gap-2">
            <label htmlFor="player-search" className="text-sm font-medium">
              Player name
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="player-search"
                placeholder="e.g. LeBron James"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="pl-8"
              />
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor="game-date" className="text-sm font-medium">
              Game date
            </label>
            <Input
              id="game-date"
              type="date"
              value={gameDate}
              onChange={(e) => setGameDate(e.target.value)}
              className="w-44"
            />
          </div>
        </CardContent>
      </Card>

      {debouncedName.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Start typing a player&apos;s name to explore their matchup and recent form.
        </p>
      )}

      {debouncedName.length > 0 && (isPending || isFetching) && (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      )}

      {debouncedName.length > 0 && !isFetching && isError && is404 && (
        <Card>
          <CardContent className="text-sm text-muted-foreground">
            Player not found or no lines available for &quot;{debouncedName}&quot;.
          </CardContent>
        </Card>
      )}

      {debouncedName.length > 0 && !isFetching && isError && !is404 && (
        <p className="text-sm text-destructive">
          {error instanceof Error ? error.message : "Failed to load this player."}
        </p>
      )}

      {debouncedName.length > 0 && !isFetching && !isError && data && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                {data.player_name}
                {data.matchup && <> vs. {data.matchup.opponent}</>}
              </CardTitle>
              <CardDescription>{data.game_date}</CardDescription>
            </CardHeader>

            <CardContent>
              {data.warning && !data.matchup && (
                <p className="text-sm text-muted-foreground">{data.warning}</p>
              )}

              {data.matchup && (
                <div className="flex flex-col gap-4">
                  <div className="grid grid-cols-3 gap-4 text-sm">
                    <div>
                      <span className="text-xs text-muted-foreground">Opp Def Rating</span>
                      <p className="font-medium">
                        {data.matchup.opponent_def_rating.toFixed(1)}
                      </p>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">Opp Reb%</span>
                      <p className="font-medium">
                        {(data.matchup.opponent_reb_pct * 100).toFixed(1)}%
                      </p>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">Opp Ast%</span>
                      <p className="font-medium">
                        {(data.matchup.opponent_ast_pct * 100).toFixed(1)}%
                      </p>
                    </div>
                  </div>

                  <div>
                    <p className="mb-2 text-sm font-medium">
                      Model Projection vs. Market Line
                    </p>
                    <div className="flex flex-wrap gap-3">
                      {data.matchup.projections_vs_lines.map((row) => {
                        const isPositive = row.delta > 0;
                        const isFlat = row.delta === 0;

                        return (
                          <Badge
                            key={row.prop}
                            variant="outline"
                            className={cn(
                              "h-auto flex-col items-start gap-1 px-3 py-2 text-left",
                              !isFlat &&
                                (isPositive
                                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-500"
                                  : "border-rose-500/40 bg-rose-500/10 text-rose-500")
                            )}
                          >
                            <span className="text-xs font-normal opacity-80">{row.prop}</span>
                            <span className="text-sm font-semibold">
                              {row.model_projection.toFixed(1)} proj vs {row.market_line.toFixed(1)} line
                            </span>
                            <span className="text-xs font-normal">
                              {isFlat ? "No edge" : `${isPositive ? "+" : ""}${row.delta.toFixed(1)} ${isPositive ? "Over" : "Under"} edge`}
                            </span>
                          </Badge>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Rolling Stat Distribution</CardTitle>
              <CardDescription>
                Averages over the tracked game log -- this endpoint only carries the last 10
                games, so a season-long column isn&apos;t available here.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {last10.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No recent game log available for this player.
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Stat</TableHead>
                      <TableHead className="text-right">Last 5 Avg</TableHead>
                      <TableHead className="text-right">Last 10 Avg</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {STATS.map((stat) => {
                      const avg5 = average(last5, stat);
                      const avg10 = average(last10, stat);
                      return (
                        <TableRow key={stat}>
                          <TableCell className="font-medium">{stat}</TableCell>
                          <TableCell className="text-right">
                            {avg5 !== null ? avg5.toFixed(1) : "--"}
                          </TableCell>
                          <TableCell className="text-right">
                            {avg10 !== null ? avg10.toFixed(1) : "--"}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {last10.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Recent Game Log</CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead className="text-right">PTS</TableHead>
                      <TableHead className="text-right">REB</TableHead>
                      <TableHead className="text-right">AST</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {[...last10].reverse().map((game) => (
                      <TableRow key={game.GAME_DATE}>
                        <TableCell>{game.GAME_DATE}</TableCell>
                        <TableCell className="text-right">{game.PTS}</TableCell>
                        <TableCell className="text-right">{game.REB}</TableCell>
                        <TableCell className="text-right">{game.AST}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
