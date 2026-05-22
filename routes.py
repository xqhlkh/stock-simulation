"""
FastAPI 路由层
定义所有 API 接口
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import database as db
import data_fetcher as df
import trading

router = APIRouter()


# ─── 请求模型 ──────────────────────────────────────────────

class CreateAccountRequest(BaseModel):
    name: str
    mode: str  # "backtest" | "live"
    init_cash: float

class OpenPositionRequest(BaseModel):
    account_id: int
    symbol: str
    market: str  # "A" | "HK" | "US"
    direction: str  # "long" | "short"
    qty: float
    multiplier: float = 1.0  # 杠杆倍数
    date: Optional[str] = None  # 回测模式需要
    price: Optional[float] = None  # 回测模式需要

class ClosePositionRequest(BaseModel):
    position_id: int
    date: Optional[str] = None
    price: Optional[float] = None


# ─── 账户管理 ──────────────────────────────────────────────

@router.get("/accounts")
async def get_accounts():
    """获取所有账户"""
    accounts = await db.get_accounts()
    return {"accounts": accounts}


@router.post("/accounts")
async def create_account(req: CreateAccountRequest):
    """创建新账户"""
    if req.mode not in ("backtest", "live"):
        raise HTTPException(400, "mode 必须是 backtest 或 live")
    if req.init_cash <= 0:
        raise HTTPException(400, "初始资金必须大于0")
    account = await db.create_account(req.name, req.mode, req.init_cash)
    return {"account": account}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int):
    """删除账户"""
    success = await db.delete_account(account_id)
    if not success:
        raise HTTPException(404, "账户不存在")
    return {"success": True}


# ─── 行情接口 ──────────────────────────────────────────────

@router.get("/quote")
async def get_quote(symbol: str, market: str, date: str = None):
    """
    获取指定日期（回测）或当前（实时）价格
    date: 回测模式下指定日期，实时模式不需要
    """
    if market not in ("A", "HK", "US"):
        raise HTTPException(400, "market 必须是 A, HK, US")

    if date:
        # 回测模式
        price_data = await df.fetch_price_on_date(symbol, market, date)
        if not price_data:
            raise HTTPException(404, f"未找到 {symbol} 在 {date} 的价格数据")
        return {
            "symbol": symbol,
            "market": market,
            "date": date,
            "open": price_data["open"],
            "high": price_data["high"],
            "low": price_data["low"],
            "close": price_data["close"],
            "volume": price_data.get("volume"),
        }
    else:
        # 实时模式
        quote = await df.fetch_realtime_quote(symbol, market)
        if not quote:
            raise HTTPException(404, f"未找到 {symbol} 的实时行情")
        return quote


@router.get("/quote/range")
async def get_quote_range(symbol: str, market: str,
                          start: str, end: str):
    """获取一段时间历史价格"""
    if market not in ("A", "HK", "US"):
        raise HTTPException(400, "market 必须是 A, HK, US")
    prices = await df.fetch_history(symbol, market, start, end)
    return {"symbol": symbol, "market": market, "prices": prices}


# ─── 交易接口 ──────────────────────────────────────────────

@router.post("/trade/open")
async def trade_open(req: OpenPositionRequest):
    """开仓"""
    if req.direction not in ("long", "short"):
        raise HTTPException(400, "direction 必须是 long 或 short")
    if req.market not in ("A", "HK", "US"):
        raise HTTPException(400, "market 必须是 A, HK, US")
    if req.qty <= 0:
        raise HTTPException(400, "数量必须大于0")

    result = await trading.open_position(
        req.account_id, req.symbol, req.market,
        req.direction, req.qty, req.date, req.price, req.multiplier
    )

    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@router.post("/trade/close")
async def trade_close(req: ClosePositionRequest):
    """平仓"""
    result = await trading.close_position(
        req.position_id, req.date, req.price
    )
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


# ─── 持仓与盈亏 ──────────────────────────────────────────

@router.get("/positions/{account_id}")
async def get_positions(account_id: int, date: str = None):
    """获取账户所有持仓及当前浮动盈亏"""
    account = await db.get_account(account_id)
    if not account:
        raise HTTPException(404, "账户不存在")

    summary = await trading.get_account_summary(account_id, date)
    return summary


@router.get("/snapshot/{account_id}")
async def get_snapshot(account_id: int):
    """获取账户每日资产曲线数据"""
    account = await db.get_account(account_id)
    if not account:
        raise HTTPException(404, "账户不存在")

    snapshots = await db.get_snapshots(account_id)
    return {"snapshots": snapshots}


# ─── 回测时间控制 ──────────────────────────────────────────

@router.get("/calendar/prev")
async def calendar_prev(date: str, market: str, symbol: str = "000001"):
    """获取上一个交易日"""
    prev = await df.get_prev_trading_day(symbol, market, date)
    return {"date": prev}


@router.get("/calendar/next")
async def calendar_next(date: str, market: str, symbol: str = "000001"):
    """获取下一个交易日"""
    next_day = await df.get_next_trading_day(symbol, market, date)
    return {"date": next_day}


# ─── 股票搜索/验证 ────────────────────────────────────────

@router.get("/stock/validate")
async def validate_stock(symbol: str, market: str):
    """验证股票代码并返回信息"""
    result = await trading.validate_symbol(symbol, market)
    return result


# ─── 汇率 ─────────────────────────────────────────────────

@router.get("/exchange_rate")
async def get_exchange_rate(from_currency: str, to_currency: str,
                            date: str = None):
    """获取汇率"""
    rate = await df.fetch_exchange_rate(from_currency, to_currency, date)
    return {"from": from_currency, "to": to_currency, "rate": rate}
