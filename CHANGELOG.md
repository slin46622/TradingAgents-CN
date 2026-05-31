# CHANGELOG

## [2026-05-31] qlib 全量增强 — 对标 GitHub Benchmark（v1.4.0）

### 背景
用户要求不筛选、把 qlib GitHub 主要功能全部接入，目标是实盘盈利。

### 新增模型（共 16 种算法）
原有 6 种（LGB×3 + XGB + DoubleEnsemble + LinearRidge）新增 10 种：
- **深度学习**：GRU · LSTM · ALSTM · TCN · TransformerModel
- **其他 DL**：TabNet · ADD（自适应动态Dropout）· LocalFormer · SFM（状态频率记忆）· DNN

### 新增数据处理
- `_make_alpha360()` — 360 维原始 OHLCV 因子集（专为深度学习设计）
- `fit_ensemble_alpha360()` — 基于 Alpha360 的 DL 专属训练流程

### 新增评估与策略
- `evaluate_ic()` — 每模型 IC / ICIR / RankIC / RankICIR 计算
- `backtest_enhanced()` — TopkDropout + A股手续费建模（买0.05%/卖0.15%）
- `backtest_enhanced_indexing()` — EnhancedIndexingStrategy（主动+被动混合）
- `retrain_incremental()` — 滚动重训练（在线学习，最近 N 天窗口）

### 新增 API 端点（7个）
- `POST /api/qlib/evaluate/ic`
- `POST /api/qlib/backtest/enhanced`
- `POST /api/qlib/backtest/enhanced-indexing`
- `POST /api/qlib/retrain`
- `GET  /api/qlib/retrain/status`
- `POST /api/qlib/fit/alpha360`
- `GET  /api/qlib/fit/alpha360/status`

### 前端更新
- 策略回测卡片增加三个 Tab：基础回测 / 增强回测（手续费） / 指数增强策略
- 新增「模型质量评估（IC/ICIR）」卡片（折叠）
- 模型训练卡片新增 Alpha360 DL 训练 + 滚动重训练区域

### Bug 修复（本 session）
- `data_source_manager.py` — `_try_fallback_sources()` 返回 tuple 未解包导致 `'tuple' has no attribute 'split'`
- `data_source_manager.py` — `asyncio event loop is already running`：新增 `_run_async_safe()` 兼容 FastAPI
- `stock_basic_info` — 5208 条重复文档已清除，添加 `symbol` 唯一索引
- `qlib status` 8188 只问题 — 改为读 `instruments/all.txt`（权威来源），删除 2578 个遗留目录
- `linear_ridge` 训练失败 — 新增 `Fillna` processor 防止 dropna 清空所有行

### 涉及文件
- `tradingagents/qlib_service/service.py`（新增 ~250 行）
- `app/routers/qlib_selection.py`（新增 ~180 行 + 7 个 Pydantic 模型）
- `frontend/src/views/QuantSelection/index.vue`（新增 IC评估/增强回测/DL训练/重训练 UI）

### PyTorch
- 已安装：`torch 2.5.1+cu121`，CUDA 可用（GPU 训练就绪）

### 回滚方法
```bash
git checkout HEAD -- tradingagents/qlib_service/service.py app/routers/qlib_selection.py frontend/src/views/QuantSelection/index.vue
```

---

## [2026-05-31] 任务中心时间显示修复（时区错误）

### 问题根因
任务中心（Task Center）显示完成时间为 `04:43+08:00`，而正确值应为 `12:43+08:00`。
- MongoDB 存储的是 UTC 裸时间（无 tzinfo）
- `simple_analysis_service.py` 中使用 `dt.replace(tzinfo=china_tz)` 仅贴标签不转换
- 导致 UTC `04:43` 被错误标注为 `04:43+08:00`（应先 `replace(tzinfo=UTC)` 再 `astimezone(CST)`）
- `reports` 路由一直使用 `to_config_tz()`（正确），所以报告时间显示正常

### 变更内容
- `app/services/simple_analysis_service.py`：两处时间处理块统一改用 `to_config_tz()`（`app/utils/timezone.py` 中的工具函数）

### 涉及文件
- `app/services/simple_analysis_service.py`（2 处修改，约第 2246 行和第 2304 行）

### 回滚方法
```bash
git checkout HEAD -- app/services/simple_analysis_service.py
```

---

## [2026-05-31] 资讯源修复 — mootdx 安装 + 财联社/财新数据源替换

### 问题根因（深度分析）
| 数据源 | 表象 | 真正根因 |
|--------|------|---------|
| mootdx | 模块未找到 | 单纯未安装 |
| 财联社 | 超时/404 | `cls.cn` 服务器端主动重置 WSL IP 的 SSL 连接（SSL EOF），网络层封锁 |
| 百度财经 | cookie 失败 | `hm.baidu.com`（百度统计域名）连接被 reset，AKShare 需经此步骤完成 cookie 链 |
| 新浪/同花顺 | "函数已移除" | **误判**：函数存在但无需 `symbol` 参数，之前误传参数导致 TypeError |

### 变更内容
- `venv/` 中安装 `mootdx==0.11.7`（A 股通达信 TCP 行情）
- `tradingagents/dataflows/news/realtime_news.py`：
  - 删除失效的财联社 RSS URL（`cls.cn` 封锁，无法绕过）
  - 新增 `stock_info_global_ths()` 同花顺全球快讯（字段：标题/内容/发布时间/链接）
  - 新增 `stock_info_global_sina()` 新浪全球快讯（字段：时间/内容）
  - 新增 `stock_news_main_cx()` 财新宏观新闻
- 腾讯行情：确认正常（GBK，代码本身一直正确）

### 数据源最终状态
| 数据源 | 状态 | 方式 |
|--------|------|------|
| 东方财富 | ✅ | `ak.stock_news_em()` 个股新闻 |
| 同花顺 | ✅ | `ak.stock_info_global_ths()` 全球快讯 |
| 新浪 | ✅ | `ak.stock_info_global_sina()` 全球快讯 |
| 财新 | ✅ | `ak.stock_news_main_cx()` 宏观新闻 |
| 腾讯行情 | ✅ | `qt.gtimg.cn` GBK 实时报价 |
| mootdx | ✅ | TCP 7709 A股 OHLCV |
| 财联社 | ❌ | WSL 出口 IP 被 cls.cn 封锁（SSL EOF），无法绕过 |
| 百度财经 | ❌ | hm.baidu.com 统计域名被重置，cookie 链断裂，无法绕过 |

### 涉及文件
- `tradingagents/dataflows/news/realtime_news.py`
- `venv/` (新增 mootdx 包)

### 回滚方法
```bash
git checkout HEAD -- tradingagents/dataflows/news/realtime_news.py
venv/bin/pip uninstall mootdx -y
```

---

## [2026-05-31] 财务数据面板修复 — 三处 Bug 联合修复

### 问题根因
财务数据面板一直显示空数据，原因是三处 Bug 叠加：
1. **AKShare provider 连接未初始化**：`financial_data_sync_service.py` 创建 provider 后未调用 `connect()`，导致 `is_available()` 永远返回 False，同步被跳过
2. **AKShare 数据格式误解**：`stock_financial_abstract()` 返回透视表格式（每行是一个指标名，列是各报告期），而旧代码按普通列名读取，提取结果全为 None
3. **路由层 `ok()` 参数错误**：`financial_data.py` 中 3 处 `ok(success=...)` 调用，但 `ok()` 只接受 `data` 和 `message` 参数

### 变更内容
- `app/worker/financial_data_sync_service.py`：初始化时对每个 provider 调用 `await provider.connect()`
- `app/services/financial_data_service.py`：`_extract_akshare_indicators()` 重写为透视表格式解析（`_pivot_to_flat()` 辅助函数），并增加多候选字段名 fallback
- `app/routers/financial_data.py`：3 处 `ok(success=False, ...)` 改为 `ok(data=..., message=...)`
- `frontend/src/views/Analysis/SingleAnalysis.vue`：字段名修正 `net_profit` → `net_income`，`debt_ratio` → `debt_to_assets`；显示条件改为 `revenue != null || net_income != null || roe != null`

### 涉及文件
- `app/worker/financial_data_sync_service.py`
- `app/services/financial_data_service.py`
- `app/routers/financial_data.py`
- `frontend/src/views/Analysis/SingleAnalysis.vue`

### 回滚方法
```bash
# 仅回滚财务数据相关
git checkout HEAD -- app/worker/financial_data_sync_service.py
git checkout HEAD -- app/services/financial_data_service.py
git checkout HEAD -- app/routers/financial_data.py
git checkout HEAD -- frontend/src/views/Analysis/SingleAnalysis.vue
```

---

## [2026-05-31] 量化选股/因子发现多项修复

### 变更内容
- `tradingagents/qlib_service/service.py`：
  - 恢复被误删的 XGBoost 模型配置（`xgb` 条目），恢复到 6 种算法 × 5 窗口 = 最多 30 个模型
  - `_get_stock_name_map()` 用 AKShare 缓存 A 股名称（24h TTL）
  - `select_ensemble()` 每只股票附加 `name` 字段
- `tradingagents/qlib_service/factor_agent.py`：
  - `run_research_loop()` 启动前加载磁盘已有因子，注入提示词避免 LLM 重复提案
  - 过滤已知 expr，跳过重复因子并记录日志
- `app/routers/qlib_selection.py`：
  - `discover_cron` 默认值改为 `"0 20 * * 1-5"`（每周一至五，而非仅周五）
  - 新增 `_LAST_SELECTION_FILE` 持久化最近一次选股结果到 `~/.qlib/last_selection.json`
  - 新增 `GET /api/qlib/select/last` 端点
- `frontend/src/views/QuantSelection/index.vue`：
  - 股票列表新增「名称」列
  - `onMounted` 自动加载最近一次选股结果
  - discover_cron 输入框 placeholder 更新

### 涉及文件
- `tradingagents/qlib_service/service.py`
- `tradingagents/qlib_service/factor_agent.py`
- `app/routers/qlib_selection.py`
- `frontend/src/views/QuantSelection/index.vue`

### 回滚方法
```bash
git checkout HEAD -- tradingagents/qlib_service/service.py
git checkout HEAD -- tradingagents/qlib_service/factor_agent.py
git checkout HEAD -- app/routers/qlib_selection.py
git checkout HEAD -- frontend/src/views/QuantSelection/index.vue
```

---

## [2026-05-31] 加密货币分析端点新增 + strftime Bug 修复

### 变更内容
- `app/routers/crypto_price.py`：新增 `POST /api/crypto/analyze/{symbol}` 端点，基于 RSI14/MA5/MA20/MACD 输出技术面信号（无需 LLM）
- `tradingagents/dataflows/interface.py`：`get_crypto_stock_data_unified()` 中 strftime 调用增加 `hasattr` 守卫，兼容 Binance 返回的字符串日期格式

### 涉及文件
- `app/routers/crypto_price.py`
- `tradingagents/dataflows/interface.py`

### 回滚方法
```bash
git checkout HEAD -- app/routers/crypto_price.py
git checkout HEAD -- tradingagents/dataflows/interface.py
```

---

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
