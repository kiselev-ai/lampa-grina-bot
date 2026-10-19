"""Фоновые напоминания о бронях и мероприятиях."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot

from .common import human_dt
from .content import POINTS
from .db import db, iso, now, parse

log = logging.getLogger(__name__)


async def _tick(bot: Bot) -> None:
    t = now()
    bookings = await db.all(
        "SELECT * FROM bookings WHERE status='confirmed' AND reminded=0 AND at<=? AND at>=?",
        iso(t + timedelta(hours=2)), iso(t))
    for b in bookings:
        await _send(bot, b["user_id"], f"⏰ Напоминаем: столик в {POINTS[b['point']]['name']} "
                                       f"забронирован на {parse(b['at']):%H:%M}. Ждём вас!")
        await db.run("UPDATE bookings SET reminded=1 WHERE id=?", b["id"])

    for col, delta, label in (("rem24", timedelta(hours=24), "завтра"), ("rem2", timedelta(hours=2), "через 2 часа")):
        regs = await db.all(
            f"SELECT r.id, r.user_id, e.title, e.starts_at, e.point FROM registrations r "
            f"JOIN events e ON e.id=r.event_id WHERE r.status='registered' AND r.{col}=0 "
            f"AND e.starts_at<=? AND e.starts_at>=?", iso(t + delta), iso(t))
        for r in regs:
            if col == "rem24" and parse(r["starts_at"]) - t < timedelta(hours=3):
                await db.run("UPDATE registrations SET rem24=1 WHERE id=?", r["id"])
                continue
            await _send(bot, r["user_id"], f"🎟 Напоминаем: {label} — «{r['title']}»\n"
                                           f"{human_dt(parse(r['starts_at']))}, {POINTS[r['point']]['name']}.\n"
                                           f"Ваш билет: LG-{r['id']:04d}")
            await db.run(f"UPDATE registrations SET {col}=1 WHERE id=?", r["id"])


async def _send(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        log.warning("Напоминание %s не доставлено: %s", chat_id, e)


async def run(bot: Bot) -> None:
    while True:
        try:
            await _tick(bot)
        except Exception:
            log.exception("Ошибка в напоминаниях")
        await asyncio.sleep(60)
