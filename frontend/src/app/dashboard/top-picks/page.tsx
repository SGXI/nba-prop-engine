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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { getTopPicks } from "@/lib/api";
import {
  formatAmericanOdds,
  formatPct,
  formatSignedPct,
  formatUsd,
  impliedProbabilityPct,
  todayIso,
} from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

const FRACTIONAL_KELLY_OPTIONS = [
  { value: "1", label: "Full Kelly" },
  { value: "0.5", label: "0.5x Kelly" },
  { value: "0.25", label: "0.25x Kelly" },
] as const;

export default function TopPicksPage() {
  const [gameDate, setGameDate] = useState(todayIso());
  const [bankroll, setBankroll] = useState(1000);
  const [fractionalKelly, setFractionalKelly] = useState("0.25");

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["top-picks", gameDate, bankroll, fractionalKelly],
    queryFn: () =>
      getTopPicks({
        game_date: gameDate,
        bankroll,
        fractional_kelly: Number(fractionalKelly),
      }),
    enabled: bankroll > 0,
  });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Top 10 Picks</h1>
        <p className="text-sm text-muted-foreground">
          The slate&apos;s highest-EV bets, staked with fractional Kelly sizing against your
          bankroll.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Staking Controls</CardTitle>
          <CardDescription>
            Each pick&apos;s stake assumes it&apos;s the only bet placed -- staking all 10 at
            once will often exceed 100% of your bankroll.
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
            <label htmlFor="bankroll" className="text-sm font-medium">
              Bankroll
            </label>
            <div className="relative">
              <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-muted-foreground">
                $
              </span>
              <Input
                id="bankroll"
                type="number"
                min={0}
                step={50}
                value={bankroll}
                onChange={(e) => setBankroll(Number(e.target.value))}
                className="w-36 pl-5"
              />
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor="fractional-kelly" className="text-sm font-medium">
              Kelly Fraction
            </label>
            <Select
              value={fractionalKelly}
              onValueChange={(value) => {
                if (value) setFractionalKelly(value);
              }}
            >
              <SelectTrigger id="fractional-kelly" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FRACTIONAL_KELLY_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {isPending && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-44 w-full" />
          ))}
        </div>
      )}

      {isError && (
        <p className="text-sm text-destructive">
          {error instanceof Error ? error.message : "Failed to load top picks."}
        </p>
      )}

      {!isPending && !isError && data && (
        <>
          {data.exceeds_bankroll_if_staked_simultaneously && (
            <Card className="border-amber-500/40 bg-amber-500/5">
              <CardContent className="text-sm text-amber-500">
                Staking every pick shown here at once would commit{" "}
                <span className="font-semibold">
                  {formatUsd(data.total_recommended_wager)}
                </span>{" "}
                against a {formatUsd(bankroll)} bankroll -- more than 100%. Pick a subset,
                don&apos;t stake them all simultaneously.
              </CardContent>
            </Card>
          )}

          {data.picks.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No picks clear the {formatPct(data.min_edge)} EV threshold for {gameDate}.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {data.picks.map((pick) => {
                const impliedProb = impliedProbabilityPct(pick.odds);
                const wagerPctOfBankroll = (pick.recommended_wager / bankroll) * 100;

                return (
                  <Card
                    key={`${pick.player_name}-${pick.prop_type}-${pick.side}`}
                    className="ring-emerald-500/30"
                  >
                    <CardHeader>
                      <CardTitle className="flex items-center justify-between text-base">
                        {pick.player_name}
                        <Badge variant="outline">
                          {pick.prop_type} {pick.side}
                        </Badge>
                      </CardTitle>
                      <CardDescription>
                        Line {pick.line.toFixed(1)} &middot; {formatAmericanOdds(pick.odds)}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                      <div className="flex items-baseline justify-between">
                        <span className="text-xs text-muted-foreground">
                          Recommended Stake
                        </span>
                        <span className="text-right">
                          <span className="text-lg font-semibold">
                            {formatUsd(pick.recommended_wager)}
                          </span>{" "}
                          <span className="text-xs text-muted-foreground">
                            ({formatPct(wagerPctOfBankroll)} of bankroll)
                          </span>
                        </span>
                      </div>

                      <div className="flex items-baseline justify-between">
                        <span className="text-xs text-muted-foreground">
                          Implied vs. True Prob
                        </span>
                        <span className="text-sm">
                          {formatPct(impliedProb)}{" "}
                          <span className="text-muted-foreground">vs</span>{" "}
                          {formatPct(pick.true_prob_pct)}
                        </span>
                      </div>

                      <div className="flex items-baseline justify-between">
                        <span className="text-xs text-muted-foreground">
                          Expected Value
                        </span>
                        <span className="text-sm font-semibold text-emerald-500">
                          {formatSignedPct(pick.ev_pct)}
                        </span>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
