import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  CHART_COLORS, SERIES_PALETTE, categoryBarOption, countsToPoints, dayTrendOption,
  sortByValueDesc, terminalTheme,
} from "./chartTheme";

/** Flattened text of a config object, for "must never contain X" assertions. */
const asText = (value: unknown) => JSON.stringify(value);

/** Every series object anywhere in an option tree. */
function seriesOf(option: Record<string, unknown>): { type: string }[] {
  const series = option.series;
  if (Array.isArray(series)) return series as { type: string }[];
  return series ? [series as { type: string }] : [];
}

describe("terminal chart theme", () => {
  it("keeps every colour in sync with terminal-tokens.css", () => {
    // echarts cannot resolve CSS custom properties, so the hex values are duplicated.
    // This is the guard that stops the two copies drifting apart.
    // Resolved from the Vitest cwd (the frontend package root) rather than import.meta.url,
    // which under jsdom is not a file: URL.
    const css = readFileSync(resolve(process.cwd(), "src/styles/terminal-tokens.css"), "utf8");
    const hexes = Object.values(CHART_COLORS).filter(
      (value): value is string => typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value),
    );
    expect(hexes.length).toBeGreaterThan(10);
    for (const hex of hexes) {
      expect(css, `${hex} is not declared in terminal-tokens.css`).toContain(hex);
    }
  });

  it("carries no gradient and no shadow anywhere", () => {
    // workbench-design-brief.md §3 bans gradients and glow; echarts offers both by default.
    const text = asText([terminalTheme, categoryBarOption([{ label: "a", value: 1 }]), dayTrendOption([])]);
    expect(text).not.toContain("linearGradient");
    expect(text).not.toContain("radialGradient");
    expect(text).not.toContain("shadowBlur");
    expect(text).not.toContain("shadowColor");
  });

  it("disables animation so prefers-reduced-motion is not contradicted", () => {
    expect(terminalTheme.animation).toBe(false);
    expect(categoryBarOption([{ label: "a", value: 1 }]).animation).toBe(false);
    expect(dayTrendOption([]).animation).toBe(false);
  });

  it("only ever emits bar series", () => {
    // Brief §3 bans showy visualisation; §6 of the plan narrows this to bar/line.
    const options = [
      categoryBarOption([{ label: "a", value: 1 }]),
      categoryBarOption([{ label: "a", value: 1 }], { horizontal: true }),
      dayTrendOption([{ day: "2026-09-13T00:00:00Z", runs: 2, completed: 1, failed: 1 }]),
    ];
    for (const option of options) {
      for (const series of seriesOf(option)) {
        expect(["bar", "line"]).toContain(series.type);
      }
    }
  });

  it("themes the tooltip with terminal colours rather than echarts defaults", () => {
    const tooltip = terminalTheme.tooltip as Record<string, unknown>;
    expect(tooltip.backgroundColor).toBe(CHART_COLORS.bgOverlay);
    expect(tooltip.borderColor).toBe(CHART_COLORS.borderEmphasis);
  });

  it("uses only low-saturation token colours for series", () => {
    expect((terminalTheme.color as string[]).length).toBe(SERIES_PALETTE.length);
    for (const colour of terminalTheme.color as string[]) {
      expect(Object.values(CHART_COLORS)).toContain(colour);
    }
  });
});

describe("categoryBarOption", () => {
  it("shows a missing value as a gap instead of plotting zero", () => {
    const option = categoryBarOption([{ label: "empty_view", value: null }, { label: "gpu", value: 12 }]);
    expect((option.series as { data: unknown[] }[])[0].data).toEqual([null, 12]);
  });

  it("swaps the axes when horizontal", () => {
    const vertical = categoryBarOption([{ label: "a", value: 1 }]);
    const horizontal = categoryBarOption([{ label: "a", value: 1 }], { horizontal: true });
    expect((vertical.xAxis as { type: string }).type).toBe("category");
    expect((horizontal.yAxis as { type: string }).type).toBe("category");
    expect((horizontal.xAxis as { type: string }).type).toBe("value");
  });

  it("states a missing value as an em dash in the tooltip, never as 0", () => {
    const option = categoryBarOption([{ label: "a", value: null }], { unit: "ms" });
    const formatter = (option.tooltip as { formatter: (p: unknown) => string }).formatter;
    expect(formatter({ name: "a", value: null })).toBe("a: —");
    expect(formatter({ name: "a", value: 12 })).toBe("a: 12 ms");
  });
});

describe("dayTrendOption", () => {
  it("orders days oldest first even though the API returns them newest first", () => {
    const option = dayTrendOption([
      { day: "2026-09-13T00:00:00Z", runs: 2, completed: 2, failed: 0 },
      { day: "2026-09-11T00:00:00Z", runs: 5, completed: 3, failed: 2 },
    ]);
    expect((option.xAxis as { data: string[] }).data).toEqual(["2026-09-11", "2026-09-13"]);
  });

  it("stacks completed and failed so the failure share is readable", () => {
    const series = seriesOf(dayTrendOption([{ day: "2026-09-11T00:00:00Z", runs: 5, completed: 3, failed: 2 }]));
    expect(series.map((item) => (item as unknown as { name: string }).name)).toEqual(["completed", "failed"]);
    for (const item of series) {
      expect((item as unknown as { stack: string }).stack).toBe("runs");
    }
  });
});

describe("sortByValueDesc", () => {
  it("orders by value descending and puts missing values last", () => {
    const sorted = sortByValueDesc([
      { label: "missing", value: null },
      { label: "low", value: 1 },
      { label: "high", value: 9 },
    ]);
    expect(sorted.map((point) => point.label)).toEqual(["high", "low", "missing"]);
  });

  it("breaks ties by label so ordering is deterministic", () => {
    const sorted = sortByValueDesc([{ label: "b", value: 1 }, { label: "a", value: 1 }]);
    expect(sorted.map((point) => point.label)).toEqual(["a", "b"]);
  });
});

describe("countsToPoints", () => {
  it("converts the API's dict-of-counts into sorted points", () => {
    const points = countsToPoints({ fusion_failed: 27, job_failed: 2, router_unavailable: 1 });
    expect(points).toEqual([
      { label: "fusion_failed", value: 27 },
      { label: "job_failed", value: 2 },
      { label: "router_unavailable", value: 1 },
    ]);
  });

  it("keeps every category when no limit is given", () => {
    const counts = Object.fromEntries(Array.from({ length: 16 }, (_, index) => [`type_${index}`, 16 - index]));
    expect(countsToPoints(counts)).toHaveLength(16);
    expect(countsToPoints(counts, 5)).toHaveLength(5);
  });
});
