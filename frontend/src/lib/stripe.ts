import Stripe from "stripe";

/** Server-only Stripe client, shared by the checkout Server Action and the
 * webhook route handler. Never import this from a Client Component -- it
 * reads STRIPE_SECRET_KEY, which has no NEXT_PUBLIC_ prefix on purpose. */
export const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);
