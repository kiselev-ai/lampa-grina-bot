"""Точка входа: python bot.py"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app import ai, common, reminders
from app.config import config
from app.db import db
from app.handlers import admin, booking, books, community, events, fallback, order, start


class TouchUser(BaseMiddleware):
    """Запоминаем каждого, кто пишет боту (нужно для профиля и рассылок)."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            await db.touch_user(user.id, user.username, user.full_name)
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.bot_token:
        sys.exit("Укажите BOT_TOKEN в файле .env (токен выдаёт @BotFather)")
    await db.connect()
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(TouchUser())
    # Порядок важен: общий сбор контактов первым, свободный текст — последним
    dp.include_routers(common.router, admin.router, start.router, order.router, booking.router,
                       events.router, books.router, community.router, fallback.router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="menu", description="Главное меню"),
    ])
    logging.info("ИИ-консультант: %s", "включён" if ai.enabled() else "выключен (нет ANTHROPIC_API_KEY)")
    # Бот работает через polling: снимаем webhook, если он был установлен другим сервисом
    await bot.delete_webhook(drop_pending_updates=False)
    asyncio.create_task(reminders.run(bot))
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
