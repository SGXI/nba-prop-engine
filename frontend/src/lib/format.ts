/** Shared formatting/derivation helpers for betting figures used across
 * dashboard pages. */

/** Today's date as 'YYYY-MM-DD', the format every /api/v1 endpoint expects
 * for `game_date`. Every dashboard page defaults its date control to this. */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function formatAmericanOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

export function formatPct(value: number, digits = 1): string {
  return `${value.toFixed(digits)}%`;
}

export function formatSignedPct(value: number, digits = 1): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function formatUsd(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

/** Breakeven win probability implied by American odds (the vig-inclusive
 * probability a book needs to be right for this bet to be a wash). */
export function impliedProbabilityPct(odds: number): number {
  const decimal = odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds);
  return (1 / decimal) * 100;
}

/** A bet's "edge": how much higher the model's true win probability is than
 * the market's break-even (implied) probability. Distinct from EV%, which
 * also folds in the size of the payout -- edge is purely a probability gap. */
export function edgePct(trueProbPct: number, odds: number): number {
  return trueProbPct - impliedProbabilityPct(odds);
}
