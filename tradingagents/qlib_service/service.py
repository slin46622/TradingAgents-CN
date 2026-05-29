"""Qlib-based quantitative stock selection service.

Wraps Microsoft Qlib's prediction pipeline:
  1. Init Qlib with local binary data (built by QlibDataPipeline)
  2. Fit a LightGBM α-model on Alpha158 factors
  3. Score all symbols → return top-N ranked list
  4. Run a simple backtest using Qlib's built-in engine

Usage:
    from tradingagents.qlib_service.service import QlibService

    svc = QlibService(data_dir="~/.qlib/cn_data")
    svc.fit(train_start="2018-01-01", train_end="2022-12-31")
    result = svc.select(date="2023-10-01", top_n=20)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from tradingagents.utils.logging_init import get_logger

logger = get_logger("qlib_service.service")

_DEFAULT_DATA_DIR = Path.home() / ".qlib" / "cn_data"


class QlibService:
    """Quantitative stock selector backed by Qlib + LightGBM."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir or str(_DEFAULT_DATA_DIR)).expanduser()
        self._initialized = False
        self._model = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def init_qlib(self) -> bool:
        """Initialize Qlib with the local binary data directory."""
        if self._initialized:
            return True
        try:
            import qlib
            from qlib.config import REG_CN

            qlib.init(provider_uri=str(self.data_dir), region=REG_CN)
            self._initialized = True
            logger.info(f"[QlibService] Qlib initialized: {self.data_dir}")
            return True
        except Exception as e:
            logger.error(f"[QlibService] Qlib init failed: {e}")
            return False

    def fit(
        self,
        train_start: str = "2018-01-01",
        train_end: str = "2022-12-31",
        instruments: str = "all",
    ) -> dict:
        """Fit a LightGBM model on Alpha158 factors.

        Returns training metrics.
        """
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}
        try:
            from qlib.contrib.model.gbdt import LGBModel
            from qlib.contrib.data.handler import Alpha158
            from qlib.data.dataset import DatasetH
            from qlib.data.dataset.handler import DataHandlerLP

            logger.info(f"[QlibService] 开始训练: {train_start} ~ {train_end}")

            handler = Alpha158(
                instruments=instruments,
                start_time=train_start,
                end_time=train_end,
                fit_start_time=train_start,
                fit_end_time=train_end,
            )
            dataset = DatasetH(
                handler=handler,
                segments={
                    "train": (train_start, train_end),
                    "test": (train_end, train_end),
                },
            )
            self._model = LGBModel(
                loss="mse",
                colsample_bytree=0.8879,
                learning_rate=0.0421,
                subsample=0.8789,
                lambda_l1=205.6999,
                lambda_l2=580.9768,
                max_depth=8,
                num_leaves=210,
                num_threads=4,
            )
            self._model.fit(dataset)
            logger.info("[QlibService] 训练完成")
            return {"ok": True, "train_start": train_start, "train_end": train_end}
        except Exception as e:
            logger.error(f"[QlibService] 训练失败: {e}")
            return {"error": str(e)}

    def select(
        self,
        date: Optional[str] = None,
        top_n: int = 20,
        instruments: str = "all",
    ) -> dict:
        """Score all instruments and return top-N ranked by predicted return.

        Returns list of {symbol, score, rank}.
        """
        if not self._model:
            return {"error": "Model not fitted — call fit() first"}
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        date = date or datetime.today().strftime("%Y-%m-%d")
        try:
            from qlib.contrib.data.handler import Alpha158
            from qlib.data.dataset import DatasetH

            # Use a small window around the target date for feature extraction
            start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            handler = Alpha158(
                instruments=instruments,
                start_time=start,
                end_time=date,
            )
            dataset = DatasetH(
                handler=handler,
                segments={"test": (date, date)},
            )
            pred = self._model.predict(dataset, segment="test")
            if pred is None or len(pred) == 0:
                return {"date": date, "stocks": [], "total": 0}

            # pred is a Series indexed by (date, symbol)
            df = pred.reset_index()
            if df.shape[1] == 3:
                df.columns = ["date", "symbol", "score"]
            elif df.shape[1] == 2:
                df.columns = ["symbol", "score"]
            else:
                df.columns = list(df.columns[:-1]) + ["score"]
                symbol_col = [c for c in df.columns if c not in ("score", "datetime", "date")]
                df = df.rename(columns={symbol_col[0]: "symbol"}) if symbol_col else df

            df = df.sort_values("score", ascending=False).head(top_n)
            df["rank"] = range(1, len(df) + 1)
            stocks = df[["symbol", "score", "rank"]].to_dict(orient="records")
            for s in stocks:
                s["score"] = round(float(s["score"]), 6)

            logger.info(f"[QlibService] 选股完成: date={date}, top={len(stocks)}")
            return {"date": date, "stocks": stocks, "total": len(stocks)}
        except Exception as e:
            logger.error(f"[QlibService] 选股失败: {e}")
            return {"error": str(e)}

    def backtest(
        self,
        start: str = "2023-01-01",
        end: Optional[str] = None,
        top_n: int = 20,
        instruments: str = "all",
    ) -> dict:
        """Run Qlib's built-in TopkDropoutStrategy backtest.

        Returns portfolio metrics: annualized return, Sharpe, max drawdown.
        """
        if not self._model:
            return {"error": "Model not fitted — call fit() first"}
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        end = end or datetime.today().strftime("%Y-%m-%d")
        try:
            from qlib.contrib.data.handler import Alpha158
            from qlib.data.dataset import DatasetH
            from qlib.contrib.evaluate import risk_analysis
            from qlib.contrib.strategy import TopkDropoutStrategy
            from qlib.contrib.evaluate import backtest as qlib_backtest

            handler = Alpha158(
                instruments=instruments,
                start_time=(datetime.strptime(start, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d"),
                end_time=end,
            )
            dataset = DatasetH(
                handler=handler,
                segments={"test": (start, end)},
            )
            pred = self._model.predict(dataset, segment="test")
            strategy = TopkDropoutStrategy(
                model=self._model,
                dataset=dataset,
                topk=top_n,
                n_drop=5,
            )
            report_normal, _ = qlib_backtest(
                start_time=start,
                end_time=end,
                strategy=strategy,
                executor={"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                          "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}},
            )
            analysis = risk_analysis(report_normal["return"] - report_normal["cost"])
            metrics = analysis.loc["excess_return_with_cost", :]
            result = {
                "start": start,
                "end": end,
                "top_n": top_n,
                "annualized_return": round(float(metrics.get("annualized_return", 0)), 4),
                "sharpe": round(float(metrics.get("information_ratio", 0)), 4),
                "max_drawdown": round(float(metrics.get("max_drawdown", 0)), 4),
                "win_rate": None,  # not directly from risk_analysis
            }
            logger.info(f"[QlibService] 回测完成: {result}")
            return result
        except Exception as e:
            logger.error(f"[QlibService] 回测失败: {e}")
            return {"error": str(e)}

    def status(self) -> dict:
        """Return pipeline status."""
        from tradingagents.qlib_service.data_pipeline import QlibDataPipeline

        pipeline = QlibDataPipeline(str(self.data_dir))
        data_status = pipeline.status()
        return {
            "data_dir": str(self.data_dir),
            "qlib_initialized": self._initialized,
            "model_fitted": self._model is not None,
            **data_status,
        }
