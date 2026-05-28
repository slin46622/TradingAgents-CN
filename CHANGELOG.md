# CHANGELOG

## [2026-05-29] Issue #2 — 加密货币模拟交易规则修复

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
