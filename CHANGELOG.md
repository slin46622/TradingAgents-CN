# CHANGELOG

## [2026-05-29] Issue #13 — Telegram Bot AI信号推送

### 变更内容
- `tradingagents/notification/__init__.py` (新建): 导出 TelegramNotifier
- `tradingagents/notification/telegram.py` (新建):
  - `TelegramNotifier` 类封装 Telegram Bot API
  - `send_signal()` 推送含方向/置信度/理由的交易信号
  - `start_reply_listener()` 后台轮询监听「确认」/「忽略」回复
  - `test_connection()` 验证 Bot Token 连通性
- `app/routers/telegram.py` (新建):
  - `GET /api/telegram/config` 读取配置（脱敏 token）
  - `POST /api/telegram/config` 保存配置
  - `POST /api/telegram/test` 发送测试消息
  - `POST /api/telegram/send_signal` 手动推送（调试用）
  - `notify_analysis_result()` 供分析服务调用
- `app/main.py`: 注册 telegram_router
- `app/services/simple_analysis_service.py`:
  - 新增 `_fire_telegram_notification()` 后台线程推送
  - 分析完成后自动触发 Telegram 通知
- `frontend/src/views/Settings/TelegramConfig.vue` (新建): 配置 UI
- `frontend/src/router/index.ts`: 新增 `/settings/telegram` 路由
- `frontend/src/components/Layout/SidebarMenu.vue`: 新增菜单项

### 涉及文件
- `tradingagents/notification/__init__.py` (新建)
- `tradingagents/notification/telegram.py` (新建)
- `app/routers/telegram.py` (新建)
- `app/main.py`
- `app/services/simple_analysis_service.py`
- `frontend/src/views/Settings/TelegramConfig.vue` (新建)
- `frontend/src/router/index.ts`
- `frontend/src/components/Layout/SidebarMenu.vue`

### 回滚方法
```bash
git revert HEAD --no-edit
```
