# CHANGELOG

## [2026-05-29] Issue #10 — A股社交情绪分析师 Agent

### 变更内容
- `tradingagents/agents/analysts/cn_social_analyst.py` (新建):
  - `create_cn_social_analyst(llm, toolkit)` 返回节点函数
  - 仅对6位纯数字A股代码激活，非A股直接返回空报告
  - 预取雪球+股吧数据，构建结构化 prompt，直接调用 LLM
  - 写入 `cn_social_report` 和 `cn_social_tool_call_count`
- `tradingagents/agents/utils/agent_states.py`:
  - 新增 `cn_social_report: Annotated[str, ...]`
  - 新增 `cn_social_tool_call_count: Annotated[int, ...]`
- `tradingagents/graph/analyst_execution.py`:
  - `ANALYST_NODE_SPECS` 注册 `"cn_social"` 条目
- `tradingagents/graph/setup.py`:
  - 新增 `if "cn_social" in selected_analysts:` 导入分析师
- `tradingagents/graph/trading_graph.py`:
  - `_create_tool_nodes()` 新增 `"cn_social": ToolNode([])`
  - `_log_state()` 新增 `cn_social_report` 字段
- `tradingagents/graph/conditional_logic.py`:
  - 新增 `should_continue_cn_social()` 路由函数
- `tradingagents/graph/propagation.py`:
  - `create_initial_state()` 新增 `cn_social_report: ""`

### 涉及文件
- `tradingagents/agents/analysts/cn_social_analyst.py` (新建)
- `tradingagents/agents/utils/agent_states.py`
- `tradingagents/graph/analyst_execution.py`
- `tradingagents/graph/setup.py`
- `tradingagents/graph/trading_graph.py`
- `tradingagents/graph/conditional_logic.py`
- `tradingagents/graph/propagation.py`

### 回滚方法
```bash
git revert HEAD --no-edit
```
