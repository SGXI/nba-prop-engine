"use client";

import { ManageBillingButton } from "@/components/dashboard/manage-billing-button";
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
import { Slider } from "@/components/ui/slider";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getEVBoard } from "@/lib/api";
import { edgePct, formatAmericanOdds, formatPct, formatSignedPct, todayIso } from "@/lib/format";
import type { EVBet } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

const MIN_EV_FLOOR = 0;
const MIN_EV_CEILING = 15;

export default function EVBoardPage() {
  const [gameDate, setGameDate] = useState(todayIso());
  const [minEv, setMinEv] = useState(2);

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["ev-board", gameDate],
    queryFn: () => getEVBoard({ game_date: gameDate }),
  });

  const filteredBets = useMemo<EVBet[]>(() => {
    if (!data) return [];
    return data.bets
      .filter((bet) => bet.ev_pct >= minEv)
      .sort((a, b) => b.ev_pct - a.ev_pct);
  }, [data, minEv]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Daily +EV Board</h1>
          <p className="text-sm text-muted-foreground">
            Every prop bet on the slate that clears the model&apos;s expected-value threshold.
          </p>
        </div>
        <ManageBillingButton />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Filters</CardTitle>
          <CardDescription>
            The backend only returns bets at 2.0% EV or better, so lowering the slider
            below that won&apos;t surface additional bets.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-6 sm:flex-row sm:items-end">
          <div className="flex flex-col gap-2">
            <label htmlFor="game-date" className="text-sm font-medium">
              Slate date
            </label>
            <Input
              id="game-date"
              type="date"
              value={gameDate}
              onChange={(e) => setGameDate(e.target.value)}
              className="w-44"
            />
          </div>

          <div className="flex flex-1 flex-col gap-2">
            <label htmlFor="min-ev" className="text-sm font-medium">
              Minimum EV%: {formatPct(minEv)}
            </label>
            <Slider
              id="min-ev"
              min={MIN_EV_FLOOR}
              max={MIN_EV_CEILING}
              step={0.5}
              value={[minEv]}
              onValueChange={(value) => setMinEv(Array.isArray(value) ? value[0] : value)}
              className="max-w-md"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {data ? `${filteredBets.length} of ${data.count} bets` : "Bets"}
          </CardTitle>
          <CardDescription>Slate: {gameDate}</CardDescription>
        </CardHeader>
        <CardContent>
          {isPending && (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          )}

          {isError && (
            <p className="text-sm text-destructive">
              {error instanceof Error ? error.message : "Failed to load the +EV board."}
            </p>
          )}

          {!isPending && !isError && data && filteredBets.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No bets match the current filters for {gameDate}.
            </p>
          )}

          {!isPending && !isError && filteredBets.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Player</TableHead>
                  <TableHead>Prop</TableHead>
                  <TableHead className="text-right">Line</TableHead>
                  <TableHead className="text-right">Sportsbook Odds</TableHead>
                  <TableHead className="text-right">True Prob %</TableHead>
                  <TableHead className="text-right">Edge %</TableHead>
                  <TableHead className="text-right">EV %</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredBets.map((bet) => (
                  <TableRow key={`${bet.player_name}-${bet.prop_type}-${bet.side}`}>
                    <TableCell className="font-medium">{bet.player_name}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="gap-1">
                        {bet.prop_type} {bet.side}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">{bet.line.toFixed(1)}</TableCell>
                    <TableCell className="text-right">
                      {formatAmericanOdds(bet.odds)}
                    </TableCell>
                    <TableCell className="text-right">
                      {formatPct(bet.true_prob_pct)}
                    </TableCell>
                    <TableCell className="text-right">
                      {formatSignedPct(edgePct(bet.true_prob_pct, bet.odds))}
                    </TableCell>
                    <TableCell className="text-right font-medium text-emerald-500">
                      {formatSignedPct(bet.ev_pct)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
