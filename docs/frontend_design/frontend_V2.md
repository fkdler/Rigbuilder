# RigBuilder 前端开发进展 V2

**文档日期**：2026-09-13
**开发阶段**：决策终端缺陷修正 + 只读运行统计控制台（Plan V3.6）
**状态**：✅ 已完成并通过构建、单测、e2e 与真机冒烟验证
**计划依据**：[Plan_V3.6.md](../Plan_documents/Plan_V3.6.md)
**继承**：[frontend_V1.md](frontend_V1.md)（终端风格 V1）、[workbench-design-brief.md](workbench-design-brief.md)（设计简报）

---

## 一、本轮目标与结论

V3.5 让后端**读取侧**闭环（5 个 `/api/admin/*` 只读端点），但 V3.5 §6 把界面拆成 P1/P2，
其中 P2「只读管理页面」从未实施，且 §311 明确把其形态（原生 HTML vs 做进 Vue）**推迟到
看到 P1 真实数据后再定**。

本轮完成该评审与实施。**结论：做进现有 Vue 前端，使用 echarts**（`echarts@^6.1.0` 已在
`package.json` 且此前 0 引用，属存量依赖启用，不新增任何依赖）。

同时修正 4 个经代码定位与实测确认的界面缺陷，并处置 element-plus 的首屏负担。

---

## 二、修正的界面缺陷

### 2.1 融合返回 3 个候选，界面只渲染 1 个

`RecommendationBlock.vue` 原为：

```ts
const top = computed(() => props.response.result?.top_k?.[0] ?? null);
```

而提交时固定 `top_k: 3`，实测每次 fusion 均返回 3 个已通过真值校验的候选。
即**每次融合都静默丢弃两个已验证候选**，而 `eliminated` 反而有折叠入口。

现按排名渲染全部候选，每项含 rank、`canonical_name`、`recommendation_score` 与
`credibility`（设计简报 §4 要求二者并列，因其语义不同）、来源模型标签；
第 1 名指标默认展开以保持单候选场景与改动前一致，其余默认折叠。

### 2.2 全站没有导航

**实测：`frontend/src` 全目录检索 `router-link` / `RouterLink` / `useRouter` /
`router.push` —— 0 处匹配。** `/legacy` 与 `/comparison` 只能手打 URL 到达。

现 `AppSidebar` 增加导航区，接入 `/`、`/admin`、`/comparison`、`/legacy`，当前项由
`RouterLink` 自带 `aria-current="page"`；窄屏下导航变横向条，不会把会话行挤出屏幕。

### 2.3 模式选择器隐藏了 86 倍延迟差距

`TerminalHeader.vue` 原为裸 `<select>`，配 localStorage 持久化，界面上零代价提示。
实测两条路径的实际代价：

| 模式 | 实测 |
|---|---|
| `FAST`（chat） | **743 ms / 1 401 ms** |
| `VERIFIED`（fusion） | **114 055 ms**（与 p50 86.1 s 一致） |

这正是「说一句『你好』等了 115 秒」的直接成因。现在选项常驻标注实测代价
（`AUTO · routed per message` / `FAST · ~1 s` / `VERIFIED · ~90 s+`），并在任务完成后
显示 `resolved_mode` —— 请求模式是请求、不是结果，`resolve_query_mode` 只对决策类问题
采纳显式模式，这一点此前在界面上不可见。

### 2.4 端点健康不可见

`/api/admin/endpoints` 早已返回每个 profile 的 `reachable` 与 `latency_ms`，但界面上没有
任何呈现。某个 `llama-server` 挂掉时，用户只能在约 115 秒后看到 `fusion_failed` ——
**库中已积 27 次**。

现头部显示三端点状态（ok / degraded / down / unknown）与最大延迟，任一不可达时额外渲染
`role="alert"` 的可见警告行。探针失败只使指示器保持 unknown，不渲染错误横幅：终端本身
仍可能完全可用。

---

## 三、新增：只读运行统计控制台

### 3.1 页面

`/admin`（`views/AdminDashboard.vue`，懒加载），六个区块：

| 区块 | 数据来源 |
|---|---|
| 概览条 | `summary.jobs` |
| Agent 分布（含每日趋势） | `summary.agents` |
| 任务与错误（状态 / 错误码 / 模式 / 事件类型 / 最近任务） | `summary.jobs` |
| 数据库概况（release / 视图 / 空视图 / 空表 / warnings） | `summary.database` |
| 推理端点（可达性 / 延迟 / 运行期开关） | `/api/admin/endpoints` |
| 最近任务 | `summary.jobs.recent` |

一次 `/api/admin/summary` 打底，端点探测独立请求 —— 后端自身把探测上限设为 12 秒，
独立请求可避免它拖慢其余区块。

### 3.2 硬性渲染纪律

这是本轮最重要的设计约束，继承 V3.5 §5.3 的核心纪律：

- **缺失值渲染为 `—`，绝不渲染为 `0`。** 后端把缺失的 `agent_run.metrics` 键报成
  `null`（33 个键中有 6 个只出现在部分历史运行里），渲染成 0 等于陈述一个从未做过的测量。
- **`metric_coverage` 与数值同屏出现。** 实测真机数据：`agent_duration_ms 66/100 runs partial`、
  `llm_calls 99/100 runs partial` —— 覆盖率低于运行数时标注 `partial`。
- **`agent_coverage` 的 `null`（非 fusion）与 `0.0`（fusion 但无候选）视觉可分**，
  分别渲染为 `—` 与 `0/3`。
- **全部数值直接来自端点，前端不做任何重算**，包括不自行求平均、不自行推比值。
- **吞吐率在时长覆盖率不足时扣留**：`tokens_per_second_coverage < 0.95` 时不显示数字而显示
  `withheld`，因为用部分运行算出的比率看起来精确却是错的。
- **页面只读**：不发任何写请求，不提供重跑入口。

### 3.3 组件

```text
api/admin.ts                       5 个端点的类型化客户端（返回 AdminResult，不抛异常）
components/admin/format.ts         缺失与零的渲染纪律（纯函数）
components/admin/chartTheme.ts     echarts 终端主题与选项构造（纯函数，不 import echarts）
components/admin/ChartPanel.vue    echarts 容器（按需引入 + 实例销毁）
components/admin/MetricCard.vue    单指标卡
components/admin/AgentStatsTable.vue   每 Agent 的 p50/p95/max 矩阵 + 覆盖率
components/admin/JobStatsPanel.vue     状态 / 错误码 / 模式 / 事件类型 / 最近任务
components/admin/DatabasePanel.vue     release / 视图 / 空视图 / 空表
components/admin/EndpointPanel.vue     三端点可达性与运行期开关
```

`chartTheme.ts` 刻意做成**无 echarts 依赖的纯函数**：可脱离渲染器单测，且保证主题不可能
泄漏到其他页面。

---

## 四、echarts 接入规范（设计一致性）

设计简报 §3 的禁止项依然有效，而 echarts 默认主题恰好违反它：

| 设计简报禁止 | echarts 默认 | 本轮对策 |
|---|---|---|
| 蓝紫渐变 | 默认调色板含蓝紫与面积渐变 | 自定义主题，色板只取 `terminal-tokens.css` 的值 |
| 发光光晕 | 部分图形带阴影 | 主题内无 shadow |
| 炫技数据可视化 | 提供 3D / 仪表盘 / 雷达 | **只产出 bar 与 line**（单测断言） |
| 动效 | 默认入场动画 | `animation: false`，与 `prefers-reduced-motion` 全局规则一致 |
| 大圆角 | 默认圆角 | 取 `--radius-sm` |

**色板副本的防漂移**：echarts 无法解析 CSS 自定义属性，故 `CHART_COLORS` 是
`terminal-tokens.css` 的副本；`chartTheme.spec.ts` 读取该样式表并断言每个颜色值仍在其中，
使两份拷贝不可能静默分叉。

**体积**：按需引入 `echarts/core` + `BarChart / LineChart / GridComponent /
TooltipComponent / LegendComponent / CanvasRenderer`，**禁止 `import * as echarts from "echarts"`**
（全量 dist 实测 3 847 293 B）。`/admin` 经路由懒加载。

---

## 五、element-plus 改为随对比页懒加载

**Plan_V3.6 §12-2 决策项，独立提交 `a31c05a`，可单独回退。**

实测事实：

| 事实 | 证据 |
|---|---|
| `main.ts` 无条件导入 EP 全量 CSS | 该文件 **361 258 B** |
| 占首屏 CSS 的 **94.0%** | 361 258 / 384 270 |
| EP 的 JS 也在首屏 | 首屏 chunk 内含 `el-form-item` 与「三模型对比测试」文案 |
| 原因 | `/comparison` 此前是静态导入 |
| EP 在界面上唯一可达的使用者 | 只有 `/comparison` |

改动三个文件（`main.ts` 移除全局导入、`ModelComparisonView.vue` 接收该导入、
`/comparison` 改动态导入）后的实测结果：

| 产物 | 改动前 | 改动后 |
|---|---|---|
| 首屏 CSS | 384.27 kB（gz 52.61） | **29.09 kB（gz 5.81）** |
| 首屏 JS | 273.59 kB（gz 100.09） | **183.86 kB（gz 68.55）** |
| `/comparison` CSS | — | 359.98 kB（gz 47.92，按需） |

`assets/main.css` 中的 `--el-*` 覆盖规则原样保留：EP 未加载时不生效，无需清理。

---

## 六、构建体积

| 产物 | raw | gzip | 说明 |
|---|---|---|---|
| `index-*.css` | 29.09 kB | 5.81 kB | 首屏 |
| `index-*.js` | 183.86 kB | 68.55 kB | 首屏 |
| `AdminDashboard-*.css` | 12.42 kB | 2.29 kB | 懒加载 |
| `AdminDashboard-*.js` | 558.16 kB | 188.39 kB | 懒加载（含 echarts） |
| `ModelComparisonView-*.css` | 359.98 kB | 47.92 kB | 懒加载（含 Element Plus） |
| `ModelComparisonView-*.js` | 95.59 kB | 34.08 kB | 懒加载 |
| `TerminalWorkbenchV2-*.js` | 16.33 kB | 6.13 kB | 懒加载 |

**关于 `/admin` chunk 的 558 kB**：Plan_V3.6 §6 初稿写的 250 kB 预算**不成立**，那是未实测
时估的数字。实测 echarts 核心本身即约 513 kB（去掉 LineChart 与 LegendComponent 也只降到
513.34 kB）。已修正计划书，改为两条真实成立且可验证的线：`/admin` 必须为懒加载 chunk
且不出现在首屏（由 e2e bundle 守卫用例断言）、首屏 `index-*.js` 不得超过基线 273.59 kB。

---

## 七、测试与验证

| 项目 | 基线（V3.6 前） | 本轮后 |
|---|---|---|
| 后端 pytest | 266 passed / 2 skipped | **266 passed / 2 skipped**（`backend/` 零改动） |
| 前端 Vitest | 3 文件 / 4 项 | **9 文件 / 71 项** |
| Playwright（封闭式） | 10 项 | **24 项** |
| Playwright（真机 `npm run test:live`） | — | **1 项，零 console 错误** |
| `vue-tsc --noEmit` | 通过 | 通过 |

既有测试断言未做任何放宽；新增用例为追加。

### 7.1 真机冒烟检查（新增）

`e2e/livesmoke.live.ts` + 独立配置 `playwright.live.config.ts` 与脚本 `npm run test:live`：
**不打任何 mock**，直接打本机真实后端。默认 `playwright test` 仍为封闭式运行，不要求后端在线。

**这个检查抓到了一个单测拦不住的错**：初版把「每日趋势」画在任务面板上，读
`stats.by_day`；真机核对后确认 `by_day` 只在 `AgentStatsResponse` 上（它截断的是
`agent_run.created_at`），`JobStatsResponse` 没有该字段 —— 对真机永远为空，而 mock 出来的
单测全部通过。真机返回 5 天真实数据。

真机另验证了部分覆盖率标注：`agent_duration_ms 66/100 runs partial`。

### 7.2 本轮修掉的自造缺陷

诚实记录，均先由测试或冒烟发现：

1. `TerminalHeader` 初版误写 `result.profiles`（应为 `result.data.profiles`）。
2. `JobStatsPanel` 模板中 `stats.by_day.length` 在缺该字段时**使整页渲染函数崩溃**；
   已把纯函数层（`countsToPoints` / `dayTrendOption` / `sortByValueDesc`）改为容忍缺省，
   使任何单个字段缺失都不能白屏（§7-5）。
3. `JobStatsPanel` 模板中一处**嵌套模板字符串**写在属性里，模板编译器解析不了。
4. 概览条与任务面板重复显示同一批数字，已去重使概览条成为唯一来源。
5. 移动端原隐藏 coverage 列；那会使部分覆盖的指标与完整指标无法区分，改为隐藏 `max` 列。
6. e2e 的 `**/api/**` 路由把 Vite 的 `/src/api/*.ts` 模块请求也拦截了，应用根本没启动；
   已加 `startsWith("/api/")` 守卫（既有 spec 早有此守卫，新 spec 漏了）。
7. `AgentStatsTable` 初版在覆盖率不足时既显示数值又标 `withheld`，自相矛盾；改为扣留数值。

---

## 八、明确未做

- **约束编辑器（Plan_V3.6 §12-1）—— 仍待确认。** 这是唯一的业务变更：接入后提交给 Agent
  的 payload 会从恒定 `constraints: []` 变为用户可编辑的约束列表，会切换 `fusion-v1` 的
  算分公式（有偏好时 `consensus` 权重从 1.0 降到 0.30）并可能通过 hard 约束淘汰候选。
  按既定规矩「要更改业务，请向我询问」，未获确认前不动。
- **死代码删除（§12-4）** —— 约 47 KB 待确认。其中 `ConstraintEditor.vue` 建议保留：
  它是约束能力的唯一现存实现参考，且已确认**必须重写而非复用**（见 §九）。
- 不改 `/comparison` 与 `/legacy` 的内部实现，本轮只保证从导航可到达。

---

## 九、既有文档中被证伪的两处记述

不改动既有文档，仅在此记录：

1. **`frontend_V1.md` §4.3 已过时**：它把 `RecommendationCard.vue` 与 `TraceViewer.vue`
   和死文件并列标为「可删除」，但两者已被 `/legacy` 的 `TerminalWorkbenchV2.vue` 引用，
   **属活代码**。
2. **`ConstraintEditor.vue` 当前是渲染坏掉的**：它引用的 `--color-ink` / `--color-paper` /
   `--font-serif` 等 Token 来自 `styles/tokens.css`，而该文件**从未被导入**
   （`main.ts` 只导入 `terminal-tokens.css`）。实测 grep 确认 `tokens.css` 零导入，
   因此这些 CSS 变量在运行时全部未定义。加上它**只有「移除」没有「新增」**，
   结论是必须重写为终端风格。

另：`frontend_V1.md` §5.1 把「约束编辑器（终端风格）」列为高优先级待实现，
`workbench-design-brief.md` §2 把它写进主要任务第 1 条 —— 与本轮的待确认项一致。

---

**文档维护**：本文档记录 V2 阶段的完成状态，后续版本更新请创建 `frontend_V3.md` 等文件。
