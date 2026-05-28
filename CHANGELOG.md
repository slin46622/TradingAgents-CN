# Changelog

## 2026-05-29 — feat/backtest-enhanced (#11) on top of feat/agent-memory-feedback (#4)

### 新增
- `tradingagents/backtest/engine.py` — `PerformanceMetrics` dataclass，`calculate_performance()` 方法
- `frontend/src/views/Backtest/index.vue` — 回测系统前端页面

### 修改
- `tradingagents/backtest/engine.py` — `EvaluationConfig` 新增 commission_rate/slippage_rate
- `frontend/src/router/index.ts` — 新增 /backtest 路由
- `frontend/src/components/Layout/SidebarMenu.vue` — 左侧菜单新增「回测系统」

### 回滚命令
```bash
git revert HEAD
```
