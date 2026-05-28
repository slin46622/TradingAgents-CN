"""
加密货币专属分析师 Agent

直接调用 BinanceProvider REST 接口获取价格/资金费率数据，
再通过 LLM 生成专业的加密货币分析报告。
不依赖 LangChain 工具（tool_node 为空占位）。
"""

from langchain_core.messages import HumanMessage

# 导入统一日志系统
from tradingagents.utils.logging_init import get_logger

logger = get_logger("default")


def _get_funding_rate(symbol: str) -> str:
    """获取 Binance 永续合约资金费率（公开接口，无需Key）"""
    import requests

    try:
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym = sym + "USDT"
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": sym, "limit": 3},
            timeout=8,
        )
        data = r.json()
        if isinstance(data, list) and data:
            latest = float(data[-1]["fundingRate"])
            return (
                f"最新资金费率: {latest * 100:.4f}%"
                f"（正值多头付空头，负值空头付多头）"
            )
    except Exception as e:
        logger.debug(f"[加密分析师] 获取资金费率失败: {e}")
    return "资金费率：暂无数据"


def create_crypto_analyst(llm, toolkit):
    """
    创建加密货币专属分析师。

    Args:
        llm: LangChain LLM 实例
        toolkit: Toolkit 实例（当前未使用，保留接口一致性）

    Returns:
        crypto_analyst_node 函数
    """

    def crypto_analyst_node(state: dict) -> dict:
        ticker = state.get("company_of_interest", state.get("ticker", "BTCUSDT"))
        trade_date = state.get("trade_date", "")

        logger.info(f"[加密分析师] 开始分析 {ticker}，日期: {trade_date}")

        # ── 1. 获取价格数据 ──────────────────────────────────────────
        price_data_str = "暂无价格数据"
        current_price_str = "暂无当前价格"
        try:
            from tradingagents.dataflows.providers.crypto.binance import BinanceProvider

            bp = BinanceProvider()
            df = bp.get_ohlcv(ticker, limit=30)
            current_price = bp.get_price(ticker)

            if df is not None and not df.empty:
                # 只保留最近 30 条，格式化为简洁表格
                rows = []
                for _, row in df.tail(30).iterrows():
                    rows.append(
                        f"  {row['date']}  开:{row['open']:.2f}  高:{row['high']:.2f}"
                        f"  低:{row['low']:.2f}  收:{row['close']:.2f}  量:{row['volume']:.0f}"
                    )
                price_data_str = "\n".join(rows)
                logger.info(f"[加密分析师] 成功获取 {len(df)} 条 OHLCV 数据")
            else:
                logger.warning(f"[加密分析师] OHLCV 数据为空: {ticker}")

            if current_price is not None:
                current_price_str = f"{current_price:.4f} USDT"
        except Exception as e:
            logger.error(f"[加密分析师] 获取价格数据失败: {e}")

        # ── 2. 获取资金费率 ──────────────────────────────────────────
        funding_rate_str = _get_funding_rate(ticker)
        logger.info(f"[加密分析师] {funding_rate_str}")

        # ── 3. 构建分析 Prompt ────────────────────────────────────────
        prompt_text = f"""你是一个专业的加密货币市场分析师。请分析 {ticker} 的近期走势并给出操作建议。

价格数据（最近30天，单位 USDT）：
{price_data_str}

当前价格：{current_price_str}
{funding_rate_str}

请提供：
1. 价格趋势分析（支撑/阻力位、成交量变化、均线形态）
2. 资金费率解读（市场情绪偏多还是偏空，是否存在爆仓风险）
3. 短期操作建议（买入/持有/卖出/观望）及理由
4. 主要风险提示

分析日期：{trade_date}
请用中文撰写专业分析报告，并在报告末尾附上 Markdown 表格总结关键指标与建议。"""

        # ── 4. 调用 LLM ───────────────────────────────────────────────
        messages = state.get("messages", []) + [HumanMessage(content=prompt_text)]
        try:
            result = llm.invoke(messages)
            report = result.content if hasattr(result, "content") else str(result)
            logger.info(f"[加密分析师] 报告生成成功，长度: {len(report)}")
        except Exception as e:
            logger.error(f"[加密分析师] LLM 调用失败: {e}")
            report = f"加密货币分析报告生成失败（{ticker}）：{e}"

        # ── 5. 返回 state 更新 ────────────────────────────────────────
        return {
            "messages": [HumanMessage(content=prompt_text)],
            "crypto_report": report,
            "sender": "CryptoAnalyst",
        }

    return crypto_analyst_node
