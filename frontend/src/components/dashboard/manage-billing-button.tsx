"use client";

import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { useState } from "react";

/** Sends the user to Stripe's hosted Billing Portal to manage or cancel
 * their subscription. Full-page redirect (not client-side navigation) since
 * the destination is Stripe's own domain, not a route in this app. */
export function ManageBillingButton() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch("/api/stripe/portal", { method: "POST" });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error ?? "Failed to open the billing portal.");
      }

      window.location.href = data.url;
      // Deliberately no `finally { setIsLoading(false) }` -- the button
      // should stay disabled through the full-page navigation away from
      // this app, not flash back to its idle state first.
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setIsLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button onClick={handleClick} disabled={isLoading} variant="outline">
        {isLoading ? (
          <>
            <Loader2 className="animate-spin" />
            Opening billing portal...
          </>
        ) : (
          "Manage Billing"
        )}
      </Button>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
