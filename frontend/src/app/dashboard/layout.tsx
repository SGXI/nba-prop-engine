import { HealthBadge } from "@/components/dashboard/health-badge";
import { SidebarNav } from "@/components/dashboard/sidebar-nav";
import { UserButton } from "@clerk/nextjs";
import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // The authoritative auth check for the whole /dashboard route group.
  // proxy.ts's clerkMiddleware redirect is only an optimistic, path-matching
  // based fast path (Clerk's own guidance: middleware auth "can diverge from
  // how Next.js routes requests and leave protected resources reachable") --
  // this is what actually guarantees no page under /dashboard renders for a
  // signed-out user.
  await auth.protect();

  // Subscription gate. currentUser() hits the Backend API directly rather
  // than reading the (JWT-cached) session claims, so a user redirected here
  // straight from a successful Stripe Checkout sees their just-written
  // publicMetadata immediately instead of a stale pre-webhook value.
  const user = await currentUser();
  if (user?.publicMetadata.stripe_subscription_status !== "active") {
    redirect("/pricing");
  }

  return (
    <div className="flex min-h-screen flex-1">
      <aside className="hidden w-60 shrink-0 border-r border-border md:flex md:flex-col">
        <div className="px-4 py-5">
          <span className="text-sm font-semibold tracking-tight">
            NBA +EV Engine
          </span>
        </div>
        <SidebarNav />
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-border px-4 md:px-6">
          <span className="text-sm font-medium text-muted-foreground md:hidden">
            NBA +EV Engine
          </span>
          <div className="ml-auto flex items-center gap-4">
            <HealthBadge />
            <UserButton />
          </div>
        </header>

        <main className="flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
