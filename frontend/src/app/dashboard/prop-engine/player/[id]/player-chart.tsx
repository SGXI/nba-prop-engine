"use client";

import type { GameLogEntry } from "@/types/models";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// Tailwind's emerald-500 / rose-500 -- Recharts needs a resolvable color
// string for SVG stroke, not a CSS class, so these mirror the same theme
// tokens used everywhere else in the dashboard (text-emerald-500 for
// positive figures, text-rose-500 for negative ones) rather than
// introducing new colors.
const ACTUAL_COLOR = "#10b981";
const PROJECTED_COLOR = "#f43f5e";

export function PlayerChart({ data }: { data: GameLogEntry[] }) {
  if (data.length === 0) {
    return (
      <div className="flex h-80 items-center justify-center text-sm text-muted-foreground">
        No recent game log available for this player.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis dataKey="game_date" tick={{ fontSize: 12 }} stroke="var(--muted-foreground)" />
        <YAxis tick={{ fontSize: 12 }} stroke="var(--muted-foreground)" />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--popover)",
            borderColor: "var(--border)",
            borderRadius: "var(--radius-lg)",
            fontSize: 12,
          }}
        />
        <Line
          type="monotone"
          dataKey="actual_points"
          name="Actual Points"
          stroke={ACTUAL_COLOR}
          strokeWidth={2}
          dot={{ r: 3 }}
        />
        <Line
          type="monotone"
          dataKey="projected_points"
          name="Model Projected Points"
          stroke={PROJECTED_COLOR}
          strokeWidth={2}
          strokeDasharray="4 4"
          dot={{ r: 3 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
