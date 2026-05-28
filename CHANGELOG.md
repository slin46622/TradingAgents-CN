# Changelog

## 2026-05-29 — feat/agent-memory-feedback (#4)

### 修改
- `app/routers/paper.py` — 新增 `_write_sell_to_layered_memory()` 辅助函数，平仓后自动写入 layered_memory
- `app/services/simple_analysis_service.py` — 分析启动前注入同标的历史交易记忆至 Agent 上下文

### 效果
- 完整闭环：模拟交易平仓 → 写入长期/中期记忆 → 下次分析同标的时 Agent 读到历史决策

### 回滚命令
```bash
git revert HEAD
```
