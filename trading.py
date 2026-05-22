"""
交易业务逻辑层
处理开仓、平仓、盈亏计算、保证金冻结等核心逻辑
"""
import database as db
import data_fetcher as df
from datetime import datetime

# ─── 市场规则配置 ──────────────────────────────────────────

# 涨跌停比例
LIMIT_PCT = {
    "normal": 0.10,    # 普通股 ±10%
    "star": 0.20,      # 科创板 ±20%
    "chinext": 0.20,   # 创业板 ±20%
}

# 保证金比例（做空时冻结资金）
MARGIN_RATIO = 1.0  # 100%

# 交易时段 (hour, minute)
TRADING_HOURS = {
    "A": [(9, 30, 11, 30), (13, 0, 15, 0)],
    "HK": [(9, 30, 16, 0)],
    "US": [(9, 30, 16, 0)],
}


def _get_limit_pct(symbol: str) -> float:
    """根据股票代码判断涨跌停比例"""
    if symbol.startswith("68") or symbol.startswith("88"):
        return LIMIT_PCT["star"]   # 科创板
    elif symbol.startswith("30"):
        return LIMIT_PCT["chinext"]  # 创业板
    return LIMIT_PCT["normal"]


def _is_trading_hours(market: str, now: datetime = None) -> bool:
    """判断当前是否在交易时段"""
    if now is None:
        now = datetime.now()
    hours = TRADING_HOURS.get(market, [])
    for h1, m1, h2, m2 in hours:
        if (now.hour, now.minute) >= (h1, m1) and (now.hour, now.minute) < (h2, m2):
            return True
    return False


def _check_t1_rule(position: dict, current_date: str) -> bool:
    """
    A股T+1规则：当日买入不可当日卖出
    position['open_date'] vs current_date
    """
    return position["open_date"] != current_date


# ─── 开仓 ──────────────────────────────────────────────────

async def open_position(account_id: int, symbol: str, market: str,
                        direction: str, qty: float, date: str,
                        price: float = None, multiplier: float = 1.0) -> dict:
    """
    开仓
    price: 回测模式下指定价格，实时模式下为 None（取当前价）
    multiplier: 杠杆倍数
    """
    # 1. 检查账户
    account = await db.get_account(account_id)
    if not account:
        return {"success": False, "error": "账户不存在"}

    # 2. 获取价格
    if price is None:
        quote = await df.fetch_realtime_quote(symbol, market)
        if not quote:
            return {"success": False, "error": "无法获取行情数据"}
        price = quote["price"]
        if price <= 0:
            return {"success": False, "error": "无效价格"}

    # 3. 获取汇率（港股/美股需要换算成CNY）
    cny_rate = 1.0
    if market == "HK":
        cny_rate = await df.fetch_exchange_rate("HKD", "CNY", date)
    elif market == "US":
        cny_rate = await df.fetch_exchange_rate("USD", "CNY", date)

    # 4. 计算所需资金（考虑倍数）
    position_value_cny = price * qty * cny_rate * multiplier

    if direction == "long":
        required = position_value_cny
        if account["cash"] < required:
            return {
                "success": False,
                "error": f"资金不足，需要 ¥{required:.2f}，可用 ¥{account['cash']:.2f}"
            }
    else:
        required = position_value_cny * MARGIN_RATIO
        if account["cash"] < required:
            return {
                "success": False,
                "error": f"保证金不足，需要 ¥{required:.2f}，可用 ¥{account['cash']:.2f}"
            }

    # 5. A股涨跌停检查
    if market == "A":
        limit = _get_limit_pct(symbol)
        prev_close = None
        if date and date != datetime.now().strftime("%Y-%m-%d"):
            prev_date = await df.get_prev_trading_day(symbol, market, date)
            if prev_date:
                prev_data = await df.fetch_price_on_date(symbol, market, prev_date)
                if prev_data:
                    prev_close = prev_data["close"]
        else:
            quote = await df.fetch_realtime_quote(symbol, market)
            if quote:
                prev_close = quote.get("prev_close")
        if prev_close and prev_close > 0:
            upper = prev_close * (1 + limit)
            lower = prev_close * (1 - limit)
            if price > upper or price < lower:
                return {
                    "success": False,
                    "error": f"价格超出涨跌停限制 [{lower:.2f}, {upper:.2f}]"
                }

    # 6. 冻结资金并创建持仓
    new_cash = account["cash"] - required
    await db.update_account_cash(account_id, new_cash)

    position = await db.create_position(
        account_id, symbol, market, direction, qty, price, date, multiplier
    )

    # 7. 保存当日快照
    await _save_daily_snapshot(account_id, date)

    return {
        "success": True,
        "position": position,
        "frozen_amount": required,
        "remaining_cash": new_cash
    }


# ─── 平仓 ──────────────────────────────────────────────────

async def close_position(position_id: int, date: str,
                          price: float = None) -> dict:
    """
    平仓
    price: 回测模式下指定价格，实时模式下取当前价
    """
    # 1. 检查持仓
    position = await db.get_position(position_id)
    if not position:
        return {"success": False, "error": "持仓不存在"}
    if position["status"] != "open":
        return {"success": False, "error": "该持仓已平仓"}

    # 2. 确定当前日期（实时模式用今天）
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    # 3. A股T+1检查
    if position["market"] == "A":
        if not _check_t1_rule(position, date):
            return {"success": False, "error": "A股T+1限制：当日买入不可当日卖出"}

    # 4. 获取平仓价格
    if price is None:
        quote = await df.fetch_realtime_quote(position["symbol"], position["market"])
        if not quote:
            return {"success": False, "error": "无法获取行情数据"}
        price = quote["price"]
        if price <= 0:
            return {"success": False, "error": "无效价格"}

    # 5. 获取汇率
    cny_rate = 1.0
    if position["market"] == "HK":
        cny_rate = await df.fetch_exchange_rate("HKD", "CNY", date)
    elif position["market"] == "US":
        cny_rate = await df.fetch_exchange_rate("USD", "CNY", date)

    # 6. 计算盈亏（考虑倍数）
    lev = position.get("multiplier", 1) or 1
    if position["direction"] == "long":
        pnl = (price - position["open_price"]) * position["qty"] * cny_rate * lev
        released = position["open_price"] * position["qty"] * cny_rate * lev
    else:  # short
        pnl = (position["open_price"] - price) * position["qty"] * cny_rate * lev
        released = position["open_price"] * position["qty"] * cny_rate * MARGIN_RATIO

    # 7. 更新持仓状态
    await db.close_position(position_id, price, date)

    # 8. 更新账户资金
    account = await db.get_account(position["account_id"])
    new_cash = account["cash"] + released + pnl
    await db.update_account_cash(position["account_id"], new_cash)

    # 9. 保存当日快照
    await _save_daily_snapshot(position["account_id"], date)

    return {
        "success": True,
        "pnl": pnl,
        "close_price": price,
        "new_cash": new_cash
    }


# ─── 计算持仓盈亏 ──────────────────────────────────────────

async def calculate_position_pnl(position: dict, current_price: float,
                                   date: str = None) -> float:
    """计算单个持仓的浮动盈亏（CNY）"""
    cny_rate = 1.0
    if position["market"] == "HK":
        cny_rate = await df.fetch_exchange_rate("HKD", "CNY", date)
    elif position["market"] == "US":
        cny_rate = await df.fetch_exchange_rate("USD", "CNY", date)

    lev = position.get("multiplier", 1) or 1
    if position["direction"] == "long":
        pnl = (current_price - position["open_price"]) * position["qty"] * cny_rate * lev
    else:
        pnl = (position["open_price"] - current_price) * position["qty"] * cny_rate * lev

    return pnl


async def calculate_position_value(position: dict, current_price: float,
                                     date: str = None) -> float:
    """计算持仓市值（CNY）"""
    cny_rate = 1.0
    if position["market"] == "HK":
        cny_rate = await df.fetch_exchange_rate("HKD", "CNY", date)
    elif position["market"] == "US":
        cny_rate = await df.fetch_exchange_rate("USD", "CNY", date)

    lev = position.get("multiplier", 1) or 1
    return current_price * position["qty"] * cny_rate * lev


# ─── 账户汇总 ──────────────────────────────────────────────

async def get_account_summary(account_id: int, date: str = None) -> dict:
    """获取账户汇总：总资产、持仓市值、总盈亏"""
    account = await db.get_account(account_id)
    if not account:
        return None

    positions = await db.get_open_positions(account_id)
    total_position_value = 0.0
    position_details = []

    for pos in positions:
        # 获取当前价格
        if date:
            price_data = await df.fetch_price_on_date(pos["symbol"], pos["market"], date)
            current_price = price_data["close"] if price_data else pos["open_price"]
        else:
            quote = await df.fetch_realtime_quote(pos["symbol"], pos["market"])
            current_price = quote["price"] if quote else pos["open_price"]

        pnl = await calculate_position_pnl(pos, current_price, date)
        value = await calculate_position_value(pos, current_price, date)
        total_position_value += value

        position_details.append({
            **pos,
            "current_price": current_price,
            "pnl": pnl,
            "pnl_pct": (pnl / (pos["open_price"] * pos["qty"])) * 100 if pos["open_price"] * pos["qty"] > 0 else 0,
            "position_value": value,
        })

    total_assets = account["cash"] + total_position_value
    total_pnl = total_assets - account["init_cash"]

    return {
        "account": account,
        "total_assets": total_assets,
        "cash": account["cash"],
        "position_value": total_position_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": (total_pnl / account["init_cash"]) * 100 if account["init_cash"] > 0 else 0,
        "positions": position_details,
    }


# ─── 每日快照 ──────────────────────────────────────────────

async def _save_daily_snapshot(account_id: int, date: str):
    """保存每日资产快照"""
    summary = await get_account_summary(account_id, date)
    if summary:
        # 计算当日盈亏（与前一日快照比较）
        snapshots = await db.get_snapshots(account_id)
        prev_assets = snapshots[-1]["total_assets"] if snapshots else summary["account"]["init_cash"]
        daily_pnl = summary["total_assets"] - prev_assets

        await db.save_snapshot(
            account_id, date,
            summary["total_assets"],
            summary["cash"],
            summary["position_value"],
            daily_pnl
        )


# ─── 股票代码验证 ──────────────────────────────────────────

async def validate_symbol(symbol: str, market: str) -> dict:
    """验证股票代码并返回基本信息"""
    try:
        quote = await df.fetch_realtime_quote(symbol, market)
        if quote:
            return {"valid": True, "quote": quote}
        return {"valid": False, "error": "未找到该股票"}
    except Exception as e:
        return {"valid": False, "error": str(e)}
