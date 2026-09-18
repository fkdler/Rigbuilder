# RigBuilder 前端开发进展 V1

**文档日期**：2026-09-08  
**开发阶段**：V1 初始终端风格实现  
**状态**：✅ 已完成并通过构建验证

---

## 一、项目概述

RigBuilder 前端经历了从"浅色纸质工作台"到"终端风格决策工作台"的重大视觉与交互重构。本次重构的核心目标是将界面定位从"在线 LLM 聊天网页"转变为"专业硬件推荐决策工作台"，参考 Claude Code CLI 的秩序感与命令感，但保持独立的视觉身份。

---

## 二、已完成功能

### 2.1 设计文档与产品定义

**完成文件**：
- `PRODUCT.md` - 产品真相记录（Impeccable init）
- `docs/frontend_design/workbench-design-brief.md` - 完整 UX/UI 设计简报（Impeccable shape）

**核心定位**：
- 用户：面向广泛的个人计算机硬件选择用户（游戏玩家、学生、AI 爱好者等）
- 差异化：多 Agent 共识 + Truth DB 验证 + 完整可追溯性
- 身份：专业决策工作台/查询终端，而非聊天机器人

### 2.2 视觉系统重构

#### 2.2.1 终端主题 Token (`styles/terminal-tokens.css`)

**色彩体系**：
- 底色：`#1a1816`（近黑暖色调）
- 前景色：`#e8e6e3`（偏暖灰白）
- 状态色（低饱和）：
  - 成功/验证通过：`#7fda89`（磷光绿）
  - 进行中/警告：`#e8b563`（琥珀黄）
  - 失败/冲突：`#d87870`（砖红）
- 强调色：青色、品红、黄色

**排版系统**：
- 等宽字体：SF Mono, Consolas, Liberation Mono（用于命令、标签、数值、状态）
- 无衬线字体：Noto Sans SC, Microsoft YaHei（用于长中文说明）
- 字号范围：11px - 22px
- 行高：1.3（紧凑）、1.5（基础）、1.7（舒展）

**结构工具**：
- 极简圆角：2px
- 细分隔线和边框：`#393632`
- 微妙阴影（已移除发光效果）

#### 2.2.2 禁用的视觉元素

严格遵循设计约束，已移除：
- ❌ 蓝紫渐变
- ❌ 发光光晕、玻璃拟态
- ❌ 粒子、矩阵雨、网格背景
- ❌ 机器人图标
- ❌ 大面积圆角卡片和卡片嵌套

### 2.3 核心组件实现

#### 2.3.1 ASCII Logo (`AsciiLogoLarge.vue`)

**特性**：
- 用 Unicode 字符绘制的 "RIGBUILDER" ASCII 艺术字
- **渐变彩虹色**动画效果（8 秒循环）
- 色彩：红 → 黄 → 蓝 → 绿 → 紫 → 粉 → 红
- 响应式字号：0.65rem（桌面）→ 0.55rem（平板）
- 微妙的径向光晕效果

**位置**：侧边栏顶部，作为品牌标识

#### 2.3.2 侧边栏 (`AppSidebar.vue`)

**布局结构**（从上到下）：
1. **Logo 区域**：ASCII Art Logo
2. **导航区域**：功能入口（当前仅有"会话"）
3. **内容区域**：会话列表（可滚动）
4. **页脚区域**：用户信息入口（占位，未实现）

**会话管理功能**：
- ✅ 新建会话按钮
- ✅ 会话列表展示（按最后更新时间倒序）
- ✅ 会话切换（点击切换，自动刷新页面）
- ✅ 会话删除（悬浮显示删除按钮，带确认对话框）
- ✅ 当前会话高亮（绿色左边框）
- ✅ 会话信息显示：
  - 会话 ID（前 8 位）
  - 最后查询内容（截断，最多 2 行）
  - 相对时间（刚刚 / N分钟前 / N小时前 / N天前 / 日期）

**交互细节**：
- 使用 Element Plus `ElScrollbar` 实现自定义滚动条
- 悬浮时边框变为青色，当前会话为绿色
- 删除按钮仅在悬浮时显示
- 最多保留 50 个历史会话（自动清理）

#### 2.3.3 主工作台 (`TerminalWorkbenchV2.vue`)

**布局**：侧边栏 + 主内容区（左右结构）

**顶部 Header**：
- Release Key
- 系统状态（READY / RUNNING / COMPLETED / FAILED）
- 当前会话 ID

**查询区域**：
- 大文本输入框（5 行，可调整大小）
- 提交按钮 + 快捷键提示（Ctrl + Enter）
- 重置按钮（有结果时显示）
- 约束区域占位（待实现）

**Agent 执行状态**：
- 三个 Agent 的列表展示
- 每个 Agent 显示：
  - 索引编号 [1] [2] [3]
  - 模型 ID
  - 状态标签（COMPLETED / FAILED）
  - 成功时：候选数、验证数
  - 失败时：错误代码和错误信息
- 左侧彩色边框指示状态（绿色=成功，红色=失败）

**结果区域**：
- 占位展示（"结果展示组件开发中..."）
- 可展开查看原始 JSON 数据

**空状态**：
- "// READY" 标题
- 系统能力说明
- 功能列表（带标记符号）

**错误状态**：
- 明确的错误代码显示
- 错误描述（支持 409、503、HTTP 200 但 status: failed 等场景）

### 2.4 状态管理增强

#### 2.4.1 会话管理 Store (`stores/session.ts`)

**功能**：
- `sessions` - 会话列表（localStorage 持久化）
- `currentSessionId` - 当前会话 ID
- `createSession(id, query)` - 创建新会话记录
- `updateSession(id, query)` - 更新会话的最后查询
- `switchSession(id)` - 切换到指定会话
- `createNewSession()` - 创建新会话（清除当前会话 ID）
- `deleteSession(id)` - 删除会话

**持久化**：
- 会话列表：`rigbuilder.sessions`（JSON 数组）
- 当前会话：`rigbuilder.recommendation.conversation_id`（字符串）

**数据结构**：
```typescript
interface Session {
  id: string;                // 会话 ID（来自后端 conversation_id）
  createdAt: string;         // 创建时间（ISO 8601）
  lastQuery: string;         // 最后一次查询内容（截断到 100 字符）
  lastUpdated: string;       // 最后更新时间（ISO 8601）
}
```

#### 2.4.2 推荐 Store 集成

**新增集成**：
- 提交查询后自动记录到会话历史
- 区分新会话（创建记录）和已有会话（更新记录）
- 切换会话时重置推荐状态

### 2.5 Element Plus 主题覆盖

**已覆盖组件**：
- Button（等宽字体，主色改为绿色）
- Input / Textarea（终端底色，青色聚焦边框）
- Drawer（终端底色）
- Alert（终端色彩，圆角 2px）
- Progress（绿色进度条）
- Scrollbar（灰色滚动条，悬浮变深）

**全局 CSS 变量覆盖**：
```css
--el-color-primary: var(--color-status-success);
--el-bg-color: var(--color-terminal-bg);
--el-text-color-primary: var(--color-terminal-fg);
--el-border-color: var(--color-border);
--el-border-radius-base: var(--radius-sm);
```

---

## 三、技术验证

### 3.1 构建验证

✅ **类型检查**：`npm run type-check` - 通过  
✅ **生产构建**：`npm run build` - 成功（510ms）  
✅ **产物大小**：
- CSS: 376.53 KB (gzip: 50.74 KB)
- JS: 281.98 KB (gzip: 102.56 KB)

### 3.2 技术栈

**保持不变**：
- Vue 3.5.42
- TypeScript 6.0.3
- Element Plus 2.14.5
- Pinia 4.0.3
- Axios 1.20.0
- Vite 8.2.2

**无新增依赖** ✅

---

## 四、文件清单

### 4.1 新增文件

**组件**：
- `frontend/src/components/AsciiLogo.vue` - 小型 Logo（未使用）
- `frontend/src/components/AsciiLogoLarge.vue` - 渐变彩虹 ASCII Logo
- `frontend/src/components/SessionManager.vue` - 独立会话管理组件（已弃用，功能合并到侧边栏）
- `frontend/src/components/AppSidebar.vue` - 左侧功能侧边栏

**视图**：
- `frontend/src/views/TerminalWorkbench.vue` - 终端工作台 V1（已替换）
- `frontend/src/views/TerminalWorkbenchV2.vue` - 终端工作台 V2（当前使用）

**状态管理**：
- `frontend/src/stores/session.ts` - 会话管理 Store

**样式**：
- `frontend/src/styles/terminal-tokens.css` - 终端主题 Token
- `frontend/src/styles/tokens.css` - 旧纸质主题 Token（已弃用）

**文档**：
- `PRODUCT.md` - 产品定义
- `docs/frontend_design/workbench-design-brief.md` - 设计简报
- `docs/frontend_design/frontend_V1.md` - 本文档

### 4.2 修改文件

- `frontend/src/main.ts` - 引入终端主题 CSS
- `frontend/src/assets/main.css` - 全局样式重置，Element Plus 主题覆盖
- `frontend/src/router/index.ts` - 路由指向 TerminalWorkbenchV2

### 4.3 保留但未使用的文件

**旧推荐工作台**（可删除）：
- `frontend/src/views/RecommendationWorkbench.vue`
- `frontend/src/components/ConstraintEditor.vue`
- `frontend/src/components/RecommendationCard.vue`
- `frontend/src/components/TraceViewer.vue`

---

## 五、待实现功能

### 5.1 高优先级

1. **推荐结果展示组件**（终端风格）
   - 高密度数据表格或卡片
   - 候选名称、排名、评分、可信度
   - 约束满足状态（satisfied / violated / unknown）
   - 验证指标：fact_support、proof_coverage、information_completeness
   - 可展开的 Claims、证据 ID、Truth DB 对比

2. **约束编辑器**（终端风格）
   - 结构化约束清单
   - 添加、编辑、删除约束
   - 约束类型：field / price / compatibility / runtime
   - 硬约束与偏好约束的视觉区分

3. **Trace 查看器**（结构化日志）
   - Agent 执行详情
   - 融合公式与策略版本
   - 输入摘要与 Agent coverage

4. **淘汰候选展示**
   - 淘汰原因清单
   - 与 Top-K 的视觉区分（低对比度）

### 5.2 中优先级

5. **会话上下文管理**（后端支持）
   - 当前前端仅记录会话历史，未实现上下文加载
   - 需要后端支持：`GET /api/conversations/{id}` 返回历史查询和结果
   - 切换会话时恢复历史状态（而非刷新页面）

6. **响应式优化**
   - 窄屏时侧边栏折叠为抽屉
   - 移动端优化（当前 ASCII Logo 在 480px 以下隐藏）

7. **用户信息界面**
   - 侧边栏底部入口
   - 用户设置、偏好、登录状态等

### 5.3 低优先级

8. **键盘导航增强**
   - Tab 在会话列表中导航
   - Esc 关闭侧边栏（如果变为可关闭）

9. **扫描线/噪点质感**
   - 极轻的背景纹理
   - 可选启用/禁用

10. **动效细节**
    - 会话切换过渡
    - 结果展开动画
    - 状态切换反馈

---

## 六、已知问题与限制

### 6.1 会话管理限制

**当前实现**：
- ✅ 会话列表展示和切换
- ✅ 新建会话和删除会话
- ❌ **切换会话时需要刷新页面**（通过 `window.location.reload()`）
- ❌ **未实现上下文恢复**（切换后显示空白状态）

**原因**：
- 前端仅记录会话 ID 和最后查询，未持久化完整推荐结果
- 后端 `GET /api/conversations/{id}` 目前仅返回消息历史，不返回 Agent Fusion 结果

**解决方案**（需后端支持）：
1. 后端持久化 Decision Result / Presentation Result
2. 前端调用 `GET /api/conversations/{id}` 获取完整会话状态
3. 切换会话时恢复：
   - 最后查询内容
   - 推荐结果（Top-K、eliminated、trace）
   - Agent 执行状态

### 6.2 响应式布局

**当前状态**：
- 桌面端（>= 1024px）：正常显示
- 平板端（768px - 1023px）：侧边栏和主内容纵向排列
- 移动端（< 768px）：部分元素隐藏或调整

**待优化**：
- 侧边栏在窄屏时应折叠为抽屉（目前仍占据屏幕高度）
- ASCII Logo 在移动端完全隐藏，应考虑提供文字替代

### 6.3 推荐结果占位

**当前状态**：仅显示 "结果展示组件开发中..." 和原始 JSON 数据

**待实现**：完整的推荐结果卡片/表格组件

---

## 七、设计原则落实

### 7.1 视觉定位 ✅

- ✅ 参考 Claude Code CLI 的秩序感与命令感
- ✅ 不复制其视觉细节、品牌元素、布局
- ✅ 决策工作台身份，非聊天界面
- ✅ 渐变彩虹 ASCII Logo，惊艳且独特

### 7.2 色彩系统 ✅

- ✅ 近黑暖色调底色（#1a1816）
- ✅ 低饱和状态色（磷光绿、琥珀黄、砖红）
- ✅ 禁用蓝紫渐变、发光、玻璃拟态

### 7.3 排版系统 ✅

- ✅ 混合排版（等宽 + 无衬线）
- ✅ 等宽用于技术内容，无衬线用于中文说明
- ✅ 不使用 Inter，不引入在线字体

### 7.4 信息架构 ✅

- ✅ 符号标记的区块标题（`>`、`▸`、`▣`）
- ✅ 细分隔线和对齐
- ✅ 极简圆角（2px）
- ✅ 克制的动效

### 7.5 交互模式 ✅

- ✅ 不使用左右聊天气泡
- ✅ 不使用机器人头像
- ✅ 不使用打字机动画
- ✅ 决策记录/查询日志风格

---

## 八、后续开发建议

### 8.1 短期目标（V1.1）

1. 实现推荐结果展示组件
2. 实现约束编辑器
3. 优化响应式布局（侧边栏折叠）

### 8.2 中期目标（V1.5）

4. 完整的会话上下文管理（需后端支持）
5. Trace 查看器和淘汰候选展示
6. 用户信息界面

### 8.3 长期目标（V2.0）

7. 键盘导航增强
8. 动效细节优化
9. 可选的背景纹理
10. 性能优化（懒加载、虚拟滚动）

---

## 九、总结

RigBuilder 前端 V1 成功完成了从"浅色纸质工作台"到"终端风格决策工作台"的视觉重构。核心亮点包括：

✨ **渐变彩虹 ASCII Logo** - 独特的品牌标识  
✨ **左侧功能侧边栏** - 可扩展的导航结构  
✨ **完整的会话管理** - 新建、切换、删除、历史记录  
✨ **终端风格视觉系统** - 克制、专业、可读  
✨ **混合排版** - 等宽技术内容 + 无衬线中文  

当前实现已通过类型检查和生产构建验证，可以作为稳定的基础继续迭代推荐结果展示、约束编辑和上下文管理等核心功能。

---

**文档维护**：本文档记录 V1 阶段的完成状态，后续版本更新请创建 `frontend_V1.1.md`、`frontend_V2.0.md` 等文件。
