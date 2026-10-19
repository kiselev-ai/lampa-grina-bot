"""Свободный текст вне сценариев: ИИ-консультант или вопрос администратору."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from .. import ai
from ..common import kb, main_menu
from .start import forward_question

router = Router(name="fallback")


@router.message(StateFilter(None), F.text)
async def free_text(message: Message, bot: Bot) -> None:
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ai.answer(message.from_user.id, message.text)
    if reply:
        await message.answer(reply, parse_mode=None, reply_markup=kb([
            [("🏠 Главное меню", "home"), ("✍️ Спросить человека", "ask")]]))
        return
    await forward_question(message, bot)
    await message.answer("Передали ваш вопрос администратору — ответ придёт сюда 👌\n"
                         "А пока можно воспользоваться меню:", reply_markup=main_menu())


MEDIA = F.photo | F.sticker | F.voice | F.video | F.video_note | F.audio | F.document | F.animation | F.location


@router.message(MEDIA)
async def other(message: Message) -> None:
    # Служебные сообщения (например, «бот закрепил сообщение») сюда не попадают
    await message.answer("Я понимаю текст и кнопки 🙂", reply_markup=main_menu())
