"use client";

import { Button } from "@/components/ui/button";
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
import { ApiError, getPnL, logBet } from "@/lib/api";
import { formatPct, formatUsd, todayIso } from "@/lib/format";
import type { BetSide, LogBetPayload, PropType } from "@/lib/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

const PROPS: PropType[] = ["PTS", "AST", "REB", "PRA"];
const SIDES: BetSide[] = ["Over", "Under"];

const PNL_QUERY_KEY = ["pnl"];

interface FormState {
  date: string;
  player_name: string;
  prop: PropType;
  side: BetSide;
  line: string;
  odds: string;
  wager_amount: string;
}

function emptyForm(): FormState {
  return {
    date: todayIso(),
    player_name: "",
    prop: "PTS",
    side: "Over",
    line: "",
    odds: "",
    wager_amount: "",
  };
}

export default function BetTrackerPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(emptyForm());
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(
    null
  );

  const { data: pnl, isPending: pnlPending, isError: pnlError } = useQuery({
    queryKey: PNL_QUERY_KEY,
    queryFn: () => getPnL(),
  });

  const mutation = useMutation({
    mutationFn: (payload: LogBetPayload) => logBet(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PNL_QUERY_KEY });
      setFeedback({ type: "success", message: "Bet logged as Pending." });
      setForm(emptyForm());
    },
    onError: (err) => {
      const message =
        err instanceof ApiError || err instanceof Error
          ? err.message
          : "Failed to log this bet.";
      setFeedback({ type: "error", message });
    },
  });

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setFeedback(null);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const line = Number(form.line);
    const odds = Number(form.odds);
    const wager = Number(form.wager_amount);

    if (!form.player_name.trim() || Number.isNaN(line) || Number.isNaN(odds) || Number.isNaN(wager) || odds === 0) {
      setFeedback({ type: "error", message: "Fill in every field with valid values (odds can't be 0)." });
      return;
    }

    mutation.mutate({
      date: form.date,
      player_name: form.player_name.trim(),
      prop: form.prop,
      side: form.side,
      line,
      odds,
      wager_amount: wager,
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Bet Tracker</h1>
        <p className="text-sm text-muted-foreground">
          PnL, win rate, and ROI across every bet logged in the ledger.
        </p>
      </div>

      {pnlPending && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      )}

      {pnlError && (
        <p className="text-sm text-destructive">Failed to load the PnL summary.</p>
      )}

      {pnl && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader>
                <CardDescription>ROI</CardDescription>
                <CardTitle
                  className={`text-2xl ${pnl.roi_pct >= 0 ? "text-emerald-500" : "text-rose-500"}`}
                >
                  {formatPct(pnl.roi_pct)}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>Total Net P/L</CardDescription>
                <CardTitle
                  className={`text-2xl ${pnl.total_pnl >= 0 ? "text-emerald-500" : "text-rose-500"}`}
                >
                  {formatUsd(pnl.total_pnl)}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>Total Wagered</CardDescription>
                <CardTitle className="text-2xl">{formatUsd(pnl.total_wagered)}</CardTitle>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>Win Rate</CardDescription>
                <CardTitle className="text-2xl">{formatPct(pnl.win_pct)}</CardTitle>
              </CardHeader>
            </Card>
          </div>

          <p className="text-xs text-muted-foreground">
            {pnl.total_bets} total bets &middot; {pnl.wins}W-{pnl.losses}L &middot;{" "}
            {pnl.pushes} push &middot; {pnl.voids} void &middot; {pnl.pending} pending
          </p>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Log New Bet</CardTitle>
          <CardDescription>Recorded as Pending until the game is graded.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="flex flex-col gap-2">
                <label htmlFor="bet-date" className="text-sm font-medium">
                  Game date
                </label>
                <Input
                  id="bet-date"
                  type="date"
                  value={form.date}
                  onChange={(e) => updateField("date", e.target.value)}
                  required
                />
              </div>

              <div className="flex flex-col gap-2 lg:col-span-2">
                <label htmlFor="bet-player" className="text-sm font-medium">
                  Player
                </label>
                <Input
                  id="bet-player"
                  placeholder="e.g. LeBron James"
                  value={form.player_name}
                  onChange={(e) => updateField("player_name", e.target.value)}
                  required
                />
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="bet-prop" className="text-sm font-medium">
                  Prop
                </label>
                <Select
                  value={form.prop}
                  onValueChange={(v) => {
                    if (v) updateField("prop", v as PropType);
                  }}
                >
                  <SelectTrigger id="bet-prop" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PROPS.map((prop) => (
                      <SelectItem key={prop} value={prop}>
                        {prop}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="bet-side" className="text-sm font-medium">
                  Side
                </label>
                <Select
                  value={form.side}
                  onValueChange={(v) => {
                    if (v) updateField("side", v as BetSide);
                  }}
                >
                  <SelectTrigger id="bet-side" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SIDES.map((side) => (
                      <SelectItem key={side} value={side}>
                        {side}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="bet-line" className="text-sm font-medium">
                  Line
                </label>
                <Input
                  id="bet-line"
                  type="number"
                  step={0.5}
                  value={form.line}
                  onChange={(e) => updateField("line", e.target.value)}
                  required
                />
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="bet-odds" className="text-sm font-medium">
                  Odds (American)
                </label>
                <Input
                  id="bet-odds"
                  type="number"
                  step={5}
                  placeholder="-110"
                  value={form.odds}
                  onChange={(e) => updateField("odds", e.target.value)}
                  required
                />
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="bet-wager" className="text-sm font-medium">
                  Stake
                </label>
                <div className="relative">
                  <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-muted-foreground">
                    $
                  </span>
                  <Input
                    id="bet-wager"
                    type="number"
                    min={0}
                    step={5}
                    value={form.wager_amount}
                    onChange={(e) => updateField("wager_amount", e.target.value)}
                    className="pl-5"
                    required
                  />
                </div>
              </div>
            </div>

            {feedback && (
              <p
                className={`text-sm ${feedback.type === "success" ? "text-emerald-500" : "text-destructive"}`}
              >
                {feedback.message}
              </p>
            )}

            <div>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? "Logging..." : "Log Bet"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
