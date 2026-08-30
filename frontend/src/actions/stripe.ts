"use server";

import { stripe } from "@/lib/stripe";
import { auth } from "@clerk/nextjs/server";
import { headers } from "next/headers";
import { redirect } from "next/navigation";

/** Server Functions are reachable via direct POST requests, not just
 * through the pricing page's form -- so auth is re-checked here rather than
 * trusted from the caller. See Next.js's Server Functions security guidance. */
export async function createCheckoutSession() {
  const { userId } = await auth();
  if (!userId) {
    redirect("/dashboard");
  }

  const priceId = process.env.STRIPE_PRICE_ID;
  if (!priceId) {
    throw new Error("STRIPE_PRICE_ID is not configured.");
  }

  const host = (await headers()).get("host") ?? "localhost:3000";
  const protocol = host.startsWith("localhost") ? "http" : "https";
  const baseUrl = `${protocol}://${host}`;

  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    line_items: [{ price: priceId, quantity: 1 }],
    // Ties this Checkout session back to the signed-in Clerk user so the
    // webhook handler knows whose publicMetadata to update once payment
    // completes -- Stripe has no notion of a Clerk user on its own.
    client_reference_id: userId,
    success_url: `${baseUrl}/dashboard?checkout=success`,
    cancel_url: `${baseUrl}/pricing?checkout=cancelled`,
  });

  if (!session.url) {
    throw new Error("Stripe did not return a Checkout session URL.");
  }

  redirect(session.url);
}
