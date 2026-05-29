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
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from app.core.response import ok
from tradingagents.qlib_service.data_pipeline import QlibDataPipeline
from tradingagents.qlib_service.service import QlibService

router = APIRouter(prefix="/qlib", tags=["qlib-selection"])

# Module-level singleton — keeps model in memory between requests
_svc: Optional[QlibService] = None
_build_running = False


def _get_svc() -> QlibService:
    global _svc
    if _svc is None:
        _svc = QlibService()
    return _svc


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def qlib_status():
    """Return data directory and model status."""
    return ok(_get_svc().status())


@router.post("/build")
async def qlib_build(req: BuildRequest, background_tasks: BackgroundTasks):
    """Start async data download and Qlib binary file generation.

    Returns immediately; progress visible in server logs.
    """
    global _build_running
    if _build_running:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="数据构建正在运行中")

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
            return result
        finally:
            _build_running = False

    background_tasks.add_task(_run)
    return ok({"started": True, "message": "数据构建已在后台启动，请查看服务器日志"})


@router.post("/build/sync")
async def qlib_build_sync(req: BuildRequest):
    """Synchronous data build (blocks until complete). For small symbol lists."""
    pipeline = QlibDataPipeline(req.data_dir)
    result = pipeline.build(symbols=req.symbols, start=req.start, end=req.end)
    if "error" in result:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["error"])
    return ok(result)


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
