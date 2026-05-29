"""AKShare → Qlib data pipeline for A-share markets.

Converts daily OHLCV data fetched via AKShare into the binary format
that Qlib's FileStorageProvider expects:

  <qlib_data_dir>/
    calendars/day.txt          — trading day list
    instruments/all.txt        — symbol universe
    features/<symbol>/
      open.day.bin
      high.day.bin
      low.day.bin
      close.day.bin
      volume.day.bin
      factor.day.bin           — adjustment factor (1.0 if not available)

Usage:
    from tradingagents.qlib_service.data_pipeline import QlibDataPipeline

    pipeline = QlibDataPipeline(data_dir="~/.qlib/cn_data")
    pipeline.build(symbols=["000001", "000002", ...], start="2020-01-01")
    pipeline.update(symbols=[...])          # incremental update
"""

from __future__ import annotations

import os
import struct
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from tradingagents.utils.logging_init import get_logger

logger = get_logger("qlib_service.pipeline")

_DEFAULT_DATA_DIR = Path.home() / ".qlib" / "cn_data"

# CSI 300 + CSI 500 core symbols (padded to 6 digits)
# Used as default universe when none provided
DEFAULT_UNIVERSE_SIZE = 500  # fetch top-N by market cap from AKShare


class QlibDataPipeline:
    """Download A-share OHLCV via AKShare and write Qlib binary files."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir or str(_DEFAULT_DATA_DIR)).expanduser()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def build(
        self,
        symbols: Optional[list] = None,
        start: str = "2018-01-01",
        end: Optional[str] = None,
    ) -> dict:
        """Full build: download all symbols from *start* to *end*.

        Returns a status dict with counts.
        """
        end = end or datetime.today().strftime("%Y-%m-%d")
        symbols = symbols or self._fetch_universe()
        logger.info(f"[QlibPipeline] 开始构建数据库，股票数: {len(symbols)}, 周期: {start}~{end}")

        self._ensure_dirs()
        calendars = self._build_calendars(start, end)
        ok, fail = 0, 0
        for sym in symbols:
            try:
                df = self._fetch_ohlcv(sym, start, end)
                if df is not None and not df.empty:
                    self._write_symbol(sym, df, calendars)
                    ok += 1
            except Exception as e:
                logger.warning(f"[QlibPipeline] {sym} 失败: {e}")
                fail += 1

        self._write_instruments(symbols, start, end)
        self._write_calendars(calendars)
        logger.info(f"[QlibPipeline] 完成: ok={ok}, fail={fail}")
        return {"ok": ok, "fail": fail, "total": len(symbols)}

    def update(
        self,
        symbols: Optional[list] = None,
        days_back: int = 30,
    ) -> dict:
        """Incremental update: only re-download the last *days_back* days."""
        start = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        end = datetime.today().strftime("%Y-%m-%d")
        symbols = symbols or self._load_existing_symbols()
        if not symbols:
            return {"error": "No existing symbols found, run build() first"}
        return self.build(symbols=symbols, start=start, end=end)

    def status(self) -> dict:
        """Return information about the current data directory."""
        feat_dir = self.data_dir / "features"
        cal_file = self.data_dir / "calendars" / "day.txt"
        symbols = [d.name for d in feat_dir.iterdir()] if feat_dir.exists() else []
        dates = []
        if cal_file.exists():
            dates = cal_file.read_text().strip().splitlines()
        return {
            "data_dir": str(self.data_dir),
            "symbols": len(symbols),
            "calendar_days": len(dates),
            "last_date": dates[-1] if dates else None,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self):
        for sub in ("calendars", "instruments", "features"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

    def _fetch_universe(self) -> list:
        """Fetch A-share symbol list via AKShare (stock_info_a_code_name)."""
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            symbols = df["code"].tolist()[:DEFAULT_UNIVERSE_SIZE]
            logger.info(f"[QlibPipeline] AKShare 获取到 {len(symbols)} 只股票")
            return symbols
        except Exception as e:
            logger.error(f"[QlibPipeline] 获取股票列表失败: {e}")
            return []

    def _fetch_ohlcv(self, symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """Download daily OHLCV for one symbol via AKShare."""
        try:
            import akshare as ak
            # AKShare 前缀：6开头=sh，0/3开头=sz
            prefix = "sh" if symbol.startswith("6") else "sz"
            full_code = f"{prefix}{symbol}"
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust="hfq",  # 后复权
            )
            if df is None or df.empty:
                return None
            df = df.rename(columns={
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            # Add factor column (1.0 — data is already adjusted)
            df["factor"] = 1.0
            return df[["open", "high", "low", "close", "volume", "factor"]]
        except Exception as e:
            logger.debug(f"[QlibPipeline] {symbol} AKShare 下载失败: {e}")
            return None

    def _build_calendars(self, start: str, end: str) -> list:
        """Generate trading day list (Mon-Fri, excluding known CN holidays — simplified)."""
        try:
            import akshare as ak
            # Use trade_date_hist_sina for official trading calendar
            df = ak.tool_trade_date_hist_sina()
            dates = pd.to_datetime(df.iloc[:, 0])
            mask = (dates >= start) & (dates <= end)
            return [d.strftime("%Y-%m-%d") for d in sorted(dates[mask])]
        except Exception:
            # Fallback: generate all weekdays (not perfectly accurate)
            cal = []
            cur = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)
            while cur <= end_ts:
                if cur.weekday() < 5:
                    cal.append(cur.strftime("%Y-%m-%d"))
                cur += timedelta(days=1)
            return cal

    def _write_calendars(self, calendars: list):
        path = self.data_dir / "calendars" / "day.txt"
        path.write_text("\n".join(calendars) + "\n", encoding="utf-8")

    def _write_instruments(self, symbols: list, start: str, end: str):
        path = self.data_dir / "instruments" / "all.txt"
        lines = [f"{s.upper()}\t{start}\t{end}" for s in symbols]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_symbol(self, symbol: str, df: pd.DataFrame, calendars: list):
        """Write per-field .bin files for one symbol."""
        sym_dir = self.data_dir / "features" / symbol.upper()
        sym_dir.mkdir(parents=True, exist_ok=True)

        cal_index = {d: i for i, d in enumerate(calendars)}
        fields = ["open", "high", "low", "close", "volume", "factor"]

        for field in fields:
            if field not in df.columns:
                continue
            values = df[field].values.astype("float32")
            dates_str = df.index.strftime("%Y-%m-%d").tolist()

            # Find start offset in calendar
            start_offset = None
            for d in dates_str:
                if d in cal_index:
                    start_offset = cal_index[d]
                    break
            if start_offset is None:
                continue

            # Align to calendar (fill missing days with NaN)
            end_offset = cal_index.get(dates_str[-1], start_offset + len(values) - 1)
            aligned = [float("nan")] * (end_offset - start_offset + 1)
            for i, d in enumerate(dates_str):
                if d in cal_index:
                    idx = cal_index[d] - start_offset
                    if 0 <= idx < len(aligned):
                        aligned[idx] = float(values[i])

            out_path = sym_dir / f"{field}.day.bin"
            with open(out_path, "wb") as f:
                # Qlib binary format: 4-byte uint32 start_offset + float32 values
                f.write(struct.pack("<I", start_offset))
                for v in aligned:
                    f.write(struct.pack("<f", v))

    def _load_existing_symbols(self) -> list:
        feat_dir = self.data_dir / "features"
        if not feat_dir.exists():
            return []
        return [d.name.lower() for d in feat_dir.iterdir() if d.is_dir()]
