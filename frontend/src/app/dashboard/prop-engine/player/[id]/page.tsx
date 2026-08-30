import { fetchPlayerProjections } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PlayerChart } from "./player-chart";

export default async function PlayerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let player;
  try {
    const { projections } = await fetchPlayerProjections(id);
    player = projections[0];
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to load this player.";
    return (
      <div className="flex flex-col gap-6">
        <BackLink />
        <Card>
          <CardContent className="text-sm text-destructive">{message}</CardContent>
        </Card>
      </div>
    );
  }

  if (!player) {
    notFound();
  }

  return (
    <div className="flex flex-col gap-6">
      <BackLink />

      <div>
        <h1 className="text-xl font-semibold tracking-tight">{player.player_name}</h1>
        <p className="text-sm text-muted-foreground">
          {player.team} &middot; {player.matchup} &middot; {player.game_date}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Points: Actual vs. Model Projected</CardTitle>
        </CardHeader>
        <CardContent>
          <PlayerChart data={player.recent_games} />
        </CardContent>
      </Card>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      href="/dashboard/prop-engine"
      className="flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" />
      Back to Board
    </Link>
  );
}
