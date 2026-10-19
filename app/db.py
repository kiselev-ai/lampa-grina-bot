"""SQLite-хранилище: пользователи, заказы, брони, записи, заявки, стоп-лист."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

from .config import config
from .content import DEFAULT_SEATS, SEED_EVENTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT, phone TEXT, username TEXT,
    consent INTEGER DEFAULT 0, points INTEGER DEFAULT 0,
    quiz_best INTEGER DEFAULT 0, filter_sub INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, point TEXT, items TEXT,
    total INTEGER, pickup_at TEXT, pay TEXT, comment TEXT, status TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, point TEXT, at TEXT,
    guests INTEGER, purpose TEXT, comment TEXT, status TEXT, reminded INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, speaker TEXT, starts_at TEXT, point TEXT,
    price INTEGER, price_note TEXT, seats INTEGER, kids INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, user_id INTEGER, people INTEGER,
    child_age TEXT, pay TEXT, status TEXT, rem24 INTEGER DEFAULT 0, rem2 INTEGER DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT, payload TEXT,
    status TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS library (
    user_id INTEGER PRIMARY KEY, card TEXT, active_until TEXT, books_read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS stoplist (point TEXT, item_idx INTEGER, PRIMARY KEY (point, item_idx));
"""


def now() -> datetime:
    return datetime.now(config.tz).replace(tzinfo=None, microsecond=0)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


class DB:
    def __init__(self, path):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        # Миграции для баз, созданных предыдущими версиями
        for table, column in (("orders", "items_text TEXT"), ("orders", "card_msg_id INTEGER")):
            try:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            except aiosqlite.OperationalError:
                pass  # колонка уже есть
        cur = await self.conn.execute("SELECT COUNT(*) FROM events")
        if (await cur.fetchone())[0] == 0:
            for e in SEED_EVENTS:
                await self.conn.execute(
                    "INSERT INTO events (title, speaker, starts_at, point, price, price_note, seats, kids) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (e["title"], e["speaker"], e["starts_at"], e["point"], e["price"],
                     e.get("price_note", ""), DEFAULT_SEATS, e["kids"]))
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def all(self, sql: str, *args) -> List[aiosqlite.Row]:
        cur = await self.conn.execute(sql, args)
        return list(await cur.fetchall())

    async def one(self, sql: str, *args) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(sql, args)
        return await cur.fetchone()

    async def run(self, sql: str, *args) -> int:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # --- пользователи ---
    async def touch_user(self, user_id: int, username: Optional[str], full_name: str) -> None:
        await self.run(
            "INSERT INTO users (id, username, name, created) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username",
            user_id, username, full_name, iso(now()))

    async def user(self, user_id: int) -> Optional[aiosqlite.Row]:
        return await self.one("SELECT * FROM users WHERE id=?", user_id)

    async def is_admin(self, user_id: int) -> bool:
        if user_id in config.admin_ids:
            return True
        return await self.one("SELECT 1 FROM admins WHERE user_id=?", user_id) is not None

    async def admin_ids(self) -> List[int]:
        rows = await self.all("SELECT user_id FROM admins")
        return sorted(set(config.admin_ids) | {r["user_id"] for r in rows})

    async def add_points(self, user_id: int, pts: int) -> None:
        await self.run("UPDATE users SET points = points + ? WHERE id=?", pts, user_id)

    # --- стоп-лист ---
    async def stopped(self, point: str) -> set:
        rows = await self.all("SELECT item_idx FROM stoplist WHERE point=?", point)
        return {r["item_idx"] for r in rows}

    async def toggle_stop(self, point: str, idx: int) -> bool:
        if await self.one("SELECT 1 FROM stoplist WHERE point=? AND item_idx=?", point, idx):
            await self.run("DELETE FROM stoplist WHERE point=? AND item_idx=?", point, idx)
            return False
        await self.run("INSERT INTO stoplist VALUES (?,?)", point, idx)
        return True

    # --- заявки ---
    async def add_request(self, user_id: int, kind: str, payload: Dict[str, Any]) -> int:
        return await self.run(
            "INSERT INTO requests (user_id, kind, payload, status, created) VALUES (?,?,?,?,?)",
            user_id, kind, json.dumps(payload, ensure_ascii=False), "new", iso(now()))

    # --- мероприятия ---
    async def seats_taken(self, event_id: int) -> int:
        row = await self.one(
            "SELECT COALESCE(SUM(people),0) FROM registrations WHERE event_id=? AND status!='cancelled'",
            event_id)
        return row[0]


db = DB(config.db_path)
