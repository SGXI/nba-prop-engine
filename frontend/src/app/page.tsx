import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Show } from "@clerk/nextjs";
import { LineChart } from "lucide-react";
import Link from "next/link";

export default function Home() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 text-center">
      <div className="flex items-center gap-2 text-muted-foreground">
        <LineChart className="h-5 w-5" />
        <span className="text-sm font-medium tracking-tight">NBA +EV Engine</span>
      </div>

      <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-balance">
        Daily +EV boards, Kelly-sized picks, and parlay building for NBA player props.
      </h1>
      <p className="max-w-xl text-muted-foreground">
        Model-driven projections, live market lines, and a full bet ledger -- all in one
        dashboard.
      </p>

      <Show when="signed-out">
        <Link href="/dashboard" className={cn(buttonVariants({ size: "lg" }))}>
          Sign In
        </Link>
      </Show>
      <Show when="signed-in">
        <Link href="/dashboard" className={cn(buttonVariants({ size: "lg" }))}>
          Go to Dashboard
        </Link>
      </Show>
    </div>
  );
}
