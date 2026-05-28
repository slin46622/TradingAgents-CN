"""
加密货币数据提供器
支持 Binance（主要）和 CoinGecko（备用）
"""

try:
    from .binance import BinanceProvider
    BINANCE_AVAILABLE = True
except ImportError:
    BinanceProvider = None
    BINANCE_AVAILABLE = False

try:
    from .coingecko import CoinGeckoProvider
    COINGECKO_AVAILABLE = True
except ImportError:
    CoinGeckoProvider = None
    COINGECKO_AVAILABLE = False

__all__ = [
    'BinanceProvider',
    'BINANCE_AVAILABLE',
    'CoinGeckoProvider',
    'COINGECKO_AVAILABLE',
]
