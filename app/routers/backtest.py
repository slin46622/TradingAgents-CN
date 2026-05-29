# -*- coding: utf-8 -*-
"""回测路由 — 提供单标的和组合回测 API。"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import List

from app.core.response import ok

router = APIRouter(prefix="/backtest", tags=["backtest"])


class PortfolioBacktestRequest(BaseModel):
    symbols: List[str]
    eval_window_days: int = 20
    commission_rate: float = 0.0003
    slippage_rate: float = 0.001


class SingleBacktestRequest(BaseModel):
    symbol: str
    eval_window_days: int = 20
    commission_rate: float = 0.0003
    slippage_rate: float = 0.001


@router.post("/run")
async def single_backtest(req: SingleBacktestRequest):
    """单标的回测：复用 portfolio_evaluate，返回该标的的绩效指标。"""
    from tradingagents.backtest.service import BacktestService
    svc = BacktestService()
    result = svc.portfolio_evaluate(
        [req.symbol],
        eval_window_days=req.eval_window_days,
        commission_rate=req.commission_rate,
        slippage_rate=req.slippage_rate,
    )
    # 前端期望 data 是单个标的的指标字典
    symbol_metrics = result.get("results", {}).get(req.symbol)
    return ok(data=symbol_metrics or result)


@router.post("/portfolio")
async def portfolio_backtest(req: PortfolioBacktestRequest):
    """多标的组合回测：返回各标的绩效、等权重组合绩效及相关性矩阵。"""
    from tradingagents.backtest.service import BacktestService
    svc = BacktestService()
    result = svc.portfolio_evaluate(
        req.symbols,
        eval_window_days=req.eval_window_days,
        commission_rate=req.commission_rate,
        slippage_rate=req.slippage_rate,
    )
    return ok(data=result)
