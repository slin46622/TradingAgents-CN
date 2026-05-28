# CHANGELOG

## 2026-05-29 — feat: 新增加密货币专属分析师 Agent (#3)

### 新增
- `tradingagents/agents/analysts/crypto_analyst.py` — 加密货币专属分析师，直接调用 BinanceProvider 获取 OHLCV 及资金费率，不依赖 LangChain 工具

### 修改
- `tradingagents/agents/utils/agent_states.py` — AgentState 新增 `crypto_report` 字段
- `tradingagents/graph/analyst_execution.py` — ANALYST_NODE_SPECS 新增 `"crypto"` 条目
- `tradingagents/graph/conditional_logic.py` — ConditionalLogic 新增 `should_continue_crypto` 方法
- `tradingagents/graph/setup.py` — setup_graph 新增 `"crypto"` 分析师分支
- `tradingagents/graph/trading_graph.py` — _create_tool_nodes 新增 `"crypto"` 空占位 ToolNode，进度映射新增 `"Crypto Analyst"`

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
