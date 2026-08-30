import { createCheckoutSession } from "@/actions/stripe";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { LineChart, Check } from "lucide-react";

const FEATURES = [
  "Daily +EV board across the full NBA slate",
  "Kelly-sized Top 10 picks",
  "Uncorrelated parlay builder",
  "Player prop streak tracking",
  "Player projections vs. live market lines",
  "Full bet ledger with PnL and ROI",
];

export default function PricingPage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 py-16 text-center">
      <div className="flex items-center gap-2 text-muted-foreground">
        <LineChart className="h-5 w-5" />
        <span className="text-sm font-medium tracking-tight">NBA +EV Engine</span>
      </div>

      <h1 className="max-w-xl text-3xl font-semibold tracking-tight text-balance">
        Subscribe to unlock the dashboard
      </h1>
      <p className="max-w-md text-muted-foreground">
        Your account is signed in, but doesn&apos;t have an active subscription yet.
      </p>

      <Card className="w-full max-w-sm text-left">
        <CardHeader>
          <CardTitle className="text-base">Pro</CardTitle>
          <CardDescription>Full access to every dashboard feature.</CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-2">
            {FEATURES.map((feature) => (
              <li key={feature} className="flex items-start gap-2 text-sm">
                <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                <span>{feature}</span>
              </li>
            ))}
          </ul>
        </CardContent>
        <CardFooter>
          <form action={createCheckoutSession} className="w-full">
            <Button type="submit" size="lg" className="w-full">
              Subscribe
            </Button>
          </form>
        </CardFooter>
      </Card>
    </div>
  );
}
