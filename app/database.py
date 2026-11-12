import json
import time
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite
from app.config import DB_PATH

async def get_db_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    return conn

async def init_db() -> None:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                source TEXT NOT NULL,
                price_usd REAL NOT NULL,
                price_inr REAL NOT NULL,
                volume_24h REAL DEFAULT 0,
                high_24h REAL DEFAULT 0,
                low_24h REAL DEFAULT 0,
                change_24h_pct REAL DEFAULT 0
            );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ticks_ts ON price_ticks(timestamp);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ticks_src_ts ON price_ticks(source, timestamp);")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                alert_type TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                current_price_usd REAL NOT NULL,
                current_price_inr REAL NOT NULL,
                diff_30m_pct REAL NOT NULL,
                channels_sent TEXT NOT NULL,
                status TEXT NOT NULL
            );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts_log(timestamp);")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        await db.commit()

async def record_price_tick(
    source: str,
    price_usd: float,
    price_inr: float,
    volume_24h: float = 0.0,
    high_24h: float = 0.0,
    low_24h: float = 0.0,
    change_24h_pct: float = 0.0,
    timestamp: Optional[float] = None
) -> int:
    ts = timestamp if timestamp is not None else time.time()
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute("""
            INSERT INTO price_ticks 
            (timestamp, source, price_usd, price_inr, volume_24h, high_24h, low_24h, change_24h_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts, source, price_usd, price_inr, volume_24h, high_24h, low_24h, change_24h_pct))
        await db.commit()
        return cursor.lastrowid or 0

async def get_latest_tick(source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        if source:
            cursor = await db.execute("""
                SELECT * FROM price_ticks 
                WHERE source = ? 
                ORDER BY timestamp DESC LIMIT 1
            """, (source,))
        else:
            cursor = await db.execute("""
                SELECT * FROM price_ticks 
                ORDER BY timestamp DESC LIMIT 1
            """)
        row = await cursor.fetchone()
        return dict(row) if row else None

async def get_price_history(
    source: Optional[str] = None,
    limit: int = 120,
    since_timestamp: Optional[float] = None
) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM price_ticks WHERE 1=1"
        params: List[Any] = []
        if source:
            query += " AND source = ?"
            params.append(source)
        if since_timestamp is not None:
            query += " AND timestamp >= ?"
            params.append(since_timestamp)
        query += " ORDER BY timestamp ASC"
        if limit:
            query += f" LIMIT {int(limit)}"
        
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

async def get_price_at_or_before(
    target_timestamp: float,
    source: Optional[str] = None,
    max_lookback_seconds: float = 3600.0
) -> Optional[Dict[str, Any]]:
    """
    Find the closest price snapshot recorded at or before target_timestamp.
    """
    earliest = target_timestamp - max_lookback_seconds
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        if source:
            cursor = await db.execute("""
                SELECT * FROM price_ticks 
                WHERE source = ? AND timestamp <= ? AND timestamp >= ?
                ORDER BY timestamp DESC LIMIT 1
            """, (source, target_timestamp, earliest))
        else:
            cursor = await db.execute("""
                SELECT * FROM price_ticks 
                WHERE timestamp <= ? AND timestamp >= ?
                ORDER BY timestamp DESC LIMIT 1
            """, (target_timestamp, earliest))
        row = await cursor.fetchone()
        return dict(row) if row else None

async def record_alert(
    alert_type: str,
    title: str,
    message: str,
    current_price_usd: float,
    current_price_inr: float,
    diff_30m_pct: float,
    channels_sent: List[str],
    status: str = "success"
) -> int:
    ts = time.time()
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute("""
            INSERT INTO alerts_log
            (timestamp, alert_type, title, message, current_price_usd, current_price_inr, diff_30m_pct, channels_sent, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts, alert_type, title, message, current_price_usd, current_price_inr, diff_30m_pct, json.dumps(channels_sent), status))
        await db.commit()
        return cursor.lastrowid or 0

async def get_recent_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM alerts_log 
            ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["channels_sent"] = json.loads(d["channels_sent"])
            except Exception:
                pass
            result.append(d)
        return result

async def get_last_alert_time(alert_type: Optional[str] = None) -> float:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        if alert_type:
            cursor = await db.execute("SELECT MAX(timestamp) FROM alerts_log WHERE alert_type = ?", (alert_type,))
        else:
            cursor = await db.execute("SELECT MAX(timestamp) FROM alerts_log")
        row = await cursor.fetchone()
        return float(row[0]) if (row and row[0] is not None) else 0.0

async def set_dynamic_setting(key: str, value: Any) -> None:
    val_str = json.dumps(value) if not isinstance(value, str) else value
    now = time.time()
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            INSERT INTO dynamic_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, val_str, now))
        await db.commit()

async def get_dynamic_setting(key: str, default: Any = None) -> Any:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        cursor = await db.execute("SELECT value FROM dynamic_settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]
