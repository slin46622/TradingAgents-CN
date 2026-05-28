"""测试加密货币数据源"""
import sys
sys.path.insert(0, '/home/sun/worktree-crypto')

from tradingagents.dataflows.providers.crypto.binance import BinanceProvider
from tradingagents.dataflows.providers.crypto.coingecko import CoinGeckoProvider

print("=== Binance 数据源测试 ===")
bp = BinanceProvider()
df = bp.get_ohlcv("BTCUSDT", limit=30)
if not df.empty:
    print(f"✅ BTC 最近 {len(df)} 天数据获取成功")
    print(df.tail(5).to_string(index=False))
    price = bp.get_price("BTCUSDT")
    print(f"✅ BTC 当前价格: ${price:,.2f}")
else:
    print("❌ Binance 数据获取失败")

print("\n=== CoinGecko 备用数据源测试 ===")
cg = CoinGeckoProvider()
df2 = cg.get_ohlcv("BTC", limit=7)
if not df2.empty:
    print(f"✅ CoinGecko BTC 最近 {len(df2)} 天数据获取成功")
    print(df2.tail(3).to_string(index=False))
else:
    print("❌ CoinGecko 数据获取失败（可能被限速，稍后重试）")
