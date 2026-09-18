# RigBuilder Frontend

Vue 3 + TypeScript + Vite 前端。Router 负责页面组织，Pinia 保存界面状态，Axios 统一访问 FastAPI，Element Plus 提供基础组件，ECharts 预留给推荐解释和实验指标页面。

## 本地开发

```powershell
Copy-Item .env.example .env
npm.cmd install
npm.cmd run dev
```

开发服务器默认位于 `http://127.0.0.1:5173`，并将 `/api` 与 `/health` 代理到 `http://127.0.0.1:8000`。

生产部署前必须填写 `.env.example` 中标记为 `TODO(CONFIG)` 的 FastAPI 公网地址或反向代理策略。
