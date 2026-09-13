import { headers } from "next/headers";
import Stripe from "stripe";

/** Server-only Stripe client, shared by the checkout Server Action, the
 * billing portal route, and the webhook route handler. Never import this
 * from a Client Component -- it reads STRIPE_SECRET_KEY, which has no
 * NEXT_PUBLIC_ prefix on purpose. */
export const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

/** This app's own origin, derived from the request's Host header rather
 * than a hardcoded env var -- works unchanged in local dev and in
 * production. Shared by every Stripe flow that needs a success/return URL
 * (checkout's success_url/cancel_url, the billing portal's return_url). */
export async function getBaseUrl(): Promise<string> {
  const host = (await headers()).get("host") ?? "localhost:3000";
  const protocol = host.startsWith("localhost") ? "http" : "https";
  return `${protocol}://${host}`;
}
