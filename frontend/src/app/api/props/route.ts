import { fetchPlayerProjections } from "@/lib/api";
import { currentUser } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

/** BFF proxy: browser -> this route -> the internal ML predictive model.
 * Keeps ML_API_SECRET server-side and centralizes the subscription gate
 * that dashboard/layout.tsx already enforces for page navigation, so the
 * underlying data is also protected at the request level, not just the UI. */
export async function GET(request: NextRequest) {
  const user = await currentUser();
  if (user?.publicMetadata.stripe_subscription_status !== "active") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const playerId = request.nextUrl.searchParams.get("playerId") ?? undefined;

  try {
    const projections = await fetchPlayerProjections(playerId);
    return NextResponse.json(projections);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json(
      { error: `The ML prediction service failed to respond: ${message}` },
      { status: 500 }
    );
  }
}
