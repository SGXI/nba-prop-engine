/**
 * Typed fetch helpers for the FastAPI backend. One function per endpoint in
 * src/nbabettingmodel/api/v1/, plus checkHealth() for the root `/` route
 * main.py's dashboard layout polls for connectivity.
 */

import type {
  EVBoardResponse,
  HealthResponse,
  LogBetPayload,
  LogBetResponse,
  ParlayTicket,
  PlayerProjection,
  PnLSummary,
  StreaksResponse,
  TopPicksResponse,
} from "@/lib/types";
import type { PlayerProjectionsResponse } from "@/types/models";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const API_V1 = `${API_BASE_URL}/api/v1`;

/** Drops undefined values so callers can pass optional filters straight
 * through without manually building a query string. */
function toQueryString(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

/** Thrown for any non-2xx API response. Carries the HTTP status so callers
 * can branch on it (e.g. a 404 from GET /player/{name} means "never tracked"
 * -- see that route's docstring -- and deserves a friendlier message than a
 * generic 500). */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new Error(
      `Could not reach the API at ${API_BASE_URL}. Is the backend running?`
    );
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail ?? response.statusText;
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}

export function checkHealth(): Promise<HealthResponse> {
  return apiFetch(`${API_BASE_URL}/`);
}

export function getEVBoard(params?: { game_date?: string }): Promise<EVBoardResponse> {
  return apiFetch(`${API_V1}/ev-board${toQueryString({ ...params })}`);
}

export function getTopPicks(params?: {
  game_date?: string;
  min_edge?: number;
  bankroll?: number;
  fractional_kelly?: number;
}): Promise<TopPicksResponse> {
  return apiFetch(`${API_V1}/top-picks${toQueryString({ ...params })}`);
}

export function getParlays(params?: {
  game_date?: string;
  target_odds?: number;
  num_legs?: number;
}): Promise<ParlayTicket> {
  return apiFetch(`${API_V1}/parlays${toQueryString({ ...params })}`);
}

export function getStreaks(): Promise<StreaksResponse> {
  return apiFetch(`${API_V1}/streaks`);
}

export function getPlayer(
  playerName: string,
  params?: { game_date?: string }
): Promise<PlayerProjection> {
  return apiFetch(
    `${API_V1}/player/${encodeURIComponent(playerName)}${toQueryString({ ...params })}`
  );
}

export function getPnL(params?: { top_10_only?: boolean }): Promise<PnLSummary> {
  return apiFetch(`${API_V1}/tracker/pnl${toQueryString({ ...params })}`);
}

export function logBet(payload: LogBetPayload): Promise<LogBetResponse> {
  return apiFetch(`${API_V1}/tracker/log`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Server-only: internal ML predictive model client.
//
// Unlike everything above (called directly from Client Components against
// the CORS-enabled FastAPI backend), this hits a separate internal service
// that requires a secret bearer token -- ML_API_SECRET has no NEXT_PUBLIC_
// prefix, so Next.js never inlines it into a client bundle; calling this
// from a Client Component won't leak the secret, but it will silently send
// `Authorization: Bearer undefined` and fail. This is meant to be called
// only from src/app/api/props/route.ts.
// ---------------------------------------------------------------------------

const ML_BACKEND_URL =
  process.env.ML_BACKEND_URL ?? "http://localhost:8080/api/predictions/props";

export async function fetchPlayerProjections(
  playerId?: string
): Promise<PlayerProjectionsResponse> {
  const url = new URL(ML_BACKEND_URL);
  if (playerId) url.searchParams.set("playerId", playerId);

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      headers: { Authorization: `Bearer ${process.env.ML_API_SECRET}` },
      // Next.js Data Cache: reuse the response for an hour instead of
      // hitting the ML backend on every request.
      next: { revalidate: 3600 },
    });
  } catch {
    throw new Error(`Could not reach the ML backend at ${ML_BACKEND_URL}.`);
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<PlayerProjectionsResponse>;
}
