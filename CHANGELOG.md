# CHANGELOG

## [2026-05-29] Issue #6 — Binance 实盘交易接口

### 变更内容
- 新增 `tradingagents/trading/live/binance_trader.py`：HMAC-SHA256 签名、test_connection、get_account、place_order、cancel_order、get_open_orders、get_order_history
- 新增 `app/routers/live_trading.py`：GET/POST /api/live/config、POST /api/live/test、GET /api/live/account、GET /api/live/price/{symbol}、POST /api/live/order、DELETE /api/live/order/{id}、GET /api/live/orders、GET /api/live/history
- `app/main.py`：注册 live_trading 路由
- 新增前端 `frontend/src/views/LiveTrading/index.vue`：API Key配置、账户余额、快速下单（市价/限价）、挂单管理、历史订单
- `frontend/src/router/index.ts`：新增 /live 路由
- `frontend/src/components/Layout/SidebarMenu.vue`：新增"实盘交易"菜单项

### 涉及文件
- `tradingagents/trading/live/__init__.py`（新）
- `tradingagents/trading/live/binance_trader.py`（新）
- `app/routers/live_trading.py`（新）
- `app/main.py`
- `frontend/src/views/LiveTrading/index.vue`（新）
- `frontend/src/router/index.ts`
- `frontend/src/components/Layout/SidebarMenu.vue`

### 回滚
```bash
git revert <merge commit hash>
```

## [2026-05-29] Issue #14 — 前端移动端响应式布局适配

### 变更内容
- `frontend/src/layouts/BasicLayout.vue`:
  - 补全缺失 import：`watch`（vue）、`useRoute`（vue-router）、`useWindowSize`（@vueuse/core）
  - 已有移动端基础：isMobile 判断、sidebar overlay、auto-collapse watch
- `frontend/src/styles/index.scss`:
  - 新增全局 `@media (max-width: 767px)` 移动端样式块：
    - 所有 `el-col-*` 强制100%宽度（竖向堆叠）
    - `el-table` 横向滚动
    - 页面标题缩小、卡片内边距收窄
    - el-form 单列布局
    - el-descriptions 横向滚动
    - 面包屑隐藏（手机空间不足）

### 涉及文件
- `frontend/src/layouts/BasicLayout.vue`
- `frontend/src/styles/index.scss`

### 回滚方法
```bash
git revert HEAD --no-edit
```
