import { getBaseUrl, stripe } from "@/lib/stripe";
import { currentUser } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

/** Creates a Stripe Billing Portal session so a signed-in subscriber can
 * manage or cancel their subscription. The customer ID comes from
 * publicMetadata.stripe_customer_id -- the same field the checkout webhook
 * (src/app/api/webhooks/stripe/route.ts) writes on checkout.session.completed.
 *
 * Uses currentUser() rather than auth()'s session claims for the same
 * reason dashboard/layout.tsx's subscription gate does: it reads
 * publicMetadata fresh from Clerk's Backend API instead of a JWT that only
 * refreshes periodically. */
export async function POST() {
  const user = await currentUser();
  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const customerId = user.publicMetadata.stripe_customer_id;
  if (typeof customerId !== "string" || customerId.length === 0) {
    return NextResponse.json(
      { error: "No Stripe customer on file for this user." },
      { status: 400 }
    );
  }

  try {
    const portalSession = await stripe.billingPortal.sessions.create({
      customer: customerId,
      return_url: `${await getBaseUrl()}/dashboard`,
    });

    return NextResponse.json({ url: portalSession.url });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json(
      { error: `Failed to create billing portal session: ${message}` },
      { status: 500 }
    );
  }
}
