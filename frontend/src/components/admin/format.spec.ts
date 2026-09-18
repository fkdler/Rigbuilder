import { describe, expect, it } from "vitest";

import {
  MISSING, formatCoverage, formatDateTime, formatInt, formatMs, formatPercent, formatRatio,
  isPartial, metricCell,
} from "./format";

describe("missing is never rendered as zero", () => {
  it("renders every formatter's absent case as the missing marker", () => {
    // The backend reports an absent metrics key as null, not 0. Rendering it as 0 would
    // assert a measurement that was never taken.
    for (const value of [null, undefined]) {
      expect(formatInt(value)).toBe(MISSING);
      expect(formatRatio(value)).toBe(MISSING);
      expect(formatMs(value)).toBe(MISSING);
      expect(formatPercent(value)).toBe(MISSING);
      expect(formatCoverage(value)).toBe(MISSING);
      expect(formatDateTime(value)).toBe(MISSING);
    }
  });

  it("keeps a real zero distinct from a missing value", () => {
    expect(formatInt(0)).toBe("0");
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatCoverage(0)).toBe("0/3");
    expect(formatInt(0)).not.toBe(formatInt(null));
  });

  it("rejects non-finite numbers rather than printing NaN", () => {
    expect(formatInt(Number.NaN)).toBe(MISSING);
    expect(formatMs(Number.POSITIVE_INFINITY)).toBe(MISSING);
  });
});

describe("units", () => {
  it("scales durations so a column stays comparable", () => {
    expect(formatMs(742)).toBe("742 ms");
    expect(formatMs(1_400)).toBe("1.4 s");
    expect(formatMs(86_132)).toBe("1.4 min");
  });

  it("formats coverage on the configured three-Agent scale", () => {
    expect(formatCoverage(1)).toBe("3/3");
    expect(formatCoverage(2 / 3)).toBe("2/3");
  });

  it("formats a local timestamp without a timezone library", () => {
    expect(formatDateTime("2026-09-13T16:41:55Z")).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(formatDateTime("not-a-date")).toBe(MISSING);
  });
});

describe("metricCell", () => {
  it("reports the coverage count alongside the value", () => {
    const cell = metricCell(8_100, { llm_duration_ms: 107 }, "llm_duration_ms", 183, formatMs);
    expect(cell.text).toBe("8.1 s");
    expect(cell.recorded).toBe(true);
    expect(cell.covered).toBe(107);
    expect(cell.total).toBe(183);
  });

  it("flags a metric that only describes part of the window", () => {
    // Measured: six of the 33 keys appear in only 107-132 of 183 historical runs.
    expect(isPartial(metricCell(1, { agent_duration_ms: 107 }, "agent_duration_ms", 183))).toBe(true);
    expect(isPartial(metricCell(1, { agent_duration_ms: 183 }, "agent_duration_ms", 183))).toBe(false);
  });

  it("treats an absent key as missing rather than partial", () => {
    const cell = metricCell(null, { agent_duration_ms: 0 }, "agent_duration_ms", 183);
    expect(cell.text).toBe(MISSING);
    expect(cell.recorded).toBe(false);
    // A missing value is not "partial coverage of a real number": nothing was recorded.
    expect(isPartial(cell)).toBe(false);
  });

  it("does not claim partial coverage when the coverage map omits the key", () => {
    expect(isPartial(metricCell(5, undefined, "rounds_used", 10))).toBe(false);
  });
});
