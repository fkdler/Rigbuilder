import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import * as dailyApi from "@/api/hardwareDaily";
import HardwareDaily from "./HardwareDaily.vue";

vi.mock("@/api/hardwareDaily", () => ({
  getHardwareDailies: vi.fn(),
  hardwareDailyError: vi.fn(() => "读取失败"),
}));

const ARTICLE = {
  title: "下代显卡普遍延期｜显卡日报9月18日",
  content_id: "content-18",
  content_text: "一、今日价格分析\n价格回调\n二、新闻速览\n新品消息",
  url: "https://zhuanlan.zhihu.com/p/2084021781136986760",
  comment_count: 7,
  vote_up_count: 30,
  author_name: "Wallace",
  author_profile_url: "https://www.zhihu.com/people/mimi-86-49",
  author_badge_text: null,
  edit_time: "2026-09-18T12:00:00Z",
  authority_level: "4",
  thumbnail_url: null,
  content_is_excerpt: true,
};

const RESPONSE = {
  articles: [
    ARTICLE,
    { ...ARTICLE, title: "显存价格变化｜显卡日报9月17日", content_id: "content-17", edit_time: "2026-09-17T12:00:00Z" },
  ],
  cache: { status: "fresh" as const, fetched_at: "2026-09-18T12:10:00Z", expires_at: "2026-09-18T18:10:00Z" },
};

describe("HardwareDaily", () => {
  it("renders a dated issue index and opens a selected article", async () => {
    vi.mocked(dailyApi.getHardwareDailies).mockResolvedValue(RESPONSE);
    const wrapper = mount(HardwareDaily);
    await flushPromises();

    expect(wrapper.get(".daily-logo").attributes("aria-label")).toBe("HARDWARE DAILY");
    expect(wrapper.findAll(".issue-entry")).toHaveLength(2);
    expect(wrapper.text()).toContain("按日期查看硬件新闻");
    expect(wrapper.get(".issue-date").text()).toBe("9/18");
    expect(wrapper.find(".daily-byline a").exists()).toBe(false);
    expect(wrapper.text()).toContain("下代显卡普遍延期｜显卡日报9月18日");
    expect(wrapper.text()).not.toContain("新知答主");

    await wrapper.findAll(".issue-entry")[0].trigger("click");
    expect(wrapper.find(".daily-article").exists()).toBe(true);
    expect(wrapper.text()).toContain("一、今日价格分析");
    expect(wrapper.text()).toContain("二、新闻速览");
    expect(wrapper.text()).toContain("新闻速览为搜索摘要");
    expect(wrapper.get("a.daily-source").attributes("href")).toBe(ARTICLE.url);

    await wrapper.get(".article-nav button").trigger("click");
    expect(wrapper.findAll(".issue-entry")).toHaveLength(2);
  });

  it("shows a readable error without cached articles", async () => {
    vi.mocked(dailyApi.getHardwareDailies).mockRejectedValue(new Error("offline"));
    const wrapper = mount(HardwareDaily);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("读取失败");
  });
});
