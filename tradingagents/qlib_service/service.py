"""Qlib-based quantitative stock selection service.

Single-model mode (default):
  fit() → train one LightGBM model on Alpha158
  select() → score all symbols, return top-N

Ensemble mode (Plan B — qlibAssistant-style):
  fit_ensemble() → train N LightGBM models with different configs and rolling windows
  select_ensemble() → average predictions + majority-vote positive filter

Usage:
    svc = QlibService()
    svc.fit(train_start="2018-01-01", train_end="2022-12-31")
    result = svc.select(top_n=20)

    # or ensemble mode
    svc.fit_ensemble(train_end="2022-12-31")
    result = svc.select_ensemble(top_n=20)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from tradingagents.utils.logging_init import get_logger

logger = get_logger("qlib_service.service")

_DEFAULT_DATA_DIR = Path.home() / ".qlib" / "cn_data"


def _make_alpha158_plus(instruments, start_time, end_time, fit_start_time, fit_end_time):
    """Return a DataHandler with Alpha158's 158 features + any discovered custom factors.

    If the factor library on disk is non-empty, the discovered factor expressions
    are appended as additional features (CUSTOM_000, CUSTOM_001, …) so that
    trained models benefit from AI-discovered signals.
    """
    from qlib.contrib.data.handler import Alpha158

    # Load persisted factor library — strong tier first; add candidates if < 10 strong
    try:
        from tradingagents.qlib_service.factor_agent import FactorAgent
        strong = FactorAgent.load_library_from_disk(tier="strong")
        if len(strong) < 10:
            candidates = FactorAgent.load_library_from_disk(tier="candidate")
            extra = strong + candidates
        else:
            extra = strong
    except Exception:
        extra = []

    if not extra:
        return Alpha158(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            fit_start_time=fit_start_time,
            fit_end_time=fit_end_time,
        )

    # Build combined feature list: Alpha158 (158 features) + custom factors
    from qlib.contrib.data.handler import DataHandlerLP
    from qlib.contrib.data.handler import _DEFAULT_LEARN_PROCESSORS
    try:
        from qlib.utils import check_transform_proc
    except ImportError:
        from qlib.contrib.data.handler import check_transform_proc

    base_fields, base_names = Alpha158.__new__(Alpha158).get_feature_config()
    extra_fields = [f["expr"] for f in extra]
    extra_names  = [f"CUSTOM_{i:03d}" for i in range(len(extra_fields))]

    all_fields = base_fields + extra_fields
    all_names  = base_names  + extra_names

    # Append Fillna after CSZScoreNorm: custom factors may have all-NaN columns
    # for stocks with no data; LinearModel.fit() calls dropna() internally and
    # sklearn Ridge cannot tolerate NaN. Filling with 0 is safe here because
    # features are already z-scored (0 == cross-sectional mean).
    learn_procs = check_transform_proc(
        _DEFAULT_LEARN_PROCESSORS + [
            {"class": "Fillna", "kwargs": {"fields_group": "feature", "fill_value": 0}}
        ],
        fit_start_time,
        fit_end_time,
    )

    data_loader = {
        "class": "QlibDataLoader",
        "kwargs": {
            "config": {
                "feature": (all_fields, all_names),
                "label": (["Ref($close, -2)/Ref($close, -1) - 1"], ["LABEL0"]),
            },
            "freq": "day",
        },
    }

    logger.info(
        f"[QlibService] Alpha158+{len(extra_fields)}自定义因子 = {len(all_fields)} 个特征"
    )
    return DataHandlerLP(
        instruments=instruments,
        start_time=start_time,
        end_time=end_time,
        data_loader=data_loader,
        infer_processors=[],
        learn_processors=learn_procs,
        fit_start_time=fit_start_time,
        fit_end_time=fit_end_time,
    )

def _make_alpha360(instruments, start_time, end_time, fit_start_time, fit_end_time):
    """Return a DataHandler with Alpha360's 360 raw OHLCV features (designed for deep learning)."""
    from qlib.contrib.data.handler import Alpha360
    return Alpha360(
        instruments=instruments,
        start_time=start_time,
        end_time=end_time,
        fit_start_time=fit_start_time,
        fit_end_time=fit_end_time,
    )


# ST stock filter — cached 24 h to avoid repeated AKShare calls
_st_codes: set = set()
_st_cache_time: float = 0.0
_ST_CACHE_TTL = 86400.0

# Stock name map — cached 24 h
_stock_name_map: dict = {}
_stock_name_cache_time: float = 0.0


def _get_stock_name_map() -> dict:
    """Return dict mapping code (e.g. '000001') → stock name. Cached 24 h."""
    import time
    global _stock_name_map, _stock_name_cache_time
    if _stock_name_map and (time.time() - _stock_name_cache_time) < _ST_CACHE_TTL:
        return _stock_name_map
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        _stock_name_map = dict(zip(df["code"].str.upper(), df["name"]))
        _stock_name_cache_time = time.time()
        logger.info(f"[QlibService] 股票名称缓存刷新: {len(_stock_name_map)} 只")
        return _stock_name_map
    except Exception as exc:
        logger.warning(f"[QlibService] 获取股票名称失败，跳过: {exc}")
        return {}


def _get_st_codes() -> set:
    """Return a set of stock codes (uppercase, no exchange suffix) that are
    currently ST / *ST / 退市.  Refreshed every 24 h via AKShare.
    Returns empty set and logs a warning if AKShare is unavailable."""
    import time
    global _st_codes, _st_cache_time
    if _st_codes and (time.time() - _st_cache_time) < _ST_CACHE_TTL:
        return _st_codes
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        st_df = df[df["name"].str.contains("ST|退", na=False, case=False)]
        _st_codes = set(st_df["code"].str.upper().tolist())
        _st_cache_time = time.time()
        logger.info(f"[QlibService] ST 股缓存刷新: {len(_st_codes)} 只")
        return _st_codes
    except Exception as exc:
        logger.warning(f"[QlibService] 获取 ST 股列表失败，跳过过滤: {exc}")
        return set()


def _filter_st(df, symbol_col: str = "symbol") -> "import pandas; pandas.DataFrame":
    """Remove ST / *ST / 退市 stocks from a DataFrame.  The symbol column may
    contain exchange suffixes (e.g. '000001.SZ'); they are stripped before
    matching."""
    st_set = _get_st_codes()
    if not st_set:
        return df
    import pandas as pd
    codes = df[symbol_col].str.split(".").str[0].str.upper()
    before = len(df)
    df = df[~codes.isin(st_set)].copy()
    filtered = before - len(df)
    if filtered:
        logger.info(f"[QlibService] 过滤 ST 股: -{filtered} 只 ({before} → {len(df)})")
    return df

# Ensemble: 5 algorithm configs × up to 5 rolling windows ≈ 25 models (qlibAssistant parity)
# Each entry: (label, model_class_name, params)
# 训练线程数：留 4 个核给 FastAPI 事件循环 + OS，其余全给训练
# 4核: 2线程  8核: 4线程  14核: 10线程  32核: 28线程
import os as _os
_TRAIN_THREADS = max(2, (_os.cpu_count() or 4) - 4)

_ENSEMBLE_MODEL_CONFIGS = [
    # ── LightGBM: 3 hyperparameter variants ──────────────────────────────
    ("lgb_shallow", "LGBModel", {
        "loss": "mse", "learning_rate": 0.05, "num_leaves": 128,
        "max_depth": 6, "lambda_l1": 50.0, "lambda_l2": 100.0, "num_threads": _TRAIN_THREADS,
    }),
    ("lgb_default", "LGBModel", {
        "loss": "mse", "learning_rate": 0.0421, "num_leaves": 210,
        "max_depth": 8, "lambda_l1": 205.7, "lambda_l2": 580.98, "num_threads": _TRAIN_THREADS,
    }),
    ("lgb_deep", "LGBModel", {
        "loss": "mse", "learning_rate": 0.02, "num_leaves": 300,
        "max_depth": 10, "lambda_l1": 10.0, "lambda_l2": 50.0, "num_threads": _TRAIN_THREADS,
    }),
    # ── XGBoost ──────────────────────────────────────────────────────────
    ("xgb", "XGBModel", {
        "n_estimators": 300, "max_depth": 8, "learning_rate": 0.05,
        "colsample_bytree": 0.8, "subsample": 0.8,
        "reg_alpha": 10.0, "reg_lambda": 20.0, "n_jobs": _TRAIN_THREADS,
    }),
    # ── DoubleEnsemble (Qlib自研，抗分布漂移) ─────────────────────────────
    ("double_ens", "DEnsembleModel", {
        "base_model": "gbm",
        "loss": "mse",
        "num_models": 4,
        "enable_sr": True,
        "enable_fs": True,
        "alpha1": 1.0,
        "alpha2": 1.0,
        "bins_sr": 10,
        "bins_fs": 5,
        "decay": 0.9,
        "num_threads": _TRAIN_THREADS,
    }),
    # ── Linear Ridge (baseline, 快速) ─────────────────────────────────────
    ("linear_ridge", "LinearModel", {
        "estimator": "ridge",
        "alpha": 0.05,
        "fit_intercept": False,
    }),
    # ── Deep Learning — PyTorch (需要 torch 已安装) ───────────────────────
    ("gru", "GRU", {
        "d_feat": 158, "hidden_size": 64, "num_layers": 2, "dropout": 0.0,
        "n_epochs": 50, "lr": 1e-3, "early_stop": 10, "batch_size": 800,
        "metric": "IC", "loss": "mse",
    }),
    ("lstm", "LSTM", {
        "d_feat": 158, "hidden_size": 64, "num_layers": 2, "dropout": 0.0,
        "n_epochs": 50, "lr": 1e-3, "early_stop": 10, "batch_size": 800,
        "metric": "IC", "loss": "mse",
    }),
    ("alstm", "ALSTM", {
        "d_feat": 158, "hidden_size": 64, "num_layers": 2, "dropout": 0.0,
        "n_epochs": 50, "lr": 1e-3, "early_stop": 10, "batch_size": 800,
        "metric": "IC", "loss": "mse", "rnn_type": "GRU",
    }),
    ("tcn", "TCN", {
        "d_feat": 158, "num_layers": 8, "n_chans": 32, "kernel_size": 5, "dropout": 0.0,
        "n_epochs": 50, "lr": 1e-3, "early_stop": 10, "batch_size": 800,
        "metric": "IC", "loss": "mse",
    }),
    ("transformer", "TransformerModel", {
        "d_feat": 158, "d_model": 64, "nhead": 2, "num_layers": 2, "dropout": 0.0,
        "n_epochs": 50, "lr": 2e-4, "early_stop": 10, "batch_size": 2048,
        "metric": "IC", "loss": "mse",
    }),
    # ── More Deep Learning models ─────────────────────────────────────────
    ("tabnet", "TabnetModel", {
        "d_feat": 158, "out_dim": 64, "final_out_dim": 1,
        "batch_size": 4096, "virtual_batch_size": 256,
        "momentum": 0.02, "n_epochs": 50, "lr": 1e-3,
        "early_stop": 10, "metric": "IC",
    }),
    ("add", "ADD", {
        "d_feat": 158, "hidden_size": 64, "num_layers": 2, "dropout": 0.0,
        "n_epochs": 50, "lr": 1e-3, "early_stop": 10, "batch_size": 800,
        "metric": "IC", "loss": "mse",
    }),
    ("localformer", "LocalformerModel", {
        "d_feat": 158, "d_model": 64, "nhead": 2, "num_layers": 2, "dropout": 0.0,
        "n_epochs": 50, "lr": 2e-4, "early_stop": 10, "batch_size": 2048,
        "metric": "IC", "loss": "mse",
    }),
    ("sfm", "SFM", {
        "d_feat": 158, "freq_dim": 25, "hidden_size": 64, "dropout_W": 0.0, "dropout_U": 0.0,
        "n_epochs": 50, "lr": 1e-3, "early_stop": 10, "batch_size": 800,
        "metric": "IC", "loss": "mse",
    }),
    # ── DNN (simple MLP baseline) ─────────────────────────────────────────
    ("dnn", "DNNModelPytorch", {
        "input_dim": 158, "output_dim": 1, "layers": (256, 128, 64),
        "act": "LeakyReLU", "dropout": 0.0,
        "n_epochs": 50, "lr": 1e-3, "early_stop": 10, "batch_size": 2000,
        "metric": "IC", "loss": "mse",
    }),
]

# Window lookback targets (days). Dynamic logic in fit_ensemble() will clip to actual data range.
_WINDOW_LOOKBACKS_DAYS = [365, 730, 1095, 1460, 1825]  # 1/2/3/4/5 yr


def _instantiate_model(model_class: str, params: dict):
    """Import and instantiate a Qlib model by class name. Returns None if unavailable."""
    try:
        if model_class == "LGBModel":
            from qlib.contrib.model.gbdt import LGBModel
            return LGBModel(**params)
        if model_class == "XGBModel":
            from qlib.contrib.model.xgboost import XGBModel
            return XGBModel(**params)
        if model_class == "DEnsembleModel":
            from qlib.contrib.model.double_ensemble import DEnsembleModel
            return DEnsembleModel(**params)
        if model_class == "LinearModel":
            from qlib.contrib.model.linear import LinearModel
            return LinearModel(**params)
        if model_class == "GRU":
            from qlib.contrib.model.pytorch_gru import GRU
            return GRU(**params)
        if model_class == "LSTM":
            from qlib.contrib.model.pytorch_lstm import LSTM
            return LSTM(**params)
        if model_class == "ALSTM":
            from qlib.contrib.model.pytorch_alstm import ALSTM
            return ALSTM(**params)
        if model_class == "TCN":
            from qlib.contrib.model.pytorch_tcn import TCN
            return TCN(**params)
        if model_class == "TransformerModel":
            from qlib.contrib.model.pytorch_transformer import TransformerModel
            return TransformerModel(**params)
        if model_class == "TabnetModel":
            from qlib.contrib.model.pytorch_tabnet import TabnetModel
            return TabnetModel(**params)
        if model_class == "ADD":
            from qlib.contrib.model.pytorch_add import ADD
            return ADD(**params)
        if model_class == "LocalformerModel":
            from qlib.contrib.model.pytorch_localformer import LocalformerModel
            return LocalformerModel(**params)
        if model_class == "SFM":
            from qlib.contrib.model.pytorch_sfm import SFM
            return SFM(**params)
        if model_class == "DNNModelPytorch":
            from qlib.contrib.model.pytorch_nn import DNNModelPytorch
            return DNNModelPytorch(**params)
    except Exception as exc:
        logger.warning(f"[QlibService] 跳过 {model_class}: {exc}")
    return None


class QlibService:
    """Quantitative stock selector backed by Qlib + LightGBM."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir or str(_DEFAULT_DATA_DIR)).expanduser()
        self._initialized = False
        self._model = None
        self._ensemble: list = []  # list of fitted LGBModel instances
        self._ensemble_save_path = self.data_dir / "ensemble_models.pkl"
        # Auto-load any previously saved ensemble on startup
        self._auto_load_ensemble()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _auto_load_ensemble(self) -> None:
        """Load saved ensemble from disk if available (called at init)."""
        import pickle
        try:
            if self._ensemble_save_path.exists():
                with open(self._ensemble_save_path, "rb") as f:
                    saved = pickle.load(f)
                # Saved format: list of (label, model). Restore as (label, model, None).
                self._ensemble = [(lbl, mdl, None) for lbl, mdl in saved]
                logger.info(f"[QlibService] 自动加载集成模型: {len(self._ensemble)} 个模型 (from {self._ensemble_save_path})")
        except Exception as e:
            logger.warning(f"[QlibService] 自动加载集成模型失败（将忽略）: {e}")
            self._ensemble = []

    def save_ensemble(self) -> dict:
        """Persist the current ensemble to disk (only label+model, not dataset)."""
        import pickle
        if not self._ensemble:
            return {"error": "集成模型为空，无法保存"}
        try:
            self._ensemble_save_path.parent.mkdir(parents=True, exist_ok=True)
            # Save only (label, model) — dataset is never reused after training
            to_save = [(lbl, mdl) for lbl, mdl, _ds in self._ensemble]
            with open(self._ensemble_save_path, "wb") as f:
                pickle.dump(to_save, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info(f"[QlibService] 集成模型已保存: {len(to_save)} 个模型 → {self._ensemble_save_path}")
            return {"ok": True, "models": len(to_save), "path": str(self._ensemble_save_path)}
        except Exception as e:
            logger.error(f"[QlibService] 保存集成模型失败: {e}")
            return {"error": str(e)}

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
            # Qlib raises when qlib.init() is called a second time in the same process
            # (QlibRecorder already activated). Treat this as "already initialized".
            if "reinitialize" in str(e).lower() or "already" in str(e).lower():
                self._initialized = True
                logger.info(f"[QlibService] Qlib already initialized in this process, reusing.")
                return True
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

            df = _filter_st(df)
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

    def fit_ensemble(
        self,
        train_end: str = "2022-12-31",
        instruments: str = "all",
        progress_callback=None,
        log_fn=None,
    ) -> dict:
        """Train an ensemble of LightGBM models (Plan B — qlibAssistant style).

        Trains len(configs) × len(windows) models, each with a different
        hyperparameter config and rolling training window. Final predictions
        are averaged across all models with a majority-positive filter.

        Args:
            train_end: Latest date available for any window. Windows older than
                       this are used for training; the remaining period is left
                       for out-of-sample scoring.
        """
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        def _log(msg: str, level: str = "info"):
            if log_fn:
                try:
                    log_fn(msg, level)
                except Exception:
                    pass
            logger.info(f"[QlibService] {msg}")

        try:
            from qlib.contrib.data.handler import Alpha158
            from qlib.data.dataset import DatasetH
            from datetime import datetime, timedelta

            # Determine actual data start from calendar file
            cal_file = self.data_dir / "calendars" / "day.txt"
            data_start = "2018-01-01"
            if cal_file.exists():
                lines = [l for l in cal_file.read_text().strip().splitlines() if l.strip()]
                if lines:
                    data_start = lines[0]

            data_start_dt = datetime.strptime(data_start, "%Y-%m-%d")
            train_end_dt = datetime.strptime(train_end, "%Y-%m-%d")
            total_days = (train_end_dt - data_start_dt).days

            if total_days < 90:
                return {"error": f"数据量不足（仅 {total_days} 天），需要至少 90 天训练数据"}

            # Build up to 5 rolling windows (1/2/3/4/5 yr lookback, qlibAssistant style)
            windows: list = []
            for lb in _WINDOW_LOOKBACKS_DAYS:
                w_start = max(data_start, (train_end_dt - timedelta(days=lb)).strftime("%Y-%m-%d"))
                window_days = (train_end_dt - datetime.strptime(w_start, "%Y-%m-%d")).days
                if window_days >= 90:
                    entry = (w_start, train_end)
                    if entry not in windows:
                        windows.append(entry)
            if not windows:
                windows = [(data_start, train_end)]

            self._ensemble = []
            total = len(_ENSEMBLE_MODEL_CONFIGS) * len(windows)
            _log(f"集成训练开始: {len(windows)} 个窗口 × {len(_ENSEMBLE_MODEL_CONFIGS)} 算法 = {total} 个模型")
            _log(f"数据范围: {data_start} ~ {train_end}，窗口起点: {[w[0] for w in windows]}")

            completed = 0  # track completed count separately from self._ensemble length
            for win_idx, (win_start, win_end) in enumerate(windows, 1):
                eff_end = min(win_end, train_end)
                if eff_end < win_start:
                    continue

                _log(f"── 窗口 {win_idx}/{len(windows)}: {win_start} ~ {eff_end} ──")

                # Use last 20% of the window as validation (needed by XGBoost / DEnsemble)
                win_start_dt = datetime.strptime(win_start, "%Y-%m-%d")
                win_end_dt = datetime.strptime(eff_end, "%Y-%m-%d")
                valid_offset = timedelta(days=max(30, int((win_end_dt - win_start_dt).days * 0.2)))
                valid_start = (win_end_dt - valid_offset).strftime("%Y-%m-%d")

                try:
                    _log(f"  构建 Alpha158 特征集 ({instruments})…")
                    handler = _make_alpha158_plus(
                        instruments=instruments,
                        start_time=win_start,
                        end_time=eff_end,
                        fit_start_time=win_start,
                        fit_end_time=eff_end,
                    )
                    dataset = DatasetH(
                        handler=handler,
                        segments={
                            "train": (win_start, valid_start),
                            "valid": (valid_start, eff_end),
                            "test":  (eff_end, eff_end),
                        },
                    )
                    _log(f"  数据集构建完成，训练段: {win_start}~{valid_start}，验证段: {valid_start}~{eff_end}")
                except Exception as e:
                    _log(f"  窗口 {win_idx} 数据集构建失败，跳过: {e}", "warn")
                    total -= len(_ENSEMBLE_MODEL_CONFIGS)
                    continue

                for label, model_class, params in _ENSEMBLE_MODEL_CONFIGS:
                    model = _instantiate_model(model_class, params)
                    if model is None:
                        total -= 1
                        _log(f"  跳过 {label}（{model_class} 未安装）", "warn")
                        continue
                    _log(f"  开始训练: {label} ({model_class})")
                    try:
                        model.fit(dataset)
                        self._ensemble.append((label, model, dataset))
                        completed += 1
                        _log(f"  ✓ {label} 训练完成（第 {completed} 个）")
                        if progress_callback:
                            try:
                                progress_callback(completed, total, label, win_start, eff_end)
                            except Exception:
                                pass
                    except Exception as e:
                        total -= 1
                        err_msg = str(e)[:200]
                        _log(f"  ✗ {label} 训练失败（跳过）: {err_msg}", "warn")
                        if progress_callback:
                            try:
                                progress_callback(completed, total, label, win_start, eff_end, error=err_msg)
                            except Exception:
                                pass

            if not self._ensemble:
                _log("所有模型均训练失败，请检查数据范围和依赖安装", "error")
                return {"error": "所有窗口训练失败，请检查数据范围"}

            _log(f"集成训练完成，共 {len(self._ensemble)} 个模型，保存中…")
            save_result = self.save_ensemble()
            if "error" in save_result:
                _log(f"模型保存失败（不影响本次使用）: {save_result['error']}", "warn")
            else:
                _log("模型已持久化到磁盘")
            return {"ok": True, "models": len(self._ensemble), "train_end": train_end}
        except Exception as exc:
            _log(f"集成训练意外中断: {exc}", "error")
            logger.error(f"[QlibService] 集成训练失败: {exc}")
            return {"error": str(exc)}

    def select_ensemble(
        self,
        date: Optional[str] = None,
        top_n: int = 20,
        instruments: str = "all",
        min_positive_ratio: float = 0.5,
    ) -> dict:
        """Score all instruments using ensemble voting.

        Each model produces a score; we average all scores and apply a
        majority-positive filter: only keep stocks where at least
        min_positive_ratio of models give a positive prediction.

        Returns list of {symbol, score, positive_ratio, rank}.
        """
        if not self._ensemble:
            return {"error": "Ensemble not fitted — call fit_ensemble() first"}
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        date = date or datetime.today().strftime("%Y-%m-%d")
        feat_start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")

        try:
            import pandas as pd
            from qlib.contrib.data.handler import Alpha158
            from qlib.data.dataset import DatasetH

            score_frames: list = []

            for label, model, _ in self._ensemble:
                try:
                    handler = _make_alpha158_plus(
                        instruments=instruments,
                        start_time=feat_start,
                        end_time=date,
                        fit_start_time=feat_start,
                        fit_end_time=date,
                    )
                    ds = DatasetH(
                        handler=handler,
                        segments={"test": (date, date)},
                    )
                    pred = model.predict(ds, segment="test")
                    if pred is None or len(pred) == 0:
                        continue
                    df = pred.reset_index()
                    # normalize columns
                    if df.shape[1] == 3:
                        df.columns = ["date", "symbol", "score"]
                    elif df.shape[1] == 2:
                        df.columns = ["symbol", "score"]
                    else:
                        sym_col = [c for c in df.columns if c not in ("score", "date", "datetime")]
                        df = df.rename(columns={sym_col[0]: "symbol"}) if sym_col else df
                        df.columns = list(df.columns[:-1]) + ["score"]

                    df = df[["symbol", "score"]].copy()
                    df["score"] = df["score"].astype(float)
                    df["_model"] = label
                    score_frames.append(df)
                except Exception as e:
                    logger.warning(f"[QlibService] 模型 {label} 预测失败: {e}")

            if not score_frames:
                return {"date": date, "stocks": [], "total": 0, "models_used": 0}

            combined = pd.concat(score_frames, ignore_index=True)
            n_models = combined["_model"].nunique()

            # Aggregate per symbol
            agg = combined.groupby("symbol").agg(
                avg_score=("score", "mean"),
                positive_ratio=("score", lambda x: (x > 0).sum() / len(x)),
                model_count=("_model", "count"),
            ).reset_index()

            # Remove ST / *ST stocks
            agg = _filter_st(agg, symbol_col="symbol")

            # Apply positive-ratio filter
            filtered = agg[agg["positive_ratio"] >= min_positive_ratio].copy()
            if filtered.empty:
                filtered = agg.copy()  # relax filter if nothing passes

            filtered = filtered.sort_values("avg_score", ascending=False).head(top_n)
            filtered["rank"] = range(1, len(filtered) + 1)

            name_map = _get_stock_name_map()
            stocks = []
            for _, row in filtered.iterrows():
                code = str(row["symbol"]).split(".")[0].upper()
                stocks.append({
                    "symbol": row["symbol"],
                    "name": name_map.get(code, ""),
                    "score": round(float(row["avg_score"]), 6),
                    "positive_ratio": round(float(row["positive_ratio"]), 3),
                    "rank": int(row["rank"]),
                })

            logger.info(f"[QlibService] 集成选股完成: date={date}, models={n_models}, top={len(stocks)}")
            return {
                "date": date,
                "stocks": stocks,
                "total": len(stocks),
                "models_used": n_models,
                "min_positive_ratio": min_positive_ratio,
            }
        except Exception as exc:
            logger.error(f"[QlibService] 集成选股失败: {exc}")
            return {"error": str(exc)}

    def evaluate_ic(
        self,
        start: str = "2023-01-01",
        end: Optional[str] = None,
        instruments: str = "all",
    ) -> dict:
        """Compute IC, ICIR, RankIC, RankICIR for each model in the ensemble.

        IC (Information Coefficient): Pearson correlation between predicted and actual returns.
        ICIR = mean(IC) / std(IC) — quality-adjusted IC, >0.5 is considered strong.
        RankIC: Spearman rank correlation (more robust to outliers).
        """
        if not self._ensemble:
            return {"error": "No ensemble models — run fit_ensemble() first"}
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        end = end or datetime.today().strftime("%Y-%m-%d")
        feat_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")

        try:
            import pandas as pd
            import numpy as np
            from qlib.contrib.eva.alpha import calc_ic
            from qlib.data.dataset import DatasetH

            results = []

            for label, model, _ in self._ensemble:
                try:
                    handler = _make_alpha158_plus(
                        instruments=instruments,
                        start_time=feat_start,
                        end_time=end,
                        fit_start_time=feat_start,
                        fit_end_time=end,
                    )
                    ds = DatasetH(
                        handler=handler,
                        segments={"test": (start, end)},
                    )
                    pred = model.predict(ds, segment="test")
                    if pred is None or len(pred) == 0:
                        continue

                    # Get actual labels from the test segment
                    label_df = ds.prepare("test", col_set=["label"])
                    if hasattr(label_df, "squeeze"):
                        label_series = label_df.squeeze()
                    else:
                        label_series = label_df.iloc[:, 0]

                    common_idx = pred.index.intersection(label_series.index)
                    if len(common_idx) == 0:
                        continue

                    ic_series, ric_series = calc_ic(
                        pred.loc[common_idx], label_series.loc[common_idx], dropna=True
                    )
                    ic_mean = float(ic_series.mean()) if len(ic_series) > 0 else 0.0
                    ic_std  = float(ic_series.std())  if len(ic_series) > 1 else 1.0
                    ric_mean = float(ric_series.mean()) if len(ric_series) > 0 else 0.0
                    ric_std  = float(ric_series.std())  if len(ric_series) > 1 else 1.0

                    icir     = ic_mean / ic_std   if ic_std  > 0 else 0.0
                    rankicir = ric_mean / ric_std  if ric_std > 0 else 0.0
                    ic_win   = float((ic_series > 0).sum() / len(ic_series)) if len(ic_series) > 0 else 0.0

                    results.append({
                        "model": label,
                        "IC":        round(ic_mean,  4),
                        "ICIR":      round(icir,      4),
                        "RankIC":    round(ric_mean,  4),
                        "RankICIR":  round(rankicir,  4),
                        "IC_win_rate": round(ic_win, 3),
                        "n_dates":   int(len(ic_series)),
                    })
                    logger.info(
                        f"[QlibService] IC评估 {label}: IC={ic_mean:.4f} ICIR={icir:.4f} "
                        f"RankIC={ric_mean:.4f}"
                    )
                except Exception as e:
                    logger.warning(f"[QlibService] IC评估 {label} 失败: {e}")

            results.sort(key=lambda x: x.get("ICIR", 0), reverse=True)
            return {
                "start": start,
                "end": end,
                "results": results,
                "best_model": results[0]["model"] if results else None,
            }
        except Exception as exc:
            logger.error(f"[QlibService] IC评估失败: {exc}")
            return {"error": str(exc)}

    def backtest_enhanced(
        self,
        start: str = "2023-01-01",
        end: Optional[str] = None,
        top_n: int = 20,
        n_drop: int = 5,
        instruments: str = "all",
        open_cost: float = 0.0005,   # 买入手续费 0.05%
        close_cost: float = 0.0015,  # 卖出手续费 0.05% + 印花税 0.1%
        min_cost: float = 5.0,
        account: float = 1e7,
        benchmark: str = "SH000300",
    ) -> dict:
        """Enhanced backtest using TopkDropoutStrategy with realistic transaction costs.

        Models A-share costs: open_cost=0.05% (commission), close_cost=0.15% (commission+stamp duty).
        Returns: annualized return, Sharpe, max drawdown, win rate, excess return vs benchmark.
        """
        if not self._ensemble:
            return {"error": "No ensemble models — run fit_ensemble() first"}
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        end = end or datetime.today().strftime("%Y-%m-%d")
        feat_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")

        try:
            import pandas as pd
            import numpy as np
            from qlib.contrib.data.handler import Alpha158
            from qlib.data.dataset import DatasetH
            from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
            from qlib.contrib.evaluate import backtest_daily, risk_analysis

            all_preds = []
            for label, model, _ in self._ensemble:
                try:
                    handler = _make_alpha158_plus(
                        instruments=instruments,
                        start_time=feat_start,
                        end_time=end,
                        fit_start_time=feat_start,
                        fit_end_time=end,
                    )
                    ds = DatasetH(
                        handler=handler,
                        segments={"test": (start, end)},
                    )
                    pred = model.predict(ds, segment="test")
                    if pred is not None and len(pred) > 0:
                        all_preds.append(pred)
                except Exception as e:
                    logger.warning(f"[QlibService] 回测预测 {label} 失败: {e}")

            if not all_preds:
                return {"error": "所有模型预测均失败"}

            # Ensemble: average predictions from all models (align on common index)
            if len(all_preds) == 1:
                ensemble_pred = all_preds[0]
            else:
                combined = pd.concat(all_preds, axis=1)
                ensemble_pred = combined.mean(axis=1)
            ensemble_pred = ensemble_pred.dropna()
            ensemble_pred.name = "score"

            strategy = TopkDropoutStrategy(
                signal=ensemble_pred,
                topk=top_n,
                n_drop=n_drop,
                hold_thresh=1,
            )

            exchange_kwargs = {
                "freq": "day",
                "limit_threshold": 0.095,   # A-share ±9.5% circuit breaker
                "deal_price": "close",
                "open_cost": open_cost,
                "close_cost": close_cost,
                "min_cost": min_cost,
            }

            report_df, _ = backtest_daily(
                start_time=start,
                end_time=end,
                strategy=strategy,
                account=account,
                benchmark=benchmark,
                exchange_kwargs=exchange_kwargs,
            )

            # Net return = return − cost
            ret_col = "return" if "return" in report_df.columns else report_df.columns[0]
            cost_col = "cost"   if "cost"   in report_df.columns else None
            net_ret = report_df[ret_col] - (report_df[cost_col] if cost_col else 0)

            metrics = risk_analysis(net_ret)
            ann_return  = float(metrics.get("annualized_return", 0))
            sharpe      = float(metrics.get("information_ratio",  0))
            max_dd      = float(metrics.get("max_drawdown",       0))
            win_days    = int((net_ret > 0).sum())
            total_days  = len(net_ret)
            win_rate    = round(win_days / total_days, 4) if total_days > 0 else 0.0

            result = {
                "start": start,
                "end": end,
                "top_n": top_n,
                "n_drop": n_drop,
                "open_cost": open_cost,
                "close_cost": close_cost,
                "account": account,
                "annualized_return": round(ann_return, 4),
                "sharpe":            round(sharpe,     4),
                "max_drawdown":      round(max_dd,     4),
                "win_rate":          win_rate,
                "models_used":       len(all_preds),
            }
            logger.info(f"[QlibService] 增强回测完成: {result}")
            return result

        except Exception as exc:
            logger.error(f"[QlibService] 增强回测失败: {exc}")
            return {"error": str(exc)}

    def backtest_enhanced_indexing(
        self,
        start: str = "2023-01-01",
        end: Optional[str] = None,
        instruments: str = "all",
        index_weights_path: Optional[str] = None,
        open_cost: float = 0.0005,
        close_cost: float = 0.0015,
        account: float = 1e7,
    ) -> dict:
        """Run EnhancedIndexingStrategy backtest (active + passive hybrid).

        Combines model alpha signals with index tracking to reduce tracking error
        while still capturing alpha from high-confidence predictions.
        """
        if not self._ensemble:
            return {"error": "No ensemble models — run fit_ensemble() first"}
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        end = end or datetime.today().strftime("%Y-%m-%d")
        feat_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")

        try:
            import pandas as pd
            from qlib.data.dataset import DatasetH
            from qlib.contrib.strategy.signal_strategy import EnhancedIndexingStrategy
            from qlib.contrib.evaluate import backtest_daily, risk_analysis

            all_preds = []
            for label, model, _ in self._ensemble:
                try:
                    handler = _make_alpha158_plus(
                        instruments=instruments,
                        start_time=feat_start,
                        end_time=end,
                        fit_start_time=feat_start,
                        fit_end_time=end,
                    )
                    ds = DatasetH(handler=handler, segments={"test": (start, end)})
                    pred = model.predict(ds, segment="test")
                    if pred is not None and len(pred) > 0:
                        all_preds.append(pred)
                except Exception as e:
                    logger.warning(f"[QlibService] EnhancedIndexing 预测 {label} 失败: {e}")

            if not all_preds:
                return {"error": "所有模型预测均失败"}

            ensemble_pred = pd.concat(all_preds, axis=1).mean(axis=1).dropna() if len(all_preds) > 1 else all_preds[0].dropna()
            ensemble_pred.name = "score"

            strategy_kwargs = {
                "signal": ensemble_pred,
                "instruments": instruments,
            }
            if index_weights_path:
                strategy_kwargs["index_weights"] = index_weights_path

            strategy = EnhancedIndexingStrategy(**strategy_kwargs)

            report_df, _ = backtest_daily(
                start_time=start,
                end_time=end,
                strategy=strategy,
                account=account,
                exchange_kwargs={
                    "freq": "day",
                    "open_cost": open_cost,
                    "close_cost": close_cost,
                    "min_cost": 5,
                },
            )

            ret_col  = "return" if "return" in report_df.columns else report_df.columns[0]
            cost_col = "cost"   if "cost"   in report_df.columns else None
            net_ret  = report_df[ret_col] - (report_df[cost_col] if cost_col else 0)
            metrics  = risk_analysis(net_ret)

            return {
                "strategy": "EnhancedIndexing",
                "start": start,
                "end": end,
                "annualized_return": round(float(metrics.get("annualized_return", 0)), 4),
                "sharpe":            round(float(metrics.get("information_ratio",  0)), 4),
                "max_drawdown":      round(float(metrics.get("max_drawdown",       0)), 4),
                "win_rate":          round(float((net_ret > 0).sum() / len(net_ret)), 4) if len(net_ret) > 0 else 0.0,
                "models_used":       len(all_preds),
            }
        except Exception as exc:
            logger.error(f"[QlibService] EnhancedIndexing 回测失败: {exc}")
            return {"error": str(exc)}

    def retrain_incremental(
        self,
        days_back: int = 365,
        instruments: str = "all",
        log_fn=None,
    ) -> dict:
        """Rolling/incremental retrain: add recent data and retrain ensemble.

        Uses the most recent `days_back` days of data as the training window,
        replacing old models. This implements the online learning / rolling
        window strategy recommended by qlib for live trading systems.
        """
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        def _log(msg: str, level: str = "info"):
            if log_fn:
                try:
                    log_fn(msg, level)
                except Exception:
                    pass
            logger.info(f"[QlibService] {msg}")

        try:
            train_end = datetime.today().strftime("%Y-%m-%d")
            train_start = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")

            _log(f"滚动重训练: {train_start} ~ {train_end}（最近 {days_back} 天数据）")
            result = self.fit_ensemble(
                train_end=train_end,
                instruments=instruments,
                log_fn=log_fn,
            )
            if "error" in result:
                return result

            _log(f"滚动重训练完成，共 {result.get('models', 0)} 个模型")
            return {
                "ok": True,
                "train_start": train_start,
                "train_end": train_end,
                "models": result.get("models", 0),
                "days_back": days_back,
            }
        except Exception as exc:
            logger.error(f"[QlibService] 滚动重训练失败: {exc}")
            return {"error": str(exc)}

    def fit_ensemble_alpha360(
        self,
        train_end: str = "2022-12-31",
        instruments: str = "all",
        progress_callback=None,
        log_fn=None,
    ) -> dict:
        """Train deep learning models using Alpha360 (360 raw OHLCV features).

        Alpha360 is specifically designed for sequence models (GRU/LSTM/Transformer).
        This method trains ONLY the DL model configs against Alpha360 features,
        storing results separately so they can be merged with the main ensemble.
        """
        if not self.init_qlib():
            return {"error": "Qlib not initialized"}

        def _log(msg: str, level: str = "info"):
            if log_fn:
                try:
                    log_fn(msg, level)
                except Exception:
                    pass
            logger.info(f"[QlibService] {msg}")

        dl_configs = [cfg for cfg in _ENSEMBLE_MODEL_CONFIGS if cfg[1] in (
            "GRU", "LSTM", "ALSTM", "TCN", "TransformerModel"
        )]
        if not dl_configs:
            return {"error": "No DL model configs found"}

        # Update d_feat to 360 for Alpha360
        dl_configs_360 = [
            (label, cls, {**params, "d_feat": 360})
            for label, cls, params in dl_configs
        ]

        try:
            from qlib.data.dataset import DatasetH
            from datetime import datetime, timedelta

            cal_file = self.data_dir / "calendars" / "day.txt"
            data_start = "2018-01-01"
            if cal_file.exists():
                lines = [l for l in cal_file.read_text().strip().splitlines() if l.strip()]
                if lines:
                    data_start = lines[0]

            train_end_dt = datetime.strptime(train_end, "%Y-%m-%d")
            win_start_dt = max(
                datetime.strptime(data_start, "%Y-%m-%d"),
                train_end_dt - timedelta(days=730),
            )
            win_start = win_start_dt.strftime("%Y-%m-%d")
            valid_start = (train_end_dt - timedelta(days=60)).strftime("%Y-%m-%d")

            _log(f"Alpha360 深度学习训练: {win_start} ~ {train_end}")

            handler = _make_alpha360(
                instruments=instruments,
                start_time=win_start,
                end_time=train_end,
                fit_start_time=win_start,
                fit_end_time=train_end,
            )
            dataset = DatasetH(
                handler=handler,
                segments={
                    "train": (win_start, valid_start),
                    "valid": (valid_start, train_end),
                    "test":  (train_end, train_end),
                },
            )

            new_models = []
            for label, model_class, params in dl_configs_360:
                model = _instantiate_model(model_class, params)
                if model is None:
                    _log(f"跳过 {label}（{model_class} 不可用）", "warn")
                    continue
                _log(f"开始训练 Alpha360/{label}")
                try:
                    model.fit(dataset)
                    new_models.append((f"a360_{label}", model, dataset))
                    _log(f"✓ Alpha360/{label} 训练完成")
                    if progress_callback:
                        try:
                            progress_callback(len(new_models), len(dl_configs_360), label, win_start, train_end)
                        except Exception:
                            pass
                except Exception as e:
                    _log(f"✗ Alpha360/{label} 失败: {str(e)[:200]}", "warn")

            if not new_models:
                return {"error": "所有 Alpha360 模型均训练失败，请确认 PyTorch 已安装"}

            # Merge into main ensemble
            self._ensemble.extend(new_models)
            self.save_ensemble()
            _log(f"Alpha360 训练完成: {len(new_models)} 个 DL 模型已并入集成")
            return {"ok": True, "models": len(new_models), "train_end": train_end}

        except Exception as exc:
            logger.error(f"[QlibService] Alpha360 训练失败: {exc}")
            return {"error": str(exc)}

    def status(self) -> dict:
        """Return pipeline status."""
        from tradingagents.qlib_service.data_pipeline import QlibDataPipeline

        pipeline = QlibDataPipeline(str(self.data_dir))
        data_status = pipeline.status()
        # Summarise ensemble composition: {label: count}
        ensemble_types: dict = {}
        for label, _m, _d in self._ensemble:
            prefix = label.split("_")[0]  # e.g. "lgb" from "lgb_shallow"
            ensemble_types[prefix] = ensemble_types.get(prefix, 0) + 1

        return {
            "data_dir": str(self.data_dir),
            "qlib_initialized": self._initialized,
            "model_fitted": self._model is not None,
            "ensemble_models": len(self._ensemble),
            "ensemble_composition": ensemble_types,
            "ensemble_saved": self._ensemble_save_path.exists(),
            **data_status,
        }
