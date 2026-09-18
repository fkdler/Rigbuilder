/**
 * Terminal-styled chart configuration for the admin dashboard.
 *
 * This module is deliberately **free of any `echarts` import**.  It exports plain data
 * (a theme object and option builders), which keeps it unit-testable without booting a
 * renderer and guarantees it has no side effects — Plan V3.6 §7-4 requires that the
 * chart theme cannot leak into other pages.
 *
 * Why a custom theme is mandatory rather than cosmetic: `workbench-design-brief.md` §3
 * bans blue/purple gradients, glow, and showy visualisation, and the echarts default
 * palette violates the first of those outright.  A chart drawn with stock echarts would
 * be the only element in the product contradicting its own design brief.
 *
 * The hex values below are copies of `styles/terminal-tokens.css`, because echarts
 * cannot resolve CSS custom properties.  `chartTheme.spec.ts` reads that stylesheet and
 * asserts every value here still appears in it, so the two cannot drift apart silently.
 */

/** Terminal token values, mirrored from `styles/terminal-tokens.css`. */
export const CHART_COLORS = {
  bg: "#0e0f10",
  bgElevated: "#191a1c",
  bgOverlay: "#222426",
  fg: "#e5e5e5",
  fgDim: "#a2a6ad",
  fgDimmer: "#8b8f96",
  success: "#D97757",
  successDim: "#8c503d",
  warning: "#e8b563",
  warningDim: "#8a6f3d",
  error: "#d87870",
  errorDim: "#7d4943",
  cyan: "#D97757",
  magenta: "#d89bd6",
  yellow: "#dbc76e",
  border: "#292b2e",
  borderEmphasis: "#383b40",
  divider: "#222426",
  radiusSm: 6,
} as const;

/**
 * Series palette, in priority order.  Only low-saturation status/accent colours are
 * used; no blue-purple, no gradients.
 */
export const SERIES_PALETTE: readonly string[] = [
  CHART_COLORS.success,
  CHART_COLORS.cyan,
  CHART_COLORS.warning,
  CHART_COLORS.magenta,
  CHART_COLORS.error,
  CHART_COLORS.yellow,
];

const MONO_STACK = '"SF Mono", "Consolas", "Liberation Mono", Menlo, "Courier New", monospace';

/**
 * Base theme, applied via `echarts.registerTheme("rigbuilder-terminal", terminalTheme)`.
 *
 * `animation: false` is not an aesthetic preference: the global stylesheet already
 * disables transitions under `prefers-reduced-motion`, and an animated chart would be
 * the one place that ignores it.
 */
export const terminalTheme: Record<string, unknown> = {
  color: [...SERIES_PALETTE],
  backgroundColor: "transparent",
  animation: false,
  textStyle: { fontFamily: MONO_STACK, color: CHART_COLORS.fgDim },
  title: { textStyle: { color: CHART_COLORS.fg, fontFamily: MONO_STACK, fontWeight: 500 } },
  grid: { borderColor: CHART_COLORS.divider, containLabel: true },
  categoryAxis: {
    axisLine: { lineStyle: { color: CHART_COLORS.border } },
    axisTick: { show: false },
    axisLabel: { color: CHART_COLORS.fgDimmer, fontFamily: MONO_STACK, fontSize: 11 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: CHART_COLORS.fgDimmer, fontFamily: MONO_STACK, fontSize: 11 },
    splitLine: { lineStyle: { color: CHART_COLORS.divider } },
  },
  legend: { textStyle: { color: CHART_COLORS.fgDim, fontFamily: MONO_STACK, fontSize: 11 } },
  tooltip: {
    backgroundColor: CHART_COLORS.bgOverlay,
    borderColor: CHART_COLORS.borderEmphasis,
    borderWidth: 1,
    textStyle: { color: CHART_COLORS.fg, fontFamily: MONO_STACK, fontSize: 12 },
    extraCssText: `border-radius: ${CHART_COLORS.radiusSm}px;`,
  },
};

export interface CategoryPoint {
  label: string;
  /** `null` means "not recorded" and must render as a gap, never as 0. */
  value: number | null;
}

export interface CategoryBarOptions {
  /** Appended to the axis name and tooltip, e.g. "ms" or "次". */
  unit?: string;
  /** Horizontal bars read better when category names are long (error codes, event types). */
  horizontal?: boolean;
  color?: string;
}

function axisName(unit: string | undefined): string {
  return unit ? ` (${unit})` : "";
}

function tooltipFormatter(unit: string | undefined) {
  return (params: { name: string; value: number | null }[] | { name: string; value: number | null }) => {
    const list = Array.isArray(params) ? params : [params];
    return list
      .map((item) => {
        // A null must be stated as missing; printing 0 would invent a measurement.
        const shown = item.value === null || item.value === undefined ? "—" : `${item.value}${unit ? ` ${unit}` : ""}`;
        return `${item.name}: ${shown}`;
      })
      .join("<br/>");
  };
}

/** Bar chart over a small category set. The only chart type used for counts. */
export function categoryBarOption(points: CategoryPoint[], options: CategoryBarOptions = {}): Record<string, unknown> {
  const { unit, horizontal = false, color = CHART_COLORS.success } = options;
  const categories = points.map((point) => point.label);
  // Nulls are preserved through the mapping: echarts renders them as gaps.
  const values = points.map((point) => point.value);

  const categoryAxis = {
    type: "category" as const,
    data: categories,
    name: horizontal ? axisName(unit).trim() : undefined,
    nameTextStyle: { color: CHART_COLORS.fgDimmer, fontFamily: MONO_STACK },
  };
  const valueAxis = {
    type: "value" as const,
    name: horizontal ? undefined : axisName(unit).trim(),
    nameTextStyle: { color: CHART_COLORS.fgDimmer, fontFamily: MONO_STACK },
    minInterval: 1,
  };

  return {
    animation: false,
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
    tooltip: { trigger: "item", formatter: tooltipFormatter(unit) },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? categoryAxis : valueAxis,
    series: [{
      type: "bar",
      data: values,
      barMaxWidth: 22,
      itemStyle: { color, borderRadius: horizontal ? [0, 2, 2, 0] : [2, 2, 0, 0] },
    }],
  };
}

export interface DaySlice {
  day: string;
  runs: number;
  completed: number;
  failed: number;
}

/**
 * Runs per day, split into completed and failed.
 *
 * Stacked rather than two lines because the interesting question is the failure share on
 * a given day, which a stack shows directly and two lines do not.
 */
export function dayTrendOption(days: DaySlice[] | null | undefined): Record<string, unknown> {
  // The API returns days newest-first; a time axis reads oldest-first.
  const ordered = [...(days ?? [])].sort((a, b) => Date.parse(a.day) - Date.parse(b.day));
  return {
    animation: false,
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { top: 0, data: ["completed", "failed"] },
    xAxis: {
      type: "category",
      data: ordered.map((day) => day.day.slice(0, 10)),
      axisLabel: { color: CHART_COLORS.fgDimmer, fontFamily: MONO_STACK, fontSize: 10 },
    },
    yAxis: {
      type: "value",
      name: "runs",
      nameTextStyle: { color: CHART_COLORS.fgDimmer, fontFamily: MONO_STACK },
      minInterval: 1,
    },
    series: [
      {
        name: "completed", type: "bar", stack: "runs", barMaxWidth: 20,
        data: ordered.map((day) => day.completed),
        itemStyle: { color: CHART_COLORS.success },
      },
      {
        name: "failed", type: "bar", stack: "runs", barMaxWidth: 20,
        data: ordered.map((day) => day.failed),
        itemStyle: { color: CHART_COLORS.error },
      },
    ],
  };
}

/**
 * Longest-first ordering for horizontal category bars.
 *
 * Kept here rather than in the component so the dashboard's ordering rule is testable and
 * identical for error codes, event types and status counts.  `null` sorts last: a missing
 * measurement is not a small one.
 */
export function sortByValueDesc(points: CategoryPoint[] | null | undefined): CategoryPoint[] {
  return [...(points ?? [])].sort((a, b) => {
    if (a.value === null && b.value === null) return a.label.localeCompare(b.label);
    if (a.value === null) return 1;
    if (b.value === null) return -1;
    return b.value - a.value || a.label.localeCompare(b.label);
  });
}

/**
 * `{a: 1, b: 2}` -> sorted category points, for the API's dict-of-counts fields.
 *
 * Tolerates a missing map: the backend always sends these objects, but a render function
 * that throws on one absent key takes the whole page down, so the pure layer absorbs it
 * and every caller inherits the guarantee.
 */
export function countsToPoints(counts: Record<string, number> | null | undefined, limit?: number): CategoryPoint[] {
  const points = sortByValueDesc(Object.entries(counts ?? {}).map(([label, value]) => ({ label, value })));
  return limit === undefined ? points : points.slice(0, limit);
}
