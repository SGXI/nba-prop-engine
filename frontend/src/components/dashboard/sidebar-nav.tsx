"use client";

import { cn } from "@/lib/utils";
import {
  LineChart,
  Trophy,
  Layers,
  Flame,
  UserSearch,
  Wallet,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/dashboard", label: "+EV Board", icon: LineChart },
  { href: "/dashboard/top-picks", label: "Top 10 Picks", icon: Trophy },
  { href: "/dashboard/parlays", label: "Parlay Builder", icon: Layers },
  { href: "/dashboard/streaks", label: "Streaks", icon: Flame },
  { href: "/dashboard/player", label: "Player Explorer", icon: UserSearch },
  { href: "/dashboard/tracker", label: "Bet Tracker", icon: Wallet },
  { href: "/dashboard/prop-engine", label: "Prop Engine Pro", icon: Sparkles },
] as const;

export function SidebarNav() {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-1 p-3">
      {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
        const isActive =
          href === "/dashboard" ? pathname === href : pathname.startsWith(href);

        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
