"""
AKShare 数据获取层
优先从 SQLite 缓存读取，不存在时才调用 AKShare 拉取并写入缓存。

修复：实时行情改为单只股票查询，不再下载整个市场数据。
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, List
import database as db
import traceback

# ─── 工具函数 ──────────────────────────────────────────────

def _symbol_akshare(symbol: str, market: str) -> str:
    """将统一 symbol 转换为 AKShare 所需格式"""
    if market == "A":
        return symbol
    elif market == "HK":
        return symbol.zfill(5)
    elif market == "US":
        return symbol.upper()
    return symbol


def _df_to_records(df: pd.DataFrame) -> List[dict]:
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


# ─── 实时行情（单只股票查询，不下载全市场） ────────────────

async def fetch_realtime_quote(symbol: str, market: str) -> Optional[dict]:
    """获取单只股票的实时/最新行情"""
    try:
        if market == "A":
            return await _fetch_a_quote(symbol)
        elif market == "HK":
            return await _fetch_hk_quote(symbol)
        elif market == "US":
            return await _fetch_us_quote(symbol)
    except Exception as e:
        print(f"[data_fetcher] fetch_realtime_quote({symbol},{market}) error: {e}")
        traceback.print_exc()
        return None


async def _fetch_a_quote(symbol: str) -> Optional[dict]:
    """A股单只股票实时行情"""
    try:
        # 方法1: 用 stock_zh_a_hist 拉最近一天的数据（单只股票，很快）
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq"
        )
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            prev_close = float(df.iloc[-2]["收盘"]) if len(df) >= 2 else float(latest["收盘"])
            price = float(latest["收盘"])
            return {
                "symbol": symbol,
                "market": "A",
                "name": symbol,
                "price": price,
                "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "change_amount": round(price - prev_close, 2),
                "open": float(latest.get("开盘", 0)),
                "high": float(latest.get("最高", 0)),
                "low": float(latest.get("最低", 0)),
                "volume": float(latest.get("成交量", 0)),
                "amount": float(latest.get("成交额", 0)),
                "prev_close": prev_close,
            }
    except Exception as e:
        print(f"[data_fetcher] _fetch_a_quote({symbol}) hist error: {e}")

    # 方法2: 尝试用个股实时接口
    try:
        df = ak.stock_individual_info_em(symbol=symbol)
        if df is not None and not df.empty:
            info = {}
            for _, row in df.iterrows():
                info[str(row.iloc[0])] = row.iloc[1]
            price = float(info.get("最新价", 0))
            prev_close = float(info.get("昨收", 0))
            return {
                "symbol": symbol,
                "market": "A",
                "name": info.get("股票简称", symbol),
                "price": price,
                "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "change_amount": round(price - prev_close, 2),
                "open": float(info.get("今开", 0)),
                "high": 0,
                "low": 0,
                "volume": float(info.get("成交量", 0)),
                "amount": float(info.get("成交额", 0)),
                "prev_close": prev_close,
            }
    except Exception as e:
        print(f"[data_fetcher] _fetch_a_quote({symbol}) info error: {e}")

    return None


async def _fetch_hk_quote(symbol: str) -> Optional[dict]:
    """港股单只股票实时行情"""
    ak_symbol = symbol.zfill(5)
    try:
        df = ak.stock_hk_hist(
            symbol=ak_symbol, period="daily",
            start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq"
        )
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            prev_close = float(df.iloc[-2]["收盘"]) if len(df) >= 2 else float(latest["收盘"])
            price = float(latest["收盘"])
            col_map = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}
            return {
                "symbol": symbol,
                "market": "HK",
                "name": ak_symbol,
                "price": price,
                "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "change_amount": round(price - prev_close, 2),
                "open": float(latest.get("开盘", 0)),
                "high": float(latest.get("最高", 0)),
                "low": float(latest.get("最低", 0)),
                "volume": float(latest.get("成交量", 0)),
                "amount": 0,
                "prev_close": prev_close,
            }
    except Exception as e:
        print(f"[data_fetcher] _fetch_hk_quote({symbol}) error: {e}")
    return None


async def _fetch_us_quote(symbol: str) -> Optional[dict]:
    """美股单只股票实时行情"""
    ak_symbol = symbol.upper()
    try:
        df = ak.stock_us_daily(symbol=ak_symbol, adjust="qfq")
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            prev_close = float(df.iloc[-2]["close"]) if len(df) >= 2 else float(latest["close"])
            price = float(latest["close"])
            return {
                "symbol": symbol,
                "market": "US",
                "name": ak_symbol,
                "price": price,
                "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "change_amount": round(price - prev_close, 2),
                "open": float(latest.get("open", 0)),
                "high": float(latest.get("high", 0)),
                "low": float(latest.get("low", 0)),
                "volume": float(latest.get("volume", 0)),
                "amount": 0,
                "prev_close": prev_close,
            }
    except Exception as e:
        print(f"[data_fetcher] _fetch_us_quote({symbol}) error: {e}")
    return None


# ─── 历史行情 ──────────────────────────────────────────────

async def fetch_history(symbol: str, market: str,
                        start: str, end: str) -> List[dict]:
    """获取历史日K数据"""
    start_fmt = start.replace("-", "")
    end_fmt = end.replace("-", "")

    # 先检查缓存
    cached = await db.get_cached_prices(symbol, market)
    if cached:
        filtered = [p for p in cached if start_fmt <= p["date"].replace("-", "") <= end_fmt]
        if filtered:
            return filtered

    # 缓存没有，从 AKShare 拉取
    try:
        if market == "A":
            df = ak.stock_zh_a_hist(
                symbol=symbol, period="daily",
                start_date=start_fmt, end_date=end_fmt, adjust="qfq"
            )
            col_map = {"日期": "date", "开盘": "open", "最高": "high",
                       "最低": "low", "收盘": "close", "成交量": "volume"}
            df = df.rename(columns=col_map)
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        elif market == "HK":
            df = ak.stock_hk_hist(
                symbol=symbol.zfill(5), period="daily",
                start_date=start_fmt, end_date=end_fmt, adjust="qfq"
            )
            col_map = {"日期": "date", "开盘": "open", "最高": "high",
                       "最低": "low", "收盘": "close", "成交量": "volume"}
            df = df.rename(columns=col_map)
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        elif market == "US":
            df = ak.stock_us_daily(symbol=symbol.upper(), adjust="qfq")
            if df is not None and not df.empty:
                col_map = {"date": "date", "open": "open", "high": "high",
                           "low": "low", "close": "close", "volume": "volume"}
                df = df.rename(columns=col_map)
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                start_s = start_fmt[:4]+"-"+start_fmt[4:6]+"-"+start_fmt[6:]
                end_s = end_fmt[:4]+"-"+end_fmt[4:6]+"-"+end_fmt[6:]
                df = df[(df["date"] >= start_s) & (df["date"] <= end_s)]
        else:
            return []

        if df is None or df.empty:
            return []

        cols = ["date", "open", "high", "low", "close", "volume"]
        available_cols = [c for c in cols if c in df.columns]
        df = df[available_cols].copy()

        records = _df_to_records(df)
        await db.cache_prices(symbol, market, records)
        return records

    except Exception as e:
        print(f"[data_fetcher] fetch_history error: {e}")
        traceback.print_exc()
        cached = await db.get_cached_prices(symbol, market)
        return [p for p in cached if start_fmt <= p["date"].replace("-", "") <= end_fmt]


# ─── 单日价格（回测用） ────────────────────────────────────

async def fetch_price_on_date(symbol: str, market: str, date: str) -> Optional[dict]:
    """获取指定日期的价格，优先缓存"""
    date_fmt = date.replace("-", "")

    cached = await db.get_cached_price_on_date(symbol, market, date)
    if cached:
        return cached

    # 拉取前后14天的数据
    start = (datetime.strptime(date_fmt, "%Y%m%d") - timedelta(days=14)).strftime("%Y%m%d")
    end = (datetime.strptime(date_fmt, "%Y%m%d") + timedelta(days=14)).strftime("%Y%m%d")

    prices = await fetch_history(symbol, market, start, end)
    for p in prices:
        if p["date"].replace("-", "") == date_fmt:
            return p
    if prices:
        return prices[-1]
    return None


# ─── 汇率（简化版，避免调用慢接口） ────────────────────────

# 缓存汇率，避免重复请求
_rate_cache = {}

async def fetch_exchange_rate(from_currency: str, to_currency: str,
                               date: str = None) -> float:
    """获取汇率，带内存缓存"""
    if from_currency == to_currency:
        return 1.0

    cache_key = f"{from_currency}_{to_currency}_{date or 'now'}"
    if cache_key in _rate_cache:
        return _rate_cache[cache_key]

    try:
        df = ak.currency_boc_safe()
        if df is not None and not df.empty:
            df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")

            if date:
                row = df[df["日期"] <= date].tail(1)
            else:
                row = df.tail(1)

            if not row.empty:
                if from_currency == "USD":
                    rate = float(row.iloc[0].get("美元/人民币", 7.25))
                elif from_currency == "HKD":
                    rate = float(row.iloc[0].get("港币/人民币", 0.93))
                else:
                    rate = 1.0
                _rate_cache[cache_key] = rate
                return rate

    except Exception as e:
        print(f"[data_fetcher] fetch_exchange_rate error: {e}")

    defaults = {("USD", "CNY"): 7.25, ("HKD", "CNY"): 0.93}
    rate = defaults.get((from_currency, to_currency), 1.0)
    _rate_cache[cache_key] = rate
    return rate


# ─── 交易日历 ──────────────────────────────────────────────

async def get_prev_trading_day(symbol: str, market: str, date: str) -> Optional[str]:
    """获取上一个交易日"""
    dates = await db.get_trading_dates(symbol, market)
    if not dates:
        return None
    date_fmt = date.replace("-", "")
    for d in reversed(dates):
        if d.replace("-", "") < date_fmt:
            return d
    return None


async def get_next_trading_day(symbol: str, market: str, date: str) -> Optional[str]:
    """获取下一个交易日"""
    dates = await db.get_trading_dates(symbol, market)
    if not dates:
        return None
    date_fmt = date.replace("-", "")
    for d in dates:
        if d.replace("-", "") > date_fmt:
            return d
    return None
