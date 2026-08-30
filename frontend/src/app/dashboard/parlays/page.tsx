"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { getParlays } from "@/lib/api";
import { formatAmericanOdds, formatPct, formatSignedPct, todayIso } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

const LEG_OPTIONS = [2, 3, 4, 5] as const;

export default function ParlaysPage() {
  const [gameDate, setGameDate] = useState(todayIso());
  const [targetOdds, setTargetOdds] = useState(500);
  const [numLegs, setNumLegs] = useState(3);

  const targetOddsIsValid = targetOdds !== 0;

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["parlays", gameDate, targetOdds, numLegs],
    queryFn: () => getParlays({ game_date: gameDate, target_odds: targetOdds, num_legs: numLegs }),
    enabled: targetOddsIsValid,
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Parlay Builder</h1>
        <p className="text-sm text-muted-foreground">
          A greedy, uncorrelated parlay -- at most one leg per player -- built from the
          slate&apos;s highest-EV bets.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ticket Controls</CardTitle>
          <CardDescription>
            Stops the moment either the target odds are cleared or the leg count is
            reached, whichever comes first.
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

          <div className="flex flex-col gap-2">
            <label htmlFor="target-odds" className="text-sm font-medium">
              Target odds (American)
            </label>
            <Input
              id="target-odds"
              type="number"
              step={50}
              value={targetOdds}
              onChange={(e) => setTargetOdds(Number(e.target.value))}
              className="w-32"
              aria-invalid={!targetOddsIsValid}
            />
            {!targetOddsIsValid && (
              <span className="text-xs text-destructive">Target odds can&apos;t be 0.</span>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <span className="text-sm font-medium">Legs</span>
            <ToggleGroup
              value={[String(numLegs)]}
              onValueChange={(values) => {
                if (values[0]) setNumLegs(Number(values[0]));
              }}
              variant="outline"
            >
              {LEG_OPTIONS.map((legs) => (
                <ToggleGroupItem key={legs} value={String(legs)} className="w-10">
                  {legs}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        </CardContent>
      </Card>

      {isPending && targetOddsIsValid && (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      )}

      {isError && (
        <p className="text-sm text-destructive">
          {error instanceof Error ? error.message : "Failed to build a parlay."}
        </p>
      )}

      {!isPending && !isError && data && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              Parlay Ticket
              {data.legs.length > 0 && (
                <Badge variant={data.reached_target ? "default" : "secondary"}>
                  {data.reached_target ? "Target reached" : "Target not reached"}
                </Badge>
              )}
            </CardTitle>
            <CardDescription>
              Slate: {gameDate} &middot; Target {formatAmericanOdds(targetOdds)} &middot; up to{" "}
              {numLegs} legs
            </CardDescription>
          </CardHeader>

          <CardContent>
            {data.legs.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No positive-EV bets were available on this slate for {gameDate} -- a{" "}
                {formatAmericanOdds(targetOdds)} parlay isn&apos;t reachable.
              </p>
            ) : (
              <div className="flex flex-col">
                {data.legs.map((leg, i) => (
                  <div key={`${leg.player_name}-${leg.prop_type}-${leg.side}`}>
                    {i > 0 && <Separator />}
                    <div className="flex items-center justify-between py-3">
                      <div className="flex flex-col gap-1">
                        <span className="text-sm font-medium">{leg.player_name}</span>
                        <div className="flex items-center gap-2">
                          <Badge variant="outline">
                            {leg.prop_type} {leg.side} {leg.line.toFixed(1)}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            True prob {formatPct(leg.true_prob_pct)}
                          </span>
                        </div>
                      </div>
                      <span className="text-sm font-medium">
                        {formatAmericanOdds(leg.odds)}
                      </span>
                    </div>
                  </div>
                ))}

                {!data.reached_target && data.count === numLegs && (
                  <p className="pt-2 text-xs text-muted-foreground">
                    Ran out of legs at the {numLegs}-leg cap before reaching{" "}
                    {formatAmericanOdds(targetOdds)}; combined odds only reached{" "}
                    {data.combined_odds !== null && formatAmericanOdds(data.combined_odds)}.
                  </p>
                )}
              </div>
            )}
          </CardContent>

          {data.legs.length > 0 && (
            <CardFooter className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex flex-col">
                <span className="text-xs text-muted-foreground">Combined Odds</span>
                <span className="text-lg font-semibold">
                  {data.combined_odds !== null && formatAmericanOdds(data.combined_odds)}
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-xs text-muted-foreground">Combined True Prob</span>
                <span className="text-lg font-semibold">
                  {data.combined_true_probability_pct !== null &&
                    formatPct(data.combined_true_probability_pct)}
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-xs text-muted-foreground">Total EV</span>
                <span className="text-lg font-semibold text-emerald-500">
                  {data.total_ev_pct !== null && formatSignedPct(data.total_ev_pct)}
                </span>
              </div>
            </CardFooter>
          )}
        </Card>
      )}
    </div>
  );
}
