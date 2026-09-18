<script setup lang="ts">
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

import { terminalTheme } from "./chartTheme";

// Explicit `use([...])` rather than `import * as echarts from "echarts"`: the full bundle
// is 3.85 MB (dist/echarts.js measured), and this dashboard is lazy-loaded so the terminal
// route's first-screen bundle must not grow at all.
echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const props = withDefaults(defineProps<{
  option: Record<string, unknown>;
  /** Text summary for screen readers: the state must not be conveyed by colour alone. */
  label: string;
  height?: number;
}>(), { height: 220 });

const host = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

function render() {
  if (!host.value) return;
  if (!chart) {
    // The theme is passed per instance instead of via registerTheme, so it cannot leak
    // into any other chart in the application.
    chart = echarts.init(host.value, terminalTheme, { renderer: "canvas" });
  }
  // `notMerge` keeps a shrinking series list from leaving stale bars behind.
  chart.setOption(props.option, { notMerge: true });
}

onMounted(() => {
  render();
  // jsdom has no ResizeObserver; guard so unit tests can mount this component.
  if (typeof ResizeObserver !== "undefined" && host.value) {
    const observer = new ResizeObserver(() => chart?.resize());
    observer.observe(host.value);
    onBeforeUnmount(() => observer.disconnect());
  }
});

watch(() => props.option, render, { deep: true });

onBeforeUnmount(() => {
  // Without this, navigating away leaves the canvas and its listeners alive.
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <figure class="chart">
    <figcaption class="sr-only">{{ label }}</figcaption>
    <div ref="host" class="chart__canvas" :style="{ height: `${height}px` }" role="img" :aria-label="label" />
  </figure>
</template>

<style scoped>
.chart { margin: 0; }
.chart__canvas { width: 100%; }
</style>
