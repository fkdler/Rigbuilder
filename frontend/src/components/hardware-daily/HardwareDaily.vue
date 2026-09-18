<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";

import {
  getHardwareDailies,
  hardwareDailyError,
  type HardwareDailyArticle,
  type HardwareDailyResponse,
} from "@/api/hardwareDaily";
import AsciiHardwareDaily from "./AsciiHardwareDaily.vue";

interface DailySection { title: string | null; paragraphs: string[] }

const daily = ref<HardwareDailyResponse | null>(null);
const selectedId = ref<string | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const page = ref<HTMLElement | null>(null);
const SECTION_HEADING = /^([一二三四五六七八九十]+、\s*.+)$/;

const selectedArticle = computed<HardwareDailyArticle | null>(() =>
  daily.value?.articles.find((article) => article.content_id === selectedId.value) ?? null,
);

const sections = computed<DailySection[]>(() => {
  const result: DailySection[] = [];
  let current: DailySection = { title: null, paragraphs: [] };
  for (const rawLine of (selectedArticle.value?.content_text ?? "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const heading = line.match(SECTION_HEADING);
    if (heading) {
      if (current.title || current.paragraphs.length) result.push(current);
      current = { title: heading[1], paragraphs: [] };
    } else current.paragraphs.push(line);
  }
  if (current.title || current.paragraphs.length) result.push(current);
  return result;
});

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai", hour12: false, year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function formatIssueDate(value: string): string {
  const date = new Date(value);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai", month: "numeric", day: "numeric",
  }).formatToParts(date);
  const month = parts.find((part) => part.type === "month")?.value ?? "";
  const day = parts.find((part) => part.type === "day")?.value ?? "";
  return `${month}/${day}`;
}

async function selectArticle(article: HardwareDailyArticle): Promise<void> {
  selectedId.value = article.content_id;
  await nextTick();
  if (typeof page.value?.scrollTo === "function") page.value.scrollTo({ top: 0, behavior: "smooth" });
}

async function backToIssues(): Promise<void> {
  selectedId.value = null;
  await nextTick();
  if (typeof page.value?.scrollTo === "function") page.value.scrollTo({ top: 0, behavior: "smooth" });
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try { daily.value = await getHardwareDailies(); }
  catch (cause) { error.value = hardwareDailyError(cause); }
  finally { loading.value = false; }
}

onMounted(() => void load());
</script>

<template>
  <main ref="page" class="daily-page" aria-label="硬件日报">
    <div v-if="loading" class="daily-loading" aria-busy="true" aria-live="polite">
      <span>正在读取硬件日报</span><i /><i /><i />
    </div>

    <section v-else-if="error" class="daily-error" role="alert">
      <h1>硬件日报暂不可用</h1><p>{{ error }}</p><p>请稍后切换回本模块重试。</p>
    </section>

    <div v-else-if="daily" class="daily-content">
      <header class="daily-masthead">
        <AsciiHardwareDaily />
        <div class="daily-byline">
          <span>{{ daily.articles.length }} 期日报</span>
          <span class="daily-cache" :class="{ 'daily-cache--stale': daily.cache.status === 'stale' }">
            {{ daily.cache.status === "stale" ? "暂为缓存内容" : "已同步" }} · {{ formatDateTime(daily.cache.fetched_at) }}
          </span>
        </div>
      </header>

      <section v-if="!selectedArticle" class="daily-index" aria-labelledby="daily-index-title">
        <div class="index-intro">
          <h1 id="daily-index-title">按日期查看硬件新闻</h1>
          <p>选择一期日报进入阅读页。内容与数据来自知乎搜索接口。</p>
        </div>

        <ol class="issue-list">
          <li v-for="(article, index) in daily.articles" :key="article.content_id">
            <button type="button" class="issue-entry" @click="selectArticle(article)">
              <span class="issue-date" aria-hidden="true">
                {{ formatIssueDate(article.edit_time) }}
              </span>
              <span class="issue-copy">
                <small>NO. {{ String(daily.articles.length - index).padStart(2, "0") }}</small>
                <strong>{{ article.title }}</strong>
                <span>{{ formatDateTime(article.edit_time) }} · {{ article.vote_up_count.toLocaleString("zh-CN") }} 赞同 · {{ article.comment_count.toLocaleString("zh-CN") }} 评论</span>
              </span>
              <img v-if="article.thumbnail_url" class="issue-thumbnail" :src="article.thumbnail_url" alt="" loading="lazy" referrerpolicy="no-referrer" />
              <span class="issue-arrow" aria-hidden="true">→</span>
            </button>
          </li>
        </ol>
      </section>

      <article v-else class="daily-article">
        <nav class="article-nav" aria-label="日报导航">
          <button type="button" @click="backToIssues">← 返回日报目录</button>
          <span>{{ formatIssueDate(selectedArticle.edit_time) }}</span>
        </nav>

        <header class="article-header">
          <p class="eyebrow">GPU DAILY / WALLACE</p>
          <h1>{{ selectedArticle.title }}</h1>
          <dl class="daily-meta" aria-label="日报数据">
            <div><dt>更新于</dt><dd>{{ formatDateTime(selectedArticle.edit_time) }}</dd></div>
            <div><dt>赞同</dt><dd>{{ selectedArticle.vote_up_count.toLocaleString("zh-CN") }}</dd></div>
            <div><dt>评论</dt><dd>{{ selectedArticle.comment_count.toLocaleString("zh-CN") }}</dd></div>
            <div v-if="selectedArticle.authority_level"><dt>内容等级</dt><dd>L{{ selectedArticle.authority_level }}</dd></div>
          </dl>
        </header>

        <div class="daily-body">
          <section v-for="(section, index) in sections" :key="`${section.title}-${index}`" class="daily-section">
            <h2 v-if="section.title">{{ section.title }}</h2>
            <p v-for="(paragraph, paragraphIndex) in section.paragraphs" :key="paragraphIndex">{{ paragraph }}</p>
          </section>
        </div>

        <aside v-if="selectedArticle.content_is_excerpt" class="excerpt-notice">
          <h2>新闻速览为搜索摘要</h2>
          <p>知乎搜索接口在正文中段截断了内容，页面没有漏渲染。后续新闻与完整图表请继续阅读知乎原文。</p>
          <a :href="selectedArticle.url" target="_blank" rel="noopener noreferrer">继续阅读完整新闻速览 ↗</a>
        </aside>

        <footer class="daily-footer">
          <p>内容来自知乎搜索接口；完整图表与原文上下文请前往知乎查看。</p>
          <a class="daily-source" :href="selectedArticle.url" target="_blank" rel="noopener noreferrer">在知乎阅读全文 ↗</a>
        </footer>
      </article>
    </div>
  </main>
</template>

<style scoped>
.daily-page { flex: 1; min-width: 0; min-height: 0; height: 100%; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: var(--color-border-emphasis) transparent; color: var(--color-terminal-fg); font-family: var(--font-sans); }
.daily-content { width: min(100%, 1040px); margin: 0 auto; padding: clamp(24px, 4vw, 54px); }
.daily-masthead { padding-bottom: clamp(24px, 4vw, 42px); border-bottom: 1px solid var(--color-border); }
.daily-byline { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; margin-top: 20px; color: var(--color-terminal-fg-dimmer); font: 11px var(--font-mono); }
.daily-cache { margin-left: auto; color: var(--color-status-success); }
.daily-cache--stale { color: var(--color-status-warning); }
.daily-index, .daily-article { padding-top: clamp(28px, 5vw, 58px); }
.index-intro { max-width: 680px; margin-bottom: 30px; }
.eyebrow { margin: 0 0 10px; color: var(--color-status-success); font: 11px var(--font-mono); letter-spacing: .14em; }
.index-intro h1, .article-header h1 { margin: 0; color: var(--color-terminal-fg); font-size: clamp(1.65rem, 3.6vw, 2.65rem); font-weight: 600; line-height: 1.22; letter-spacing: -.035em; }
.index-intro > p:last-child { margin: 14px 0 0; color: var(--color-terminal-fg-dim); font-size: 14px; line-height: 1.75; }
.issue-list { margin: 0; padding: 0; border-top: 1px solid var(--color-border); list-style: none; }
.issue-list li { border-bottom: 1px solid var(--color-border); }
.issue-entry { display: grid; width: 100%; grid-template-columns: 76px minmax(0, 1fr) auto 34px; align-items: center; gap: 22px; min-height: 126px; padding: 18px 12px 18px 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.issue-entry:hover { background: var(--color-terminal-bg-elevated); }
.issue-entry:focus-visible, .article-nav button:focus-visible, .daily-source:focus-visible, .excerpt-notice a:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 3px; }
.issue-date { padding-right: 18px; border-right: 1px solid var(--color-border); color: var(--color-terminal-fg); font: 500 1.05rem var(--font-mono); font-variant-numeric: tabular-nums; text-align: center; }
.issue-copy small { color: var(--color-terminal-fg-dimmer); font: 10px var(--font-mono); letter-spacing: .08em; }
.issue-copy { display: grid; min-width: 0; gap: 8px; }
.issue-copy strong { color: var(--color-terminal-fg); font-size: clamp(1rem, 2vw, 1.28rem); font-weight: 550; line-height: 1.45; }
.issue-copy > span { color: var(--color-terminal-fg-dimmer); font: 11px/1.5 var(--font-mono); }
.issue-thumbnail { width: 120px; height: 76px; border: 1px solid var(--color-border); border-radius: 6px; object-fit: cover; }
.issue-arrow { color: var(--color-status-success); font: 20px var(--font-mono); transition: transform .16s ease; }
.issue-entry:hover .issue-arrow { transform: translateX(4px); }
.article-nav { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding-bottom: 20px; border-bottom: 1px solid var(--color-border); color: var(--color-terminal-fg-dimmer); font: 11px var(--font-mono); }
.article-nav button { min-height: 44px; padding: 0; border: 0; background: transparent; color: var(--color-status-success); font: inherit; cursor: pointer; }
.article-header { padding: clamp(28px, 5vw, 52px) 0 0; }
.article-header h1 { max-width: 820px; }
.daily-meta { display: flex; flex-wrap: wrap; gap: 10px 0; margin: 26px 0 0; padding: 14px 0; border-top: 1px solid var(--color-border); border-bottom: 1px solid var(--color-border); }
.daily-meta div { display: flex; gap: 8px; padding: 0 16px; border-left: 1px solid var(--color-border); font: 11px var(--font-mono); font-variant-numeric: tabular-nums; }
.daily-meta div:first-child { padding-left: 0; border-left: 0; }
.daily-meta dt { color: var(--color-terminal-fg-dimmer); }
.daily-meta dd { margin: 0; color: var(--color-terminal-fg-dim); }
.daily-body { padding-top: 14px; }
.daily-section { padding: clamp(22px, 3.5vw, 34px) 0; border-bottom: 1px solid var(--color-border); }
.daily-section h2 { max-width: 72ch; margin: 0 0 16px; color: var(--color-terminal-fg); font-size: 1.05rem; font-weight: 600; line-height: 1.5; }
.daily-section p { max-width: 72ch; margin: 0; color: var(--color-terminal-fg-dim); font-size: 15px; line-height: 1.95; overflow-wrap: anywhere; }
.daily-section p + p { margin-top: 14px; }
.excerpt-notice { margin-top: 26px; padding: 22px 0; border-top: 1px solid var(--color-status-warning); border-bottom: 1px solid var(--color-border); }
.excerpt-notice h2 { margin: 0; color: var(--color-terminal-fg); font-size: 1rem; font-weight: 600; }
.excerpt-notice p { max-width: 72ch; margin: 10px 0 0; color: var(--color-terminal-fg-dim); font-size: 14px; line-height: 1.75; }
.excerpt-notice a { display: inline-block; margin-top: 14px; color: var(--color-status-success); font: 12px var(--font-mono); text-underline-offset: 4px; }
.daily-footer { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 26px 0 16px; color: var(--color-terminal-fg-dimmer); font-size: 12px; line-height: 1.65; }
.daily-footer p { margin: 0; }
.daily-source { flex: 0 0 auto; color: var(--color-status-success); font: 12px var(--font-mono); text-underline-offset: 4px; }
.daily-loading, .daily-error { width: min(calc(100% - 40px), 680px); margin: 12vh auto 0; color: var(--color-terminal-fg-dim); }
.daily-loading { display: flex; align-items: center; gap: 9px; font: 12px var(--font-mono); }
.daily-loading i { width: 5px; height: 5px; border-radius: 50%; background: var(--color-status-success); animation: daily-pulse 1.2s ease-in-out infinite; }
.daily-loading i:nth-of-type(2) { animation-delay: .15s; }
.daily-loading i:nth-of-type(3) { animation-delay: .3s; }
.daily-error { padding: 24px 0; border-top: 1px solid var(--color-status-error); border-bottom: 1px solid var(--color-status-error); }
.daily-error h1 { margin: 0; color: var(--color-status-error); font-size: 18px; }
.daily-error p { margin: 10px 0 0; line-height: 1.7; }
@keyframes daily-pulse { 50% { opacity: .2; transform: translateY(-2px); } }
@media (max-width: 760px) {
  .daily-content { padding: 22px 18px 40px; }
  .daily-byline { align-items: flex-start; flex-direction: column; }
  .daily-cache { margin-left: 0; }
  .issue-entry { grid-template-columns: 54px minmax(0, 1fr) 24px; gap: 13px; min-height: 112px; padding-right: 2px; }
  .issue-date { padding-right: 12px; font-size: .88rem; }
  .issue-thumbnail { display: none; }
  .issue-copy > span { font-size: 10px; }
  .daily-meta div { padding: 0 10px; }
  .daily-meta div:first-child { width: 100%; padding-bottom: 4px; }
  .daily-section p { font-size: 15px; line-height: 1.88; }
  .daily-footer { align-items: flex-start; flex-direction: column; }
}
@media (prefers-reduced-motion: reduce) {
  .daily-loading i { animation: none; }
  .issue-arrow { transition: none; }
}
</style>
