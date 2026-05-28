"""
CoinGecko 免费 API 数据提供器（无需 API Key）
作为 Binance 的备用数据源
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.coingecko.com/api/v3"

# 常见币种的 CoinGecko ID 映射
SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "LTC": "litecoin",
}


class CoinGeckoProvider:
    """CoinGecko 数据提供器，作为 Binance 的备用源。"""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "TradingAgents-CN/1.0"})

    def _symbol_to_id(self, symbol: str) -> str:
        """将交易对符号转换为 CoinGecko ID。"""
        clean = symbol.upper().replace("USDT", "").replace("USD", "")
        return SYMBOL_TO_ID.get(clean, clean.lower())

    def get_ohlcv(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 90,
    ) -> pd.DataFrame:
        """
        获取加密货币日线 OHLCV 数据（免费接口限制较多，最多90天）。

        Args:
            symbol: 交易对，如 BTC 或 BTCUSDT
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            limit: 天数，最多 90

        Returns:
            DataFrame，列：date, open, high, low, close, volume
        """
        coin_id = self._symbol_to_id(symbol)
        days = min(limit, 90)

        try:
            # 免费接口：获取最近 N 天的 OHLC 数据
            resp = self.session.get(
                f"{_BASE_URL}/coins/{coin_id}/ohlc",
                params={"vs_currency": "usd", "days": str(days)},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            # 限流保护
            time.sleep(1.2)
        except Exception as e:
            logger.warning(f"CoinGecko 获取 {symbol} OHLC 失败: {e}")
            return pd.DataFrame()

        if not data:
            return pd.DataFrame()

        rows = []
        for bar in data:
            # CoinGecko OHLC 格式：[timestamp_ms, open, high, low, close]
            rows.append({
                "date": datetime.fromtimestamp(bar[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
                "volume": 0.0,  # OHLC 接口不含 volume
            })

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        # 去重（同一天可能有多条）
        df = df.groupby("date").last().reset_index()
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_price(self, symbol: str) -> float:
        """获取当前最新价格。"""
        coin_id = self._symbol_to_id(symbol)
        try:
            resp = self.session.get(
                f"{_BASE_URL}/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get(coin_id, {}).get("usd", 0.0))
        except Exception as e:
            logger.warning(f"CoinGecko 获取 {symbol} 最新价失败: {e}")
            return 0.0
