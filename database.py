"""
数据库层：SQLite 连接池、异步CRUD操作、优化性能
"""
import aiosqlite
import os
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
import asyncio

DB_PATH = os.path.join(os.path.dirname(__file__), "stock_sim.db")

# ─── 连接池管理 ───────────────────────────────────────────────

class DatabasePool:
    """SQLite 异步连接池"""
    
    def __init__(self, db_path: str, max_connections: int = 10):
        self.db_path = db_path
        self.max_connections = max_connections
        self._pool: asyncio.Queue[aiosqlite.Connection] = None
        self._initialized = False
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        """初始化连接池"""
        if self._initialized:
            return
        
        async with self._lock:
            if self._initialized:
                return
            
            self._pool = asyncio.Queue(maxsize=self.max_connections)
            
            # 预创建连接
            for _ in range(self.max_connections):
                conn = await aiosqlite.connect(self.db_path)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
                await conn.execute("PRAGMA synchronous=NORMAL")  # 平衡性能和安全
                await conn.execute("PRAGMA temp_store=MEMORY")
                await self._pool.put(conn)
            
            self._initialized = True
    
    @asynccontextmanager
    async def get_connection(self):
        """获取连接（上下文管理器）"""
        if not self._initialized:
            await self.initialize()
        
        conn = await self._pool.get()
        try:
            yield conn
        finally:
            await self._pool.put(conn)
    
    async def close_all(self):
        """关闭所有连接"""
        if not self._initialized:
            return
        
        while not self._pool.empty():
            conn = await self._pool.get()
            await conn.close()
        
        self._initialized = False


# 全局连接池实例
db_pool = DatabasePool(DB_PATH)


@asynccontextmanager
async def get_db():
    """获取数据库连接（向后兼容）"""
    async with db_pool.get_connection() as conn:
        yield conn


# ─── 初始化 ───────────────────────────────────────────────

async def init_db():
    """创建所有表并初始化连接池"""
    await db_pool.initialize()
    
    async with db_pool.get_connection() as db:
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
                multiplier REAL NOT NULL DEFAULT 1,
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

        # 为常用查询创建索引以提升性能
        await db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_positions_account_status 
            ON positions(account_id, status);
            
            CREATE INDEX IF NOT EXISTS idx_price_history_symbol_market 
            ON price_history(symbol, market, date);
            
            CREATE INDEX IF NOT EXISTS idx_snapshots_account 
            ON daily_snapshots(account_id, date);
        """)

        # 兼容旧数据库：添加 multiplier 列
        try:
            await db.execute("ALTER TABLE positions ADD COLUMN multiplier REAL NOT NULL DEFAULT 1")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # WAL checkpoint 以减少磁盘空间
        await db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        await db.commit()


# ─── 账户操作 ─────────────────────────────────────────────

async def create_account(name: str, mode: str, init_cash: float) -> dict:
    async with db_pool.get_connection() as db:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "INSERT INTO accounts (name, mode, init_cash, cash, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, mode, init_cash, init_cash, now)
        )
        await db.commit()
        account_id = cursor.lastrowid
        row = await db.execute_fetchall("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return dict(row[0])


async def get_accounts() -> List[dict]:
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall("SELECT * FROM accounts ORDER BY id")
        return [dict(r) for r in rows]


async def get_account(account_id: int) -> Optional[dict]:
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return dict(rows[0]) if rows else None


async def delete_account(account_id: int) -> bool:
    async with db_pool.get_connection() as db:
        cursor = await db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        await db.commit()
        return cursor.rowcount > 0


async def update_account_cash(account_id: int, cash: float):
    async with db_pool.get_connection() as db:
        await db.execute("UPDATE accounts SET cash = ? WHERE id = ?", (cash, account_id))
        await db.commit()


# ─── 持仓操作 ─────────────────────────────────────────────

async def create_position(account_id: int, symbol: str, market: str,
                          direction: str, qty: float, open_price: float,
                          open_date: str, multiplier: float = 1.0) -> dict:
    async with db_pool.get_connection() as db:
        cursor = await db.execute(
            """INSERT INTO positions
               (account_id, symbol, market, direction, qty, multiplier, open_price, open_date, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (account_id, symbol, market, direction, qty, multiplier, open_price, open_date)
        )
        await db.commit()
        pos_id = cursor.lastrowid
        rows = await db.execute_fetchall("SELECT * FROM positions WHERE id = ?", (pos_id,))
        return dict(rows[0])


async def close_position(position_id: int, close_price: float, close_date: str) -> Optional[dict]:
    async with db_pool.get_connection() as db:
        await db.execute(
            """UPDATE positions SET status='closed', close_price=?, close_date=?
               WHERE id=? AND status='open'""",
            (close_price, close_date, position_id)
        )
        await db.commit()
        rows = await db.execute_fetchall("SELECT * FROM positions WHERE id = ?", (position_id,))
        return dict(rows[0]) if rows else None


async def get_open_positions(account_id: int) -> List[dict]:
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM positions WHERE account_id=? AND status='open' ORDER BY id",
            (account_id,)
        )
        return [dict(r) for r in rows]


async def get_closed_positions(account_id: int) -> List[dict]:
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM positions WHERE account_id=? AND status='closed' ORDER BY close_date DESC",
            (account_id,)
        )
        return [dict(r) for r in rows]


async def get_position(position_id: int) -> Optional[dict]:
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall("SELECT * FROM positions WHERE id = ?", (position_id,))
        return dict(rows[0]) if rows else None


# ─── 价格缓存 ─────────────────────────────────────────────

async def get_cached_prices(symbol: str, market: str) -> List[dict]:
    """获取已缓存的历史价格"""
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM price_history WHERE symbol=? AND market=? ORDER BY date",
            (symbol, market)
        )
        return [dict(r) for r in rows]


async def cache_prices(symbol: str, market: str, prices: List[dict]):
    """批量写入历史价格（去重）"""
    if not prices:
        return
    async with db_pool.get_connection() as db:
        await db.executemany(
            """INSERT OR IGNORE INTO price_history
               (symbol, market, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(symbol, market, p["date"], p["open"], p["high"], p["low"], p["close"], p.get("volume"))
             for p in prices]
        )
        await db.commit()


async def get_cached_price_on_date(symbol: str, market: str, date: str) -> Optional[dict]:
    """获取某天的缓存价格"""
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM price_history WHERE symbol=? AND market=? AND date=?",
            (symbol, market, date)
        )
        return dict(rows[0]) if rows else None


async def get_trading_dates(symbol: str, market: str) -> List[str]:
    """获取某股票所有已缓存的交易日（用于回测日历）"""
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall(
            "SELECT DISTINCT date FROM price_history WHERE symbol=? AND market=? ORDER BY date",
            (symbol, market)
        )
        return [r["date"] for r in rows]


# ─── 每日快照 ─────────────────────────────────────────────

async def save_snapshot(account_id: int, date: str, total_assets: float,
                        cash: float, position_value: float, daily_pnl: float):
    async with db_pool.get_connection() as db:
        await db.execute(
            """INSERT OR REPLACE INTO daily_snapshots
               (account_id, date, total_assets, cash, position_value, daily_pnl)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, date, total_assets, cash, position_value, daily_pnl)
        )
        await db.commit()


async def get_snapshots(account_id: int) -> List[dict]:
    async with db_pool.get_connection() as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM daily_snapshots WHERE account_id=? ORDER BY date",
            (account_id,)
        )
        return [dict(r) for r in rows]

