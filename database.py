"""
数据库层：SQLite 表结构、初始化、CRUD 操作
"""
import aiosqlite
import os
from datetime import datetime
from typing import Optional, List

DB_PATH = os.path.join(os.path.dirname(__file__), "stock_sim.db")

# ─── 初始化 ───────────────────────────────────────────────

async def get_db():
    """获取数据库连接"""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    """创建所有表"""
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('backtest','live')),
            init_cash REAL NOT NULL,
            cash REAL NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL CHECK(market IN ('A','HK','US')),
            direction TEXT NOT NULL CHECK(direction IN ('long','short')),
            qty REAL NOT NULL,
            open_price REAL NOT NULL,
            open_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
            close_price REAL,
            close_date TEXT,
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS price_history (
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (symbol, market, date)
        );

        CREATE TABLE IF NOT EXISTS daily_snapshots (
            account_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            total_assets REAL NOT NULL,
            cash REAL NOT NULL,
            position_value REAL NOT NULL,
            daily_pnl REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (account_id, date),
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        );
    """)
    await db.commit()
    await db.close()


# ─── 账户操作 ─────────────────────────────────────────────

async def create_account(name: str, mode: str, init_cash: float) -> dict:
    db = await get_db()
    now = datetime.now().isoformat()
    cursor = await db.execute(
        "INSERT INTO accounts (name, mode, init_cash, cash, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, mode, init_cash, init_cash, now)
    )
    await db.commit()
    account_id = cursor.lastrowid
    row = await db.execute_fetchall("SELECT * FROM accounts WHERE id = ?", (account_id,))
    await db.close()
    return dict(row[0])


async def get_accounts() -> List[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM accounts ORDER BY id")
    await db.close()
    return [dict(r) for r in rows]


async def get_account(account_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM accounts WHERE id = ?", (account_id,))
    await db.close()
    return dict(rows[0]) if rows else None


async def delete_account(account_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    await db.commit()
    await db.close()
    return cursor.rowcount > 0


async def update_account_cash(account_id: int, cash: float):
    db = await get_db()
    await db.execute("UPDATE accounts SET cash = ? WHERE id = ?", (cash, account_id))
    await db.commit()
    await db.close()


# ─── 持仓操作 ─────────────────────────────────────────────

async def create_position(account_id: int, symbol: str, market: str,
                          direction: str, qty: float, open_price: float,
                          open_date: str) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO positions
           (account_id, symbol, market, direction, qty, open_price, open_date, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open')""",
        (account_id, symbol, market, direction, qty, open_price, open_date)
    )
    await db.commit()
    pos_id = cursor.lastrowid
    rows = await db.execute_fetchall("SELECT * FROM positions WHERE id = ?", (pos_id,))
    await db.close()
    return dict(rows[0])


async def close_position(position_id: int, close_price: float, close_date: str) -> Optional[dict]:
    db = await get_db()
    await db.execute(
        """UPDATE positions SET status='closed', close_price=?, close_date=?
           WHERE id=? AND status='open'""",
        (close_price, close_date, position_id)
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM positions WHERE id = ?", (position_id,))
    await db.close()
    return dict(rows[0]) if rows else None


async def get_open_positions(account_id: int) -> List[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM positions WHERE account_id=? AND status='open' ORDER BY id",
        (account_id,)
    )
    await db.close()
    return [dict(r) for r in rows]


async def get_closed_positions(account_id: int) -> List[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM positions WHERE account_id=? AND status='closed' ORDER BY close_date DESC",
        (account_id,)
    )
    await db.close()
    return [dict(r) for r in rows]


async def get_position(position_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM positions WHERE id = ?", (position_id,))
    await db.close()
    return dict(rows[0]) if rows else None


# ─── 价格缓存 ─────────────────────────────────────────────

async def get_cached_prices(symbol: str, market: str) -> List[dict]:
    """获取已缓存的历史价格"""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM price_history WHERE symbol=? AND market=? ORDER BY date",
        (symbol, market)
    )
    await db.close()
    return [dict(r) for r in rows]


async def cache_prices(symbol: str, market: str, prices: List[dict]):
    """批量写入历史价格（去重）"""
    if not prices:
        return
    db = await get_db()
    await db.executemany(
        """INSERT OR IGNORE INTO price_history
           (symbol, market, date, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(symbol, market, p["date"], p["open"], p["high"], p["low"], p["close"], p.get("volume"))
         for p in prices]
    )
    await db.commit()
    await db.close()


async def get_cached_price_on_date(symbol: str, market: str, date: str) -> Optional[dict]:
    """获取某天的缓存价格"""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM price_history WHERE symbol=? AND market=? AND date=?",
        (symbol, market, date)
    )
    await db.close()
    return dict(rows[0]) if rows else None


async def get_trading_dates(symbol: str, market: str) -> List[str]:
    """获取某股票所有已缓存的交易日（用于回测日历）"""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT DISTINCT date FROM price_history WHERE symbol=? AND market=? ORDER BY date",
        (symbol, market)
    )
    await db.close()
    return [r["date"] for r in rows]


# ─── 每日快照 ─────────────────────────────────────────────

async def save_snapshot(account_id: int, date: str, total_assets: float,
                        cash: float, position_value: float, daily_pnl: float):
    db = await get_db()
    await db.execute(
        """INSERT OR REPLACE INTO daily_snapshots
           (account_id, date, total_assets, cash, position_value, daily_pnl)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (account_id, date, total_assets, cash, position_value, daily_pnl)
    )
    await db.commit()
    await db.close()


async def get_snapshots(account_id: int) -> List[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM daily_snapshots WHERE account_id=? ORDER BY date",
        (account_id,)
    )
    await db.close()
    return [dict(r) for r in rows]
