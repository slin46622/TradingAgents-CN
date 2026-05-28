"""
Binance 公开 REST API 数据提供器（无需 API Key）
支持加密货币 OHLCV 行情数据获取
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# Binance 公开 K 线接口（无需认证）
_BASE_URL = "https://api.binance.com"
_KLINES_ENDPOINT = "/api/v3/klines"
_PRICE_ENDPOINT = "/api/v3/ticker/price"

# 常见加密货币 USDT 交易对
SUPPORTED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
    "LINKUSDT", "UNIUSDT", "ATOMUSDT", "LTCUSDT", "ETCUSDT",
]


class BinanceProvider:
    """Binance 数据提供器，获取加密货币行情数据。"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "TradingAgents-CN/1.0"})

    def get_ohlcv(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        获取加密货币日线 OHLCV 数据。

        Args:
            symbol: 交易对，如 BTCUSDT
            start_date: 起始日期 YYYY-MM-DD（可选）
            end_date: 结束日期 YYYY-MM-DD（可选）
            limit: 最大返回条数，默认 500

        Returns:
            DataFrame，列：date, open, high, low, close, volume
        """
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        params: dict = {"symbol": symbol, "interval": "1d", "limit": limit}

        if start_date:
            dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params["startTime"] = int(dt.timestamp() * 1000)
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            params["endTime"] = int(dt.timestamp() * 1000)

        try:
            resp = self.session.get(
                _BASE_URL + _KLINES_ENDPOINT, params=params, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Binance 获取 {symbol} K线失败: {e}")
            return pd.DataFrame()

        if not data:
            return pd.DataFrame()

        rows = []
        for bar in data:
            rows.append({
                "date": datetime.fromtimestamp(bar[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
                "volume": float(bar[5]),
            })

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_price(self, symbol: str) -> float:
        """获取当前最新价格。"""
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            resp = self.session.get(
                _BASE_URL + _PRICE_ENDPOINT,
                params={"symbol": symbol},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return float(resp.json()["price"])
        except Exception as e:
            logger.warning(f"Binance 获取 {symbol} 最新价失败: {e}")
            return 0.0

    def get_supported_symbols(self) -> list[str]:
        """返回常用加密货币交易对列表。"""
        return SUPPORTED_SYMBOLS.copy()
