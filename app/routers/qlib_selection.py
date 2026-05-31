"""Qlib quantitative stock selection endpoints.

Endpoints:
  GET  /api/qlib/status          — data directory + model status
  POST /api/qlib/build           — download data and write Qlib binary files
  POST /api/qlib/fit             — train LightGBM α-model on Alpha158
  POST /api/qlib/select          — score symbols, return top-N ranked list
  POST /api/qlib/backtest        — run TopkDropout backtest, return metrics
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from app.core.response import ok
from tradingagents.qlib_service.data_pipeline import QlibDataPipeline
from tradingagents.qlib_service.service import QlibService
from tradingagents.qlib_service.factor_agent import FactorAgent

router = APIRouter(prefix="/qlib", tags=["qlib-selection"])

# Module-level singleton — keeps model in memory between requests
_svc: Optional[QlibService] = None
_build_running = False
_download_running = False
_update_running = False

# Ensemble fit progress
_fit_ensemble_running = False
_fit_ensemble_progress: dict = {}

# Factor agent singleton (persists factor library in memory)
_factor_agent: Optional[FactorAgent] = None
_discover_running = False
_discover_result: Optional[dict] = None  # last completed discovery result

# Nightly auto-run
_NIGHTLY_CONFIG_FILE = Path.home() / ".qlib" / "nightly_config.json"
_NIGHTLY_RESULT_FILE = Path.home() / ".qlib" / "nightly_results.json"
_nightly_scheduler = None  # set by setup_nightly_jobs()


def _get_factor_agent() -> FactorAgent:
    global _factor_agent
    if _factor_agent is None:
        _factor_agent = FactorAgent()
    return _factor_agent


def _get_svc() -> QlibService:
    global _svc
    if _svc is None:
        _svc = QlibService()
    return _svc


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DownloadRequest(BaseModel):
    release_date: Optional[str] = Field(None, description="chenditc 发布日期，如 '2024-09-10'")
    data_url: Optional[str] = Field(None, description="自定义下载 URL（覆盖 release_date）")
    data_dir: Optional[str] = Field(None, description="Qlib 数据目录，默认 ~/.qlib/cn_data")


class BuildRequest(BaseModel):
    symbols: Optional[list] = None
    start: str = Field("2018-01-01", description="数据起始日期")
    end: Optional[str] = Field(None, description="数据结束日期，默认今天")
    data_dir: Optional[str] = Field(None, description="Qlib 数据目录，默认 ~/.qlib/cn_data")


class FitRequest(BaseModel):
    train_start: str = Field("2018-01-01")
    train_end: str = Field("2022-12-31")
    instruments: str = Field("all")
    data_dir: Optional[str] = None


class SelectRequest(BaseModel):
    date: Optional[str] = Field(None, description="选股日期，默认今天")
    top_n: int = Field(20, ge=1, le=200)
    instruments: str = Field("all")


class BacktestRequest(BaseModel):
    start: str = Field("2023-01-01")
    end: Optional[str] = None
    top_n: int = Field(20, ge=1, le=200)
    instruments: str = Field("all")


class UpdateRequest(BaseModel):
    days_back: int = Field(600, ge=7, le=3650, description="回溯天数，默认600（约覆盖2年缺口）")
    data_dir: Optional[str] = Field(None, description="Qlib 数据目录，默认 ~/.qlib/cn_data")


class DiscoverRequest(BaseModel):
    n_iter: int = Field(3, ge=1, le=10, description="R&D 循环轮数")
    factors_per_iter: int = Field(5, ge=2, le=10, description="每轮提案因子数")
    eval_start: str = Field("2022-01-01", description="IC 评估起始日期")
    eval_end: str = Field("2023-12-31", description="IC 评估结束日期")
    n_sample: int = Field(300, ge=50, le=1000, description="评估抽样股票数（越多越准但越慢）")
    data_dir: Optional[str] = None


class DiagnoseRequest(BaseModel):
    backtest_result: Optional[dict] = Field(None, description="回测结果 dict")
    selection_result: Optional[dict] = Field(None, description="选股结果 dict")


class EnsembleFitRequest(BaseModel):
    train_end: str = Field("2022-12-31", description="集成训练截止日期")
    instruments: str = Field("all")
    data_dir: Optional[str] = None


class EnsembleSelectRequest(BaseModel):
    date: Optional[str] = Field(None, description="选股日期，默认今天")
    top_n: int = Field(20, ge=1, le=200)
    instruments: str = Field("all")
    min_positive_ratio: float = Field(0.5, ge=0.0, le=1.0, description="最小正向模型比例过滤器")


class ICEvalRequest(BaseModel):
    start: str = Field("2023-01-01", description="IC 评估起始日期")
    end: Optional[str] = Field(None, description="IC 评估结束日期，默认今天")
    instruments: str = Field("all")


class BacktestEnhancedRequest(BaseModel):
    start: str = Field("2023-01-01")
    end: Optional[str] = None
    top_n: int = Field(20, ge=1, le=200)
    n_drop: int = Field(5, ge=1, le=50)
    instruments: str = Field("all")
    open_cost: float = Field(0.0005, ge=0.0, le=0.01, description="买入手续费率，默认0.05%")
    close_cost: float = Field(0.0015, ge=0.0, le=0.02, description="卖出手续费率，默认0.15%（含印花税）")
    account: float = Field(1e7, description="初始资金")


class EnhancedIndexingRequest(BaseModel):
    start: str = Field("2023-01-01")
    end: Optional[str] = None
    instruments: str = Field("all")
    open_cost: float = Field(0.0005)
    close_cost: float = Field(0.0015)
    account: float = Field(1e7)


class RetrainRequest(BaseModel):
    days_back: int = Field(365, ge=90, le=1825, description="滚动窗口天数，默认365天")
    instruments: str = Field("all")


class Alpha360FitRequest(BaseModel):
    train_end: str = Field("2022-12-31", description="Alpha360 训练截止日期")
    instruments: str = Field("all")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/download")
async def qlib_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    """Download pre-built Qlib binary data from chenditc/investment_data GitHub releases.

    Much faster than /build — no AKShare required. Runs in background (~5-15 min for 2 GB).
    """
    global _download_running
    if _download_running:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="数据下载正在进行中，请稍候")

    def _run():
        global _download_running, _svc
        _download_running = True
        try:
            pipeline = QlibDataPipeline(req.data_dir)
            result = pipeline.download_prebuilt(
                data_url=req.data_url,
                release_date=req.release_date,
            )
            if "ok" in result:
                # Reset service so it re-initializes with new data
                _svc = QlibService(req.data_dir)
        finally:
            _download_running = False

    background_tasks.add_task(_run)
    return ok({"started": True, "message": "预构建数据下载已在后台启动，完成后刷新状态（约5-15分钟，取决于网速）"})


@router.get("/download/status")
async def qlib_download_status():
    """Return whether a download is currently in progress."""
    return ok({"running": _download_running})


@router.get("/status")
async def qlib_status():
    """Return data directory and model status."""
    return ok(_get_svc().status())


@router.post("/build")
async def qlib_build(req: BuildRequest, background_tasks: BackgroundTasks):
    """Start async data download and Qlib binary file generation.

    Runs in a dedicated daemon thread so it never blocks HTTP workers.
    Returns immediately; progress visible in server logs.
    """
    global _build_running
    if _build_running:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="数据构建正在运行中")

    import threading

    def _run():
        global _build_running
        _build_running = True
        try:
            pipeline = QlibDataPipeline(req.data_dir)
            result = pipeline.build(
                symbols=req.symbols,
                start=req.start,
                end=req.end,
            )
            logger.info(f"[Build] 全量数据构建完成: {result}")
        except Exception as exc:
            logger.error(f"[Build] 数据构建失败: {exc}")
        finally:
            _build_running = False

    t = threading.Thread(target=_run, daemon=True, name="qlib-build")
    t.start()
    return ok({"started": True, "message": "数据构建已在后台独立线程启动，不影响API响应，请查看服务器日志"})


@router.post("/build/sync")
async def qlib_build_sync(req: BuildRequest):
    """Synchronous data build (blocks until complete). For small symbol lists."""
    pipeline = QlibDataPipeline(req.data_dir)
    result = pipeline.build(symbols=req.symbols, start=req.start, end=req.end)
    if "error" in result:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["error"])
    return ok(result)


@router.post("/update")
async def qlib_update(req: UpdateRequest, background_tasks: BackgroundTasks):
    """Incremental data update via AKShare — only downloads the last N days.

    Use after the initial prebuilt download to fill the gap to today.
    With 5000+ symbols this typically takes 2-4 hours; runs in background.
    """
    global _update_running
    if _update_running:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="数据更新正在运行中，请稍候")
    if _download_running:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="预构建数据下载正在运行中，请等待完成后再更新")

    def _run():
        global _update_running, _svc
        _update_running = True
        try:
            pipeline = QlibDataPipeline(req.data_dir)
            result = pipeline.update(days_back=req.days_back)
            if "ok" in result:
                _svc = QlibService(req.data_dir)
        finally:
            _update_running = False

    background_tasks.add_task(_run)
    return ok({
        "started": True,
        "days_back": req.days_back,
        "message": f"增量数据更新已在后台启动，更新最近 {req.days_back} 天数据（约 2-4 小时），请查看服务器日志",
    })


@router.get("/update/status")
async def qlib_update_status():
    """Return whether an incremental data update is currently running."""
    return ok({"running": _update_running})


@router.post("/fit")
async def qlib_fit(req: FitRequest):
    """Train LightGBM model on Alpha158 factors.

    Blocks until training completes (typically 1-5 min for full A-share universe).
    """
    global _svc
    _svc = QlibService(req.data_dir)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _svc.fit(
            train_start=req.train_start,
            train_end=req.train_end,
            instruments=req.instruments,
        ),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["error"])
    return ok(result)


@router.post("/select")
async def qlib_select(req: SelectRequest):
    """Score symbols and return top-N ranked by predicted return."""
    svc = _get_svc()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: svc.select(date=req.date, top_n=req.top_n, instruments=req.instruments),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return ok(result)


@router.post("/backtest")
async def qlib_backtest(req: BacktestRequest):
    """Run TopkDropout backtest and return annualized return, Sharpe, max drawdown."""
    svc = _get_svc()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: svc.backtest(
            start=req.start,
            end=req.end,
            top_n=req.top_n,
            instruments=req.instruments,
        ),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return ok(result)


# ---------------------------------------------------------------------------
# AI Factor Lab — R&D-Agent-Quant + Gome style
# ---------------------------------------------------------------------------

@router.post("/discover")
async def qlib_discover(req: DiscoverRequest, background_tasks: BackgroundTasks):
    """Start AI factor discovery (R&D loop).

    Each iteration: DeepSeek proposes factors → we evaluate IC → good ones
    are saved. Results of previous rounds inform the next prompt (Gome momentum).
    Runs in background; poll /discover/status for completion.
    """
    global _discover_running, _discover_result, _factor_agent
    if _discover_running:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="因子发现正在运行中，请稍候")

    def _run():
        global _discover_running, _discover_result, _factor_agent
        _discover_running = True
        try:
            agent = FactorAgent(req.data_dir)
            _factor_agent = agent
            result = agent.run_research_loop(
                n_iter=req.n_iter,
                factors_per_iter=req.factors_per_iter,
                eval_start=req.eval_start,
                eval_end=req.eval_end,
                n_sample=req.n_sample,
            )
            _discover_result = result
        finally:
            _discover_running = False

    background_tasks.add_task(_run)
    total = req.n_iter * req.factors_per_iter
    return ok({
        "started": True,
        "message": f"AI 因子发现已启动: {req.n_iter} 轮 × {req.factors_per_iter} 因子 = {total} 个待评估，每个约 3-5 秒，预计 {total * 4 // 60 + 1} 分钟完成",
    })


@router.get("/discover/status")
async def qlib_discover_status():
    """Return factor discovery progress and results."""
    agent = _factor_agent
    library = agent.get_library() if agent else []
    progress = agent.get_progress() if agent else {}
    return ok({
        "running": _discover_running,
        "factors_found": len(library),
        "library": library,
        "last_result": _discover_result,
        "progress": progress,
    })


@router.get("/factors")
async def qlib_factors():
    """Return current + persisted factor library sorted by IC."""
    from tradingagents.qlib_service.factor_agent import FactorAgent
    # Merge in-memory (current run) + persisted (previous runs)
    agent = _factor_agent
    in_memory = agent.get_library() if agent else []
    persisted = FactorAgent.load_library_from_disk()
    # deduplicate by expr
    seen = set()
    merged = []
    for f in in_memory + persisted:
        if f["expr"] not in seen:
            seen.add(f["expr"])
            merged.append(f)
    merged.sort(key=lambda x: abs(x.get("ic_mean", 0)), reverse=True)
    return ok({"factors": merged, "total": len(merged)})


@router.post("/factors/save")
async def qlib_factors_save():
    """Manually save current in-memory factor library to disk."""
    agent = _factor_agent
    if not agent or not agent.get_library():
        # Try saving what we have from disk (no-op if already saved)
        from tradingagents.qlib_service.factor_agent import FactorAgent
        persisted = FactorAgent.load_library_from_disk()
        return ok({"saved": len(persisted), "message": "已无新因子，磁盘库未变动"})
    agent.save_library()
    return ok({"saved": len(agent.get_library()), "message": "因子库已保存到磁盘，下次训练将自动加载"})


@router.post("/diagnose")
async def qlib_diagnose(req: DiagnoseRequest):
    """Gome-style diagnostic reasoning — full analysis of backtest/selection results.

    Feeds complete results to DeepSeek for structured root-cause analysis and
    3 concrete improvement recommendations.
    """
    agent = _get_factor_agent()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: agent.diagnose(
            backtest_result=req.backtest_result,
            selection_result=req.selection_result,
        ),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return ok(result)


# ---------------------------------------------------------------------------
# Ensemble endpoints (Plan B — qlibAssistant-style multi-model)
# ---------------------------------------------------------------------------

@router.post("/fit/ensemble")
async def qlib_fit_ensemble(req: EnsembleFitRequest, background_tasks: BackgroundTasks):
    """Train ensemble of LightGBM models (3 configs × 3 rolling windows = 9 models).

    Runs in background (5-20 min). Poll /fit/ensemble/status for progress.
    """
    global _fit_ensemble_running, _fit_ensemble_progress, _svc
    if _fit_ensemble_running:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="集成训练正在运行中，请稍候")

    def _run():
        global _fit_ensemble_running, _fit_ensemble_progress, _svc
        _fit_ensemble_running = True
        _fit_ensemble_progress = {
            "current": 0,
            "total": 0,
            "current_label": "",
            "started_at": datetime.now().isoformat(),
            "finished": False,
            "logs": [],
        }

        def _log(msg: str, level: str = "info"):
            ts = datetime.now().strftime("%H:%M:%S")
            entry = {"ts": ts, "level": level, "msg": msg}
            _fit_ensemble_progress["logs"].append(entry)

        try:
            svc = QlibService(req.data_dir)
            _log("QlibService 初始化完成")

            def on_progress(current: int, total: int, label: str, win_start: str, win_end: str, error: str = ""):
                _fit_ensemble_progress.update({
                    "current": current,
                    "total": total,
                    "current_label": f"{label} @ {win_start}~{win_end}",
                })
                if error:
                    _log(f"[{current}/{total}] ✗ {label} ({win_start}~{win_end}) 跳过: {error}", "warn")
                else:
                    _log(f"[{current}/{total}] ✓ {label} ({win_start}~{win_end}) 训练完成")

            result = svc.fit_ensemble(
                train_end=req.train_end,
                instruments=req.instruments,
                progress_callback=on_progress,
                log_fn=_log,
            )
            _fit_ensemble_progress["finished"] = True
            _fit_ensemble_progress["ok"] = "ok" in result
            if "error" in result:
                _fit_ensemble_progress["error"] = result["error"]
                _log(f"训练结束，出现错误: {result['error']}", "error")
            elif "ok" in result:
                _svc = svc
                _log(f"集成训练成功，共 {result.get('models', 0)} 个模型")
        finally:
            _fit_ensemble_running = False

    background_tasks.add_task(_run)
    return ok({
        "started": True,
        "message": "集成训练已在后台启动（约 5-20 分钟），请轮询 /fit/ensemble/status 查看进度",
    })


@router.get("/fit/ensemble/status")
async def qlib_fit_ensemble_status():
    """Return ensemble training progress."""
    return ok({
        "running": _fit_ensemble_running,
        "progress": _fit_ensemble_progress,
    })


_LAST_SELECTION_FILE = Path.home() / ".qlib" / "last_selection.json"


@router.post("/select/ensemble")
async def qlib_select_ensemble(req: EnsembleSelectRequest):
    """Score symbols using ensemble voting with positive_ratio filter."""
    svc = _get_svc()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: svc.select_ensemble(
            date=req.date,
            top_n=req.top_n,
            instruments=req.instruments,
            min_positive_ratio=req.min_positive_ratio,
        ),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    # 自动保存最近一次选股结果
    try:
        _LAST_SELECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_SELECTION_FILE.write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
    except Exception:
        pass
    return ok(result)


@router.get("/select/last")
async def qlib_select_last():
    """Return the most recently saved ensemble selection result."""
    try:
        if _LAST_SELECTION_FILE.exists():
            data = json.loads(_LAST_SELECTION_FILE.read_text())
            return ok(data)
    except Exception:
        pass
    return ok(None)


# ---------------------------------------------------------------------------
# IC/ICIR evaluation
# ---------------------------------------------------------------------------

@router.post("/evaluate/ic")
async def qlib_evaluate_ic(req: ICEvalRequest):
    """Compute IC, ICIR, RankIC, RankICIR for each model in the ensemble.

    Returns per-model metrics sorted by ICIR (best first).
    ICIR > 0.5 is considered a strong signal; < 0.2 is weak.
    """
    svc = _get_svc()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: svc.evaluate_ic(
            start=req.start,
            end=req.end,
            instruments=req.instruments,
        ),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return ok(result)


# ---------------------------------------------------------------------------
# Enhanced backtest (TopkDropout + fees/slippage + full metrics)
# ---------------------------------------------------------------------------

@router.post("/backtest/enhanced")
async def qlib_backtest_enhanced(req: BacktestEnhancedRequest):
    """Run TopkDropout strategy backtest with realistic A-share transaction costs.

    Returns annualized return, Sharpe ratio, max drawdown, daily win rate.
    """
    svc = _get_svc()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: svc.backtest_enhanced(
            start=req.start,
            end=req.end,
            top_n=req.top_n,
            n_drop=req.n_drop,
            instruments=req.instruments,
            open_cost=req.open_cost,
            close_cost=req.close_cost,
            account=req.account,
        ),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return ok(result)


# ---------------------------------------------------------------------------
# EnhancedIndexingStrategy backtest (主动+被动混合)
# ---------------------------------------------------------------------------

@router.post("/backtest/enhanced-indexing")
async def qlib_backtest_enhanced_indexing(req: EnhancedIndexingRequest):
    """Run EnhancedIndexingStrategy backtest (active alpha + passive index tracking)."""
    svc = _get_svc()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: svc.backtest_enhanced_indexing(
            start=req.start,
            end=req.end,
            instruments=req.instruments,
            open_cost=req.open_cost,
            close_cost=req.close_cost,
            account=req.account,
        ),
    )
    if "error" in result:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result["error"])
    return ok(result)


# ---------------------------------------------------------------------------
# Rolling retrain (incremental/online learning)
# ---------------------------------------------------------------------------

_retrain_running = False
_retrain_progress: dict = {}


@router.post("/retrain")
async def qlib_retrain(req: RetrainRequest, background_tasks: BackgroundTasks):
    """Trigger rolling retrain using the most recent N days of data.

    This replaces old model weights with fresh ones trained on recent market conditions.
    """
    global _retrain_running, _retrain_progress
    if _retrain_running:
        return ok({"started": False, "message": "滚动重训练正在进行中，请稍候"})

    svc = _get_svc()
    _retrain_running = True
    _retrain_progress = {"status": "running", "days_back": req.days_back, "started_at": datetime.now().isoformat()}

    def _run():
        global _retrain_running, _retrain_progress
        try:
            def log_fn(msg, level="info"):
                _retrain_progress["last_message"] = msg

            result = svc.retrain_incremental(
                days_back=req.days_back,
                instruments=req.instruments,
                log_fn=log_fn,
            )
            _retrain_progress.update({
                "status": "completed" if "ok" in result else "failed",
                "result": result,
                "finished_at": datetime.now().isoformat(),
            })
        except Exception as exc:
            _retrain_progress.update({"status": "failed", "error": str(exc)})
        finally:
            _retrain_running = False

    background_tasks.add_task(_run)
    return ok({"started": True, "message": f"滚动重训练已启动（最近 {req.days_back} 天数据）"})


@router.get("/retrain/status")
async def qlib_retrain_status():
    """Return rolling retrain progress."""
    return ok({"running": _retrain_running, "progress": _retrain_progress})


# ---------------------------------------------------------------------------
# Alpha360 deep learning training
# ---------------------------------------------------------------------------

_fit_alpha360_running = False
_fit_alpha360_progress: dict = {}


@router.post("/fit/alpha360")
async def qlib_fit_alpha360(req: Alpha360FitRequest, background_tasks: BackgroundTasks):
    """Train GRU/LSTM/ALSTM/TCN/Transformer models using Alpha360 (360 raw features).

    Requires PyTorch. Models are merged into the main ensemble after training.
    """
    global _fit_alpha360_running, _fit_alpha360_progress
    if _fit_alpha360_running:
        return ok({"started": False, "message": "Alpha360 训练正在进行中，请稍候"})

    svc = _get_svc()
    _fit_alpha360_running = True
    _fit_alpha360_progress = {
        "status": "running",
        "train_end": req.train_end,
        "started_at": datetime.now().isoformat(),
        "completed": 0,
        "total": 5,
        "current_model": "",
        "logs": [],
    }

    def _run():
        global _fit_alpha360_running, _fit_alpha360_progress

        def log_fn(msg, level="info"):
            _fit_alpha360_progress["logs"].append({"msg": msg, "level": level})
            if len(_fit_alpha360_progress["logs"]) > 200:
                _fit_alpha360_progress["logs"] = _fit_alpha360_progress["logs"][-200:]

        def progress_cb(done, total, label, win_start, win_end, error=None):
            _fit_alpha360_progress.update({
                "completed": done,
                "total": total,
                "current_model": label,
            })

        try:
            result = svc.fit_ensemble_alpha360(
                train_end=req.train_end,
                instruments=req.instruments,
                progress_callback=progress_cb,
                log_fn=log_fn,
            )
            _fit_alpha360_progress.update({
                "status": "completed" if "ok" in result else "failed",
                "result": result,
                "finished_at": datetime.now().isoformat(),
            })
        except Exception as exc:
            _fit_alpha360_progress.update({"status": "failed", "error": str(exc)})
        finally:
            _fit_alpha360_running = False

    background_tasks.add_task(_run)
    return ok({"started": True, "message": "Alpha360 深度学习训练已启动"})


@router.get("/fit/alpha360/status")
async def qlib_fit_alpha360_status():
    """Return Alpha360 training progress."""
    return ok({"running": _fit_alpha360_running, "progress": _fit_alpha360_progress})


# ---------------------------------------------------------------------------
# Nightly auto-run helpers
# ---------------------------------------------------------------------------

_NIGHTLY_DEFAULTS: dict = {
    "select_enabled": False,
    "select_cron": "0 17 * * 1-5",
    "select_top_n": 20,
    "select_min_positive_ratio": 0.5,
    "discover_enabled": False,
    "discover_cron": "0 20 * * 1-5",
    "discover_n_iter": 2,
    "discover_factors_per_iter": 5,
}


def _load_nightly_config() -> dict:
    cfg = dict(_NIGHTLY_DEFAULTS)
    try:
        if _NIGHTLY_CONFIG_FILE.exists():
            cfg.update(json.loads(_NIGHTLY_CONFIG_FILE.read_text()))
    except Exception:
        pass
    return cfg


def _save_nightly_config(cfg: dict) -> None:
    _NIGHTLY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NIGHTLY_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


def _load_nightly_result() -> Optional[dict]:
    try:
        if _NIGHTLY_RESULT_FILE.exists():
            return json.loads(_NIGHTLY_RESULT_FILE.read_text())
    except Exception:
        pass
    return None


class NightlyConfigRequest(BaseModel):
    select_enabled: Optional[bool] = None
    select_cron: Optional[str] = None
    select_top_n: Optional[int] = Field(None, ge=5, le=200)
    select_min_positive_ratio: Optional[float] = Field(None, ge=0.0, le=1.0)
    discover_enabled: Optional[bool] = None
    discover_cron: Optional[str] = None
    discover_n_iter: Optional[int] = Field(None, ge=1, le=10)
    discover_factors_per_iter: Optional[int] = Field(None, ge=2, le=10)


@router.get("/nightly/config")
async def get_nightly_config():
    """Return nightly auto-run configuration."""
    cfg = _load_nightly_config()
    # Include job status from scheduler if available
    next_select = None
    next_discover = None
    if _nightly_scheduler:
        try:
            job = _nightly_scheduler.get_job("qlib_nightly_select")
            if job and job.next_run_time:
                next_select = job.next_run_time.isoformat()
        except Exception:
            pass
        try:
            job = _nightly_scheduler.get_job("qlib_weekly_discover")
            if job and job.next_run_time:
                next_discover = job.next_run_time.isoformat()
        except Exception:
            pass
    cfg["next_select_run"] = next_select
    cfg["next_discover_run"] = next_discover
    return ok(cfg)


@router.post("/nightly/config")
async def set_nightly_config(req: NightlyConfigRequest):
    """Update nightly auto-run configuration and apply to running scheduler."""
    cfg = _load_nightly_config()
    update = req.model_dump(exclude_none=True)
    cfg.update(update)
    _save_nightly_config(cfg)

    # Apply to running scheduler
    if _nightly_scheduler:
        from apscheduler.triggers.cron import CronTrigger
        _apply_nightly_jobs(_nightly_scheduler, cfg)

    return ok({"saved": True, "config": cfg})


@router.get("/nightly/result")
async def get_nightly_result():
    """Return last nightly auto-selection result."""
    result = _load_nightly_result()
    return ok(result or {"stocks": [], "total": 0, "ran_at": None, "models_used": 0})


@router.post("/nightly/run")
async def trigger_nightly_select(background_tasks: BackgroundTasks):
    """Manually trigger nightly selection right now."""
    cfg = _load_nightly_config()

    def _run():
        svc = _get_svc()
        if not svc._ensemble:
            return
        result = svc.select_ensemble(
            top_n=cfg["select_top_n"],
            min_positive_ratio=cfg["select_min_positive_ratio"],
        )
        if "stocks" in result or "ok" in result:
            result["ran_at"] = datetime.now().isoformat()
            _NIGHTLY_RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _NIGHTLY_RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False))

    background_tasks.add_task(_run)
    return ok({"started": True, "message": "夜间选股已手动触发，稍后刷新结果"})


# ---------------------------------------------------------------------------
# Scheduler integration — called from app/main.py at startup
# ---------------------------------------------------------------------------

def _apply_nightly_jobs(scheduler: Any, cfg: dict) -> None:
    """Register or update nightly jobs based on config dict."""
    from apscheduler.triggers.cron import CronTrigger

    async def nightly_select_task():
        loop = asyncio.get_event_loop()
        svc = _get_svc()
        if not svc._ensemble:
            return
        cfg_now = _load_nightly_config()
        result = await loop.run_in_executor(
            None,
            lambda: svc.select_ensemble(
                top_n=cfg_now["select_top_n"],
                min_positive_ratio=cfg_now["select_min_positive_ratio"],
            ),
        )
        if "stocks" in result or "ok" in result:
            result["ran_at"] = datetime.now().isoformat()
            _NIGHTLY_RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _NIGHTLY_RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False))

    async def weekly_discover_task():
        loop = asyncio.get_event_loop()
        cfg_now = _load_nightly_config()
        agent = FactorAgent()
        global _factor_agent
        _factor_agent = agent
        await loop.run_in_executor(
            None,
            lambda: agent.run_research_loop(
                n_iter=cfg_now["discover_n_iter"],
                factors_per_iter=cfg_now["discover_factors_per_iter"],
            ),
        )

    # Add / replace nightly select job
    existing_select = scheduler.get_job("qlib_nightly_select")
    if existing_select:
        scheduler.remove_job("qlib_nightly_select")
    scheduler.add_job(
        nightly_select_task,
        CronTrigger.from_crontab(cfg["select_cron"]),
        id="qlib_nightly_select",
        name="量化选股夜间自动选股",
    )
    if not cfg.get("select_enabled"):
        scheduler.pause_job("qlib_nightly_select")

    # Add / replace weekly discover job
    existing_discover = scheduler.get_job("qlib_weekly_discover")
    if existing_discover:
        scheduler.remove_job("qlib_weekly_discover")
    scheduler.add_job(
        weekly_discover_task,
        CronTrigger.from_crontab(cfg["discover_cron"]),
        id="qlib_weekly_discover",
        name="量化选股每周自动因子发现",
    )
    if not cfg.get("discover_enabled"):
        scheduler.pause_job("qlib_weekly_discover")


def setup_nightly_jobs(scheduler: Any) -> None:
    """Called from app/main.py before scheduler.start() to register Qlib nightly jobs."""
    global _nightly_scheduler
    _nightly_scheduler = scheduler
    cfg = _load_nightly_config()
    _apply_nightly_jobs(scheduler, cfg)
