# 请求路由与实时 token 联机验收 · 2026-09-17

基线为 `5b808ca`（dev / origin/dev），开始时工作区干净。交接中的路由、登录、管理页和流式修改已经包含在该提交中。本报告对应其上的未提交修复。

## 环境与调用链

- 本机 FastAPI / PostgreSQL，真实 HTTP 鉴权、Job、SSE；使用独立普通测试账号，测试结束撤销登录令牌，保留会话与轨迹供复查；未修改 Truth 数据。
- 推理设备 `<DEVICE_B_IP>:8081/8082`；`/v1/models` 显示 agent-a 约 8.19B / Q4_K Medium、agent-b 约 4.21B / Q6_K，与部署说明一致。
- 有效配置：路由分析、stream usage、timings_per_token 均开启，路由 profile 为 agent-b。

```text
用户请求 + 同会话需求
  → 明确 FAST / 禁止查库：直接问答
  → AUTO：agent-b 结构化预分析（requires_database / continues_build）
  → 确定性规则保护（不能降级明确配置要求）
  → FAST 问答，或 SQL 检索 → 两路 Agent → 证据与约束校验 → 融合
  → 执行期间 SSE 更新真实输出 token，融合完成后才展示完整答案
```

`routing_completed.detail` 记录 `model_requires_database`、`model_continues_build` 与最终判定，便于区分模型结论和规则保护。没有上一轮需求时，即使模型说继续，最终也不会凭空继承。

## 最终九轮连续追问

以下在同一测试会话中连续执行，均完成；耗时为单次观测，不是性能承诺。

| 请求 | 路由 / 实际结果 | 秒 |
|---|---|---:|
| 你好，你能做什么？ | agent-b 分析后 FAST，无 Agent SQL | 2.10 |
| 推荐一套适合打3A游戏的电脑配置 | VERIFIED：RX 6600 XT + i5-12400 | 6.68 |
| 我想要50系NV显卡的配置 | 保留游戏整机：RTX 5060 + i5-12400 | 40.35 |
| 显卡必须换成 RTX 5090 | RTX 5090 + i5-13400 | 9.19 |
| 换成 RTX 5070，其他要求不变 | RTX 5070 + i5-12400，旧 GPU 不再生效 | 7.53 |
| 显卡换成 RTX 9999 | 查无匹配，明确未核验，不替换、不宣称未发布 | 0.85 |
| 那换回 RTX 5070，其他要求不变 | 恢复游戏整机：RTX 5070 + i5-12400 | 7.45 |
| 改为普通办公配置，不玩游戏，不要独显 | i5-13400T，已核验核显 UHD Graphics 730，无独显 | 5.51 |
| 不要查数据库，解释一下什么是显存 | 规则直接 FAST，跳过路由模型及 Agent SQL | 1.63 |

独立边界会话还确认：指定 RTX 5090 + Core i7-14700K 后，追问“还是打游戏，但显卡换成 RTX 5070”保留 i7-14700K；随后“什么是显卡？”走 FAST，不重新配机。

## Token 展示结论

- 两个端点的原始流均在 `finish_reason` 前返回 `timings.predicted_n`，例如 1 → 20 → …；完成后另发 `usage.completion_tokens=200`。没有估算文本长度或把 chunk 数当 token 数。
- 最终 50 系追问产生 90 次 `llm_usage` SSE 更新，单调用采样最高 1209 tokens。约每调用每 0.5 秒发送一次。
- 真实 Chromium 页面采到 `Cooking…(0s · ↑ 1 tokens)` → `Ruminating…(4s · ↑ 26 tokens)`；结束后撤下状态栏，并展示 RTX 5070 + i5-12400。
- 保持当前**活跃模型调用输出量之和**的口径：调用结束清除，切换调用可能下降或消失，不是整任务累计数，也不是最终答案字数。短调用可能只来得及展示 1。
- 桌面截图：`../.local-logs/token-stream-desktop.png`。页面无新增 JS 错误、无横向溢出；不改变既有视觉样式。

## 本轮修复

1. 统一禁止查库短语，支持“不要查数据库 / 不查数据库 / 别查库 / 无需查询数据库”。
2. “什么是显卡？”等纯概念问题不再因历史配机被规则强制继承。
3. 仍然打游戏但修改显卡时保留既有 CPU 指定；只有实际用途变化才切换场景。
4. Intel `Core i7-14700K` 匹配保留词边界，不再被去空格操作误判未命中，也不误匹配 KF。
5. RTX 50 系查询从 `%rtx%50__%` 收紧为 `%rtx 50__%`，防止 RTX 3050 6GB 挤占有界结果。原错误候选会被最终校验挡住，但会造成空配置；本轮修复检索源头。
6. 失败查库后的用户需求单独保留，不依赖成功推荐锚点；不伪造已核验证据。
7. SSE 白名单保留路由分类详情，便于观测 agent-b 分析是否执行。
8. 增加流式传输、工具参数拼接、final-only usage、异常清理、上下文和系列检索回归；更新旧 Thinking 浏览器断言。

## 验证与剩余问题

- 后端完整 pytest：464 passed，5 skipped；跳过项未声称通过。Windows SSL 阻塞本轮未复现，临时目录权限错误通过工作区目录/授权重试解决。
- 前端 unit：162 passed；Vue/TypeScript 构建通过。
- 真实模型 + API/SSE：上述九轮及独立边界会话完成。
- 真实浏览器 token / 50 系结果测试：1 passed。
- 扩展桌面/手机浏览器套件初次 16 passed、10 failed。两项旧 Thinking 断言已更新并单独复测，2 passed（含并行计数相加、调用结束清除、减少动画模式）；其余 8 项（4 个场景 × 2 个视口）仍失败，涉及能力参考标签、办公草案标题、部分核验提示、缺 GPU 时的部分配置说明。这些属于基线页面与历史测试契约不一致，**不能宣称全站 E2E 已通过**；本轮没有通过删除这些断言掩盖问题。
- 50 系一轮仍需 40 秒，真实模型会增加 SQL 轮次；路由成功不代表延迟已优化，也不代表全部配件兼容性、价格或游戏帧率已验证。
- 任意长会话、全部自然语言否定/约束、所有硬件型号不在本次有限题集的覆盖范围。

## 复现与证据

启动后端后，在 backend 目录执行：

```powershell
..\.venv\Scripts\python.exe -X utf8 scripts/eval_routing_stream_live.py --output ../.local-logs/routing-replay.json
# 可用 --questions 自定义 JSON 字符串数组，按同一会话连续执行。
..\.venv\Scripts\python.exe -X utf8 scripts/probe_routing_live.py
..\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

在 frontend 目录执行：

```powershell
npm.cmd run test:live -- token-stream.live.ts
npm.cmd run test:e2e -- conversation-terminal.spec.ts -g 单行思考提示
```

本地证据（不含登录 token）：

- `.local-logs/routing-stream-probe.json`：两个端点原始计数帧及修复前分类边界。
- `.local-logs/routing-stream-live-before.json`：基线七轮。
- `.local-logs/routing-stream-live-fixed.json`：双指定型号与概念问答复测。
- `.local-logs/routing-stream-live-final.json`：最终九轮 Job、SSE 和 SQL trace。
- `.local-logs/token-stream-browser.json`：真实页面文字采样、最终 Job 结果。

证据文件保存在本机且被 Git 忽略；代码、脚本与报告保持未提交，未推送远端。
