"use client";

import { Badge } from "@/components/ui/badge";
import { checkHealth } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";

export function HealthBadge() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["health"],
    queryFn: checkHealth,
    refetchInterval: 15_000,
    retry: false,
  });

  if (isPending) {
    return (
      <Badge variant="secondary" className="gap-1.5">
        <LoaderCircle className="h-3 w-3 animate-spin" />
        Checking API...
      </Badge>
    );
  }

  if (isError) {
    return (
      <Badge variant="destructive" className="gap-1.5">
        <CircleAlert className="h-3 w-3" />
        API offline
      </Badge>
    );
  }

  return (
    <Badge variant="outline" className="gap-1.5 border-emerald-500/40 text-emerald-500">
      <CircleCheck className="h-3 w-3" />
      API connected &middot; {data.models_loaded.length} models loaded
    </Badge>
  );
}
