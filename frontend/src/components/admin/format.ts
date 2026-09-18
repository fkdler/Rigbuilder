/**
 * Rendering rules for admin metrics.
 *
 * One rule dominates every function here: **a missing measurement is never rendered as
 * zero.**  The backend reports an absent `agent_run.metrics` key as `null` rather than 0
 * (six of the 33 keys appear in only a subset of historical runs), and `metric_coverage`
 * says how many runs carried it.  Printing `0` for a key the writer never emitted would
 * state a measurement that was never taken, which is the failure mode Plan V3.5 §5.3 and
 * Plan V3.6 §5.2 both exist to prevent.
 */

/** Shown wherever a value is absent. Deliberately not "0" and not blank. */
export const MISSING = "—";

export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  return Math.round(value).toLocaleString("en-US");
}

export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  return value.toFixed(digits);
}

/** Millisecond durations, scaled to s / ms so a column stays narrow and comparable. */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  if (value >= 60_000) return `${(value / 60_000).toFixed(1)} min`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)} s`;
  return `${Math.round(value)} ms`;
}

/**
 * A 0..1 rate as a percentage.  `rate === null` happens when the denominator is zero
 * (no runs in the window), which is not the same as a 0% success rate.
 */
export function formatPercent(rate: number | null | undefined, digits = 1): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return MISSING;
  return `${(rate * 100).toFixed(digits)}%`;
}

/** `agent_coverage` is `null` for a non-fusion job and `0` for a fusion that found nothing. */
export function formatCoverage(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return MISSING;
  return `${Math.round(ratio * 3)}/3`;
}

export interface MetricCell {
  /** Formatted value, or the missing marker. */
  text: string;
  /** False when the key was absent from every run in the window. */
  recorded: boolean;
  /** Runs that carried the key, out of the window's total. */
  covered?: number;
  total?: number;
}

/**
 * Pair a metric value with its coverage, so the two can never be read apart.
 *
 * `metric_coverage` counts the runs that carried the key.  When that count is 0 the value
 * itself will be `null` and is reported as missing; when it is lower than the run count the
 * value is real but describes a subset, and the caller is expected to say so on screen.
 */
export function metricCell(
  value: number | null | undefined,
  coverage: Record<string, number> | undefined,
  key: string,
  totalRuns: number,
  format: (value: number | null | undefined) => string = formatInt,
): MetricCell {
  const covered = coverage?.[key];
  const recorded = value !== null && value !== undefined;
  return {
    text: recorded ? format(value) : MISSING,
    recorded,
    covered,
    total: totalRuns,
  };
}

/** True when a coverage count shows the metric describes only part of the window. */
export function isPartial(cell: MetricCell): boolean {
  return cell.recorded && cell.covered !== undefined && cell.covered < cell.total;
}

/** `2026-09-13T16:41:55Z` -> `09-13 16:41`, in local time, without a timezone library. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return MISSING;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return MISSING;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}
