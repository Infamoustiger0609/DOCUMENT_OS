"use client";

import { BarChart3, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

// Phase 28 (see CLAUDE.md's Analytics section). Deliberately has NO hardcoded
// section/metric names anywhere in this component — every label, category
// grouping, and number comes straight from POST /analytics/generate's
// response. The backend decides WHAT is meaningful (only from real, populated
// fields it actually found in this user's data); this page only knows how to
// render {label, value, format, category} generically.
interface AnalyticsMetric {
  label: string;
  value: number;
  format: "currency" | "count" | "percent";
  category: string;
}

interface AnalyticsGenerateResponse {
  metrics: AnalyticsMetric[];
  generated_at: string;
  cached: boolean;
}

// Every real document in this app so far is Indian-denominated (see
// CLAUDE.md's India-specific document intelligence section) — INR is a safe
// default for now rather than a real bug fix waiting to happen. This is a
// deliberate simplification, not full multi-currency support: a registry
// mixing Indian and non-Indian invoices would still sum/average their
// amounts together regardless of what symbol is shown (that was already
// true before this fix), since analytics.py's aggregation has no notion of
// currency at all — real multi-currency support would mean grouping
// aggregates by currency, a bigger change than this bug fix's scope. See
// CLAUDE.md's Analytics section for this tradeoff, noted explicitly.
const CURRENCY_FORMATTER = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatMetricValue(metric: AnalyticsMetric): string {
  if (metric.format === "currency") {
    return CURRENCY_FORMATTER.format(metric.value);
  }
  if (metric.format === "percent") {
    return `${Number.isInteger(metric.value) ? metric.value : metric.value.toFixed(1)}%`;
  }
  return Number.isInteger(metric.value) ? String(metric.value) : metric.value.toFixed(1);
}

function MetricTile({ metric }: { metric: AnalyticsMetric }) {
  return (
    <Card className="flex flex-col gap-2 px-5 py-4">
      <span className="text-xs font-medium uppercase tracking-wide text-ink-soft">{metric.label}</span>
      <span className="font-serif text-3xl font-semibold tabular-nums text-ink">{formatMetricValue(metric)}</span>
    </Card>
  );
}

function CategorySection({ category, metrics }: { category: string; metrics: AnalyticsMetric[] }) {
  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-ink">{category}</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {metrics.map((metric, index) => (
          <MetricTile key={index} metric={metric} />
        ))}
      </div>
    </div>
  );
}

export default function AnalyticsPage() {
  const { authFetch } = useAuth();
  const [data, setData] = useState<AnalyticsGenerateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (force: boolean) => {
      if (force) setRegenerating(true);
      else setLoading(true);
      setError(null);
      try {
        const res = await authFetch(`/analytics/generate${force ? "?force=true" : ""}`, { method: "POST" });
        if (!res.ok) throw new Error("Failed to generate analytics");
        setData(await res.json());
      } catch {
        setError("Could not generate analytics right now. Please try again.");
      } finally {
        setLoading(false);
        setRegenerating(false);
      }
    },
    [authFetch]
  );

  useEffect(() => {
    load(false);
    // Only ever run once on mount — regenerating is a deliberate user action
    // (the button below), not something that should re-fire on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Grouped by whatever `category` values the response actually contains —
  // not a fixed list, so a category with no metrics this time just doesn't
  // get a section, and a new category type shows up automatically.
  const sections = useMemo(() => {
    const byCategory = new Map<string, AnalyticsMetric[]>();
    for (const metric of data?.metrics ?? []) {
      const list = byCategory.get(metric.category) ?? [];
      list.push(metric);
      byCategory.set(metric.category, list);
    }
    return Array.from(byCategory.entries());
  }, [data]);

  return (
    <div className="flex flex-col gap-8 px-10 py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-ink">Analytics</h1>
          <p className="mt-1 text-sm text-ink-soft">
            Real metrics generated from what&apos;s actually in your registry.
          </p>
        </div>
        <Button type="button" variant="outline" onClick={() => load(true)} disabled={loading || regenerating}>
          <RefreshCw className={cn("h-4 w-4", regenerating && "animate-spin")} strokeWidth={1.75} />
          {regenerating ? "Regenerating..." : "Regenerate"}
        </Button>
      </div>

      {error && <p className="text-sm text-overdue">{error}</p>}

      {loading ? (
        <p className="text-sm text-ink-soft">
          Generating your analytics — this can take a little while the first time...
        </p>
      ) : sections.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-line bg-paper-raised px-6 py-20 text-center">
          <BarChart3 className="h-8 w-8 text-ink-soft" strokeWidth={1.5} />
          <p className="text-sm text-ink">Not enough data yet to generate analytics.</p>
          <p className="max-w-sm text-xs text-muted">
            Upload and process a few documents, then come back — these metrics are chosen from
            whatever&apos;s actually populated in your registry, not a fixed set of categories.
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-col gap-8">
            {sections.map(([category, metrics]) => (
              <CategorySection key={category} category={category} metrics={metrics} />
            ))}
          </div>
          {data && (
            <p className="text-xs text-muted">
              Generated {new Date(data.generated_at).toLocaleString()}
              {data.cached ? " (cached)" : ""}
            </p>
          )}
        </>
      )}
    </div>
  );
}
