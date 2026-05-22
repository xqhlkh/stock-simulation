"""
AKShare 数据获取层
优先从 SQLite 缓存读取，不存在时才调用 AKShare 拉取并写入缓存。
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
import database as db
import traceback

# ─── 工具函数 ──────────────────────────────────────────────

def _symbol_akshare(symbol: str, market: str) -> str:
    """将统一 symbol 转换为 AKShare 所需格式"""
    if market == "A":
        # A股：000001 -> 000001 (sz), 600000 -> 600000 (sh)
        if symbol.startswith("6") or symbol.startswith("9"):
            return f"{symbol}.sh"
        else:
            return f"{symbol}.sz"
    elif market == "HK":
        # 港股：00700 -> 00700
        return symbol.zfill(5)
    elif market == "US":
        # 美股：AAPL -> AAPL
        return symbol.upper()
    return symbol


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame 转换为字典列表"""
    if df is None or df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        record = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                record[col.lower()] = None
            else:
                record[col.lower()] = val
        records.append(record)
    return records


# ─── 历史行情 ──────────────────────────────────────────────

async def fetch_history(symbol: str, market: str,
                        start: str, end: str) -> list[dict]:
    """
    获取历史日K数据
    start/end 格式: "20240101" 或 "2024-01-01"
    """
    # 统一日期格式
    start_fmt = start.replace("-", "")
    end_fmt = end.replace("-", "")

    # 先检查缓存
    cached = await db.get_cached_prices(symbol, market)
    if cached:
        # 筛选日期范围内的数据
        filtered = [p for p in cached if start_fmt <= p["date"].replace("-", "") <= end_fmt]
        if filtered:
            return filtered

    # 缓存没有，从 AKShare 拉取
    try:
        ak_symbol = _symbol_akshare(symbol, market)
        if market == "A":
            df = ak.stock_zh_a_hist(
                symbol=ak_symbol.split(".")[0],
                period="daily",
                start_date=start_fmt,
                end_date=end_fmt,
                adjust="qfq"  # 前复权
            )
            # 重命名列
            col_map = {"日期": "date", "开盘": "open", "最高": "high",
                       "最低": "low", "收盘": "close", "成交量": "volume"}
            df = df.rename(columns=col_map)
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        elif market == "HK":
            df = ak.stock_hk_hist(
                symbol=ak_symbol,
                period="daily",
                start_date=start_fmt,
                end_date=end_fmt,
                adjust="qfq"
            )
            col_map = {"日期": "date", "开盘": "open", "最高": "high",
                       "最低": "low", "收盘": "close", "成交量": "volume"}
            df = df.rename(columns=col_map)
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        elif market == "US":
            df = ak.stock_us_daily(
                symbol=ak_symbol,
                adjust="qfq"
            )
            if df is not None and not df.empty:
                # 确保列名统一
                col_map = {"date": "date", "open": "open", "high": "high",
                           "low": "low", "close": "close", "volume": "volume"}
                df = df.rename(columns=col_map)
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                # 筛选日期范围
                df = df[(df["date"] >= start_fmt[:4]+"-"+start_fmt[4:6]+"-"+start_fmt[6:])
                       & (df["date"] <= end_fmt[:4]+"-"+end_fmt[4:6]+"-"+end_fmt[6:])]
        else:
            return []

        if df is None or df.empty:
            return []

        # 选取需要的列
        cols = ["date", "open", "high", "low", "close", "volume"]
        available_cols = [c for c in cols if c in df.columns]
        df = df[available_cols].copy()

        records = _df_to_records(df)

        # 写入缓存
        await db.cache_prices(symbol, market, records)

        return records

    except Exception as e:
        print(f"[data_fetcher] fetch_history error: {e}")
        traceback.print_exc()
        # 如果 API 失败，返回已缓存的数据（可能不完整）
        cached = await db.get_cached_prices(symbol, market)
        return [p for p in cached if start_fmt <= p["date"].replace("-", "") <= end_fmt]


# ─── 实时行情 ──────────────────────────────────────────────

async def fetch_realtime_quote(symbol: str, market: str) -> Optional[dict]:
    """获取实时/最新行情"""
    try:
        ak_symbol = _symbol_akshare(symbol, market)

        if market == "A":
            df = ak.stock_zh_a_spot_em()
            # 按代码筛选
            row = df[df["代码"] == symbol]
            if row.empty:
                return None
            row = row.iloc[0]
            return {
                "symbol": symbol,
                "market": market,
                "name": str(row.get("名称", "")),
                "price": float(row.get("最新价", 0)),
                "change_pct": float(row.get("涨跌幅", 0)),
                "change_amount": float(row.get("涨跌额", 0)),
                "open": float(row.get("今开", 0)),
                "high": float(row.get("最高", 0)),
                "low": float(row.get("最低", 0)),
                "volume": float(row.get("成交量", 0)),
                "amount": float(row.get("成交额", 0)),
                "prev_close": float(row.get("昨收", 0)),
            }

        elif market == "HK":
            df = ak.stock_hk_spot_em()
            row = df[df["代码"] == ak_symbol]
            if row.empty:
                return None
            row = row.iloc[0]
            return {
                "symbol": symbol,
                "market": market,
                "name": str(row.get("名称", "")),
                "price": float(row.get("最新价", 0)),
                "change_pct": float(row.get("涨跌幅", 0)),
                "change_amount": float(row.get("涨跌额", 0)),
                "open": float(row.get("今开", 0)),
                "high": float(row.get("最高", 0)),
                "low": float(row.get("最低", 0)),
                "volume": float(row.get("成交量", 0)),
                "amount": float(row.get("成交额", 0)),
                "prev_close": float(row.get("昨收", 0)),
            }

        elif market == "US":
            df = ak.stock_us_spot_em()
            row = df[df["代码"] == ak_symbol]
            if row.empty:
                # 也尝试不带后缀匹配
                row = df[df["代码"].str.startswith(ak_symbol)]
            if row.empty:
                return None
            row = row.iloc[0]
            return {
                "symbol": symbol,
                "market": market,
                "name": str(row.get("名称", "")),
                "price": float(row.get("最新价", 0)),
                "change_pct": float(row.get("涨跌幅", 0)),
                "change_amount": float(row.get("涨跌额", 0)),
                "open": float(row.get("今开", 0)),
                "high": float(row.get("最高", 0)),
                "low": float(row.get("最低", 0)),
                "volume": float(row.get("成交量", 0)),
                "amount": float(row.get("成交额", 0)),
                "prev_close": float(row.get("昨收", 0)),
            }

    except Exception as e:
        print(f"[data_fetcher] fetch_realtime_quote error: {e}")
        traceback.print_exc()
        return None


# ─── 单日价格（回测用） ────────────────────────────────────

async def fetch_price_on_date(symbol: str, market: str, date: str) -> Optional[dict]:
    """获取指定日期的价格，优先缓存"""
    date_fmt = date.replace("-", "")

    # 先查缓存
    cached = await db.get_cached_price_on_date(symbol, market, date)
    if cached:
        return cached

    # 缓存没有，拉取最近一段时间的数据
    start = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")
    end = (datetime.strptime(date_fmt, "%Y%m%d") + timedelta(days=7)).strftime("%Y%m%d")

    prices = await fetch_history(symbol, market, start, end)
    # 查找最接近目标日期的价格
    for p in prices:
        if p["date"].replace("-", "") == date_fmt:
            return p
    # 如果没有精确匹配，返回最接近的
    if prices:
        return prices[-1]
    return None


# ─── 汇率 ─────────────────────────────────────────────────

async def fetch_exchange_rate(from_currency: str, to_currency: str,
                               date: str = None) -> float:
    """
    获取汇率
    from_currency/to_currency: 如 "USD"/"CNY", "HKD"/"CNY"
    date: 指定日期（回测用），None 为实时
    """
    if from_currency == to_currency:
        return 1.0

    try:
        if from_currency == "USD" and to_currency == "CNY":
            if date:
                # 历史汇率
                df = ak.currency_boc_safe()
                if df is not None and not df.empty:
                    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
                    row = df[df["日期"] <= date].tail(1)
                    if not row.empty:
                        return float(row.iloc[0].get("美元/人民币", 7.0))
            # 实时汇率
            df = ak.currency_boc_safe()
            if df is not None and not df.empty:
                row = df.tail(1)
                if not row.empty:
                    return float(row.iloc[0].get("美元/人民币", 7.0))

        elif from_currency == "HKD" and to_currency == "CNY":
            if date:
                df = ak.currency_boc_safe()
                if df is not None and not df.empty:
                    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
                    row = df[df["日期"] <= date].tail(1)
                    if not row.empty:
                        return float(row.iloc[0].get("港币/人民币", 0.92))
            df = ak.currency_boc_safe()
            if df is not None and not df.empty:
                row = df.tail(1)
                if not row.empty:
                    return float(row.iloc[0].get("港币/人民币", 0.92))

    except Exception as e:
        print(f"[data_fetcher] fetch_exchange_rate error: {e}")
        traceback.print_exc()

    # 返回默认值
    defaults = {("USD", "CNY"): 7.25, ("HKD", "CNY"): 0.93}
    return defaults.get((from_currency, to_currency), 1.0)


# ─── 交易日历 ──────────────────────────────────────────────

async def get_prev_trading_day(symbol: str, market: str, date: str) -> str | None:
    """获取上一个交易日"""
    dates = await db.get_trading_dates(symbol, market)
    if not dates:
        return None
    date_fmt = date.replace("-", "")
    for d in reversed(dates):
        if d.replace("-", "") < date_fmt:
            return d
    return None


async def get_next_trading_day(symbol: str, market: str, date: str) -> str | None:
    """获取下一个交易日"""
    dates = await db.get_trading_dates(symbol, market)
    if not dates:
        return None
    date_fmt = date.replace("-", "")
    for d in dates:
        if d.replace("-", "") > date_fmt:
            return d
    return None
