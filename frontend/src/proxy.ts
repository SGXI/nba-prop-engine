import { clerkMiddleware } from "@clerk/nextjs/server";

// Next.js 16 renamed the "middleware" file convention to "proxy" (same
// request-interception behavior, new file name/export) -- see
// node_modules/next/dist/docs/01-app/01-getting-started/16-proxy.md.
//
// This is an optimistic, edge-level redirect only -- Clerk's own guidance
// (Core 3, March 2026) is that path-matching-based middleware auth "can
// diverge from how Next.js routes requests and leave protected resources
// reachable," so `createRouteMatcher` is deprecated in favor of resource-
// based checks. The authoritative check lives in
// src/app/dashboard/layout.tsx's `await auth.protect()`; this file exists
// purely so a signed-out user gets redirected before the dashboard's React
// tree starts rendering, instead of after.
export default clerkMiddleware(async (auth, req) => {
  if (req.nextUrl.pathname.startsWith("/dashboard")) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
