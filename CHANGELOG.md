# CHANGELOG

## 2026-05-29 — feat/backtest-portfolio (#12)

### 新增
- `tradingagents/backtest/service.py` — `BacktestService.portfolio_evaluate()` 多标的组合回测方法，支持等权重组合绩效计算
- `tradingagents/backtest/service.py` — `_compute_metrics_from_returns()` 辅助函数：从净收益序列计算绩效指标
- `tradingagents/backtest/service.py` — `_compute_correlation()` 辅助函数：计算两两收益率相关性矩阵
- `app/routers/backtest.py` — `/api/backtest/portfolio` POST 路由

### 修改
- `app/main.py` — 注册 backtest_router
- `frontend/src/views/Backtest/index.vue` — 股票代码输入框支持逗号分隔多标的；多标的时调用组合回测接口，单标的走原有接口；新增各标的绩效对比表格和相关性矩阵展示

### 回滚命令
```bash
git revert HEAD
```

---


## 2026-05-29 — feat/backtest-enhanced (#11)

### 新增
- `tradingagents/backtest/engine.py` — `PerformanceMetrics` dataclass，`calculate_performance()` 方法（夏普比率/最大回撤/胜率/盈亏比）
- `frontend/src/views/Backtest/index.vue` — 回测系统前端页面

### 修改
- `tradingagents/backtest/engine.py` — `EvaluationConfig` 新增 commission_rate/slippage_rate 字段
- `frontend/src/router/index.ts` — 新增 /backtest 路由
- `frontend/src/components/Layout/SidebarMenu.vue` — 左侧菜单新增「回测系统」独立入口

### 回滚命令
```bash
git revert HEAD
```

---

## 2026-05-29 — feat/crypto-data-source (#1)

### 变更内容
- `app/routers/paper.py`: 
  - `PlaceOrderRequest.quantity` 改为 `float`（支持小数仓位如 0.001 BTC）
  - 新增 `CRYPTO_KEYWORDS` 和 `_is_crypto_code()` 自动识别加密货币代码
  - `_detect_market_and_code()` 新增 `CRYPTO` 市场类型（优先于美股字母规则）
  - `_get_available_quantity()` 对 CRYPTO 直接返回全部持仓（24/7 无 T+1）
  - `_get_last_price()` 新增 CRYPTO 分支，调用 `BinanceProvider.get_price()`
  - `INITIAL_CASH_BY_MARKET` 新增 `USDT: 100_000`
  - `currency_map` 新增 `CRYPTO → USDT`
  - 买入持仓 `available_qty` 对非 CN 市场（含 CRYPTO）立即可用
  - 卖出逻辑 `new_qty <= 0` 替代 `== 0`（兼容浮点精度）
  - 账户/持仓汇总全面扩展 USDT 货币字段
- `tradingagents/utils/stock_utils.py`:
  - 新增 `StockMarket.CRYPTO` 枚举值
  - `identify_stock_market()` 优先识别加密货币关键字
  - `get_currency_info()` / `get_data_source()` / `get_market_info()` 覆盖 CRYPTO
  - `get_market_info()` 返回 `is_crypto` 字段

### 涉及文件
- `app/routers/paper.py`
- `tradingagents/utils/stock_utils.py`

### 回滚方法
```bash
git revert HEAD --no-edit
```
