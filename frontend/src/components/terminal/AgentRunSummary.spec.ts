import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { QueryEvent } from "@/api/queryJobs";
import AgentRunSummary from "./AgentRunSummary.vue";

/**
 * Regression guard for the mislabelling bug.
 *
 * The heading used to be the constant string "Verified fusion complete" whenever
 * the component had any agent row, and a chat turn produced one because its
 * `llm_completed` event carries a `model`. Measured: a "recommend a whole gaming
 * PC" prompt was answered by the fast path — no database query, no truth
 * verification — while the interface claimed verified fusion.
 */
function event(partial: Partial<QueryEvent>): QueryEvent {
  return {
    sequence: 1, event_type: "llm_completed", phase: "llm", status: "completed",
    title: "t", created_at: "2026-09-14T00:00:00Z", ...partial,
  } as QueryEvent;
}

const FUSION_LABEL = "Verified fusion complete";
const FAST_LABEL = "Fast answer · unverified";

describe("AgentRunSummary", () => {
  it("never calls a fast answer a verified fusion", () => {
    const wrapper = mount(AgentRunSummary, {
      props: {
        running: false,
        mode: "chat",
        // A chat turn really does emit this, with the model name attached.
        events: [event({ event_type: "model_loading", model: undefined }),
                 event({ event_type: "llm_completed", model: "agent-a" })],
      },
    });
    expect(wrapper.text()).toContain(FAST_LABEL);
    expect(wrapper.text()).not.toContain(FUSION_LABEL);
    // No fabricated agent row for a path that has no agents.
    expect(wrapper.findAll(".agent-row")).toHaveLength(0);
    expect(wrapper.text()).toContain("没有查询数据库");
  });

  it("keeps the fusion label and the agent rows for a fusion turn", () => {
    const wrapper = mount(AgentRunSummary, {
      props: {
        running: false,
        mode: "fusion",
        events: [
          event({ event_type: "agent_started", phase: "agent", agent: "agent-a", model: "agent-a" }),
          event({ event_type: "agent_completed", phase: "agent", agent: "agent-a", model: "agent-a" }),
          event({ event_type: "agent_started", phase: "agent", agent: "agent-b", model: "agent-b" }),
          event({ event_type: "agent_completed", phase: "agent", agent: "agent-b", model: "agent-b" }),
        ],
      },
    });
    expect(wrapper.text()).toContain(FUSION_LABEL);
    expect(wrapper.text()).not.toContain(FAST_LABEL);
    expect(wrapper.findAll(".agent-row")).toHaveLength(2);
  });

  it("does not assume fusion when a job predates resolved_mode", () => {
    const wrapper = mount(AgentRunSummary, {
      props: {
        running: false,
        mode: null,
        events: [event({ event_type: "llm_completed", model: "agent-a" })],
      },
    });
    // Fallback reads the events, not wishful thinking.
    expect(wrapper.text()).not.toContain(FUSION_LABEL);
    expect(wrapper.text()).toContain(FAST_LABEL);
  });

  it("recognises fusion from the events when resolved_mode is absent", () => {
    const wrapper = mount(AgentRunSummary, {
      props: {
        running: false,
        mode: null,
        events: [event({ event_type: "agent_started", phase: "agent", agent: "agent-a", model: "agent-a" })],
      },
    });
    expect(wrapper.text()).toContain(FUSION_LABEL);
  });

  it("marks a running fast answer as in progress rather than verified", () => {
    const wrapper = mount(AgentRunSummary, {
      props: { running: true, mode: "chat", events: [event({ event_type: "llm_started" })] },
    });
    expect(wrapper.text()).toContain("Fast answer in progress");
    expect(wrapper.text()).not.toContain(FUSION_LABEL);
  });

  it("renders nothing when there is no activity at all", () => {
    const wrapper = mount(AgentRunSummary, { props: { running: false, mode: "fusion", events: [] } });
    expect(wrapper.find(".agent-run").exists()).toBe(false);
  });

  it("states how many Agents the request was worth", () => {
    // Two vs three Agents is the main reason one fusion ends in ~60 s and another
    // in ~90 s; without the count that difference reads as random slowness.
    const wrapper = mount(AgentRunSummary, {
      props: {
        running: false,
        mode: "fusion",
        events: [
          event({ event_type: "fusion_started", phase: "fusion", detail: { agent_count: 2 } }),
          event({ event_type: "agent_started", phase: "agent", agent: "agent-a", model: "agent-a" }),
          event({ event_type: "agent_completed", phase: "agent", agent: "agent-a", model: "agent-a" }),
          event({ event_type: "agent_started", phase: "agent", agent: "agent-b", model: "agent-b" }),
          event({ event_type: "agent_completed", phase: "agent", agent: "agent-b", model: "agent-b" }),
        ],
      },
    });
    expect(wrapper.find(".run-heading__count").text()).toContain("2 agents");
  });

  it("falls back to the rendered rows when the event carries no count", () => {
    const wrapper = mount(AgentRunSummary, {
      props: {
        running: false,
        mode: "fusion",
        events: [
          event({ event_type: "agent_started", phase: "agent", agent: "agent-a", model: "agent-a" }),
          event({ event_type: "agent_completed", phase: "agent", agent: "agent-a", model: "agent-a" }),
        ],
      },
    });
    expect(wrapper.find(".run-heading__count").text()).toContain("1 agent");
  });
});
