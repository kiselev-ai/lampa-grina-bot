"""Общие клавиатуры, уведомления персонала и сбор контактов."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, Union

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)

from .config import config
from .content import POINTS
from .db import db

log = logging.getLogger(__name__)
router = Router(name="common")

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def kb(rows: Sequence[Sequence[Tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура из списка строк [(текст, callback_data|url)]."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, url=d) if d.startswith("http") else
         InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows])


def chunks(buttons: List[Tuple[str, str]], n: int) -> List[List[Tuple[str, str]]]:
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


def vis_len(s: str) -> int:
    """Длина подписи на экране: эмодзи-модификаторы не занимают места."""
    return sum(1 for c in s if c not in "\ufe0f\u20e3\u200d")


# На телефоне в кнопку на всю ширину помещается ~30 символов, в половину — ~14
FULL_WIDTH = 30


def grid(buttons: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    """Раскладывает кнопки по рядам так, чтобы подписи не обрезались."""
    longest = max((vis_len(t) for t, _ in buttons), default=0)
    cols = 4 if longest <= 6 else 3 if longest <= 10 else 2 if longest <= 14 else 1
    return chunks(buttons, cols)


NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def num(i: int) -> str:
    return NUM[i] if i < len(NUM) else f"{i + 1}."


def choice_list(entries: Sequence[Tuple[str, str, str]]) -> Tuple[str, List[List[Tuple[str, str]]]]:
    """Список вариантов: (полный текст, короткая подпись, callback).

    Если все полные подписи помещаются в кнопку — кнопки с полным текстом.
    Иначе полный текст уходит в сообщение нумерованным списком, а на кнопках —
    номер и короткая подпись, чтобы клиент видел, что выбирает.
    """
    if all(vis_len(full) <= FULL_WIDTH for full, _, _ in entries):
        return "", [[(full, data)] for full, _, data in entries]
    text = "\n".join(f"{num(i)} {full}" for i, (full, _, _) in enumerate(entries))
    buttons = [(f"{num(i)} {short}".strip(), data) for i, (_, short, data) in enumerate(entries)]
    return text, grid(buttons)


HOME_BTN = ("🏠 Меню", "home")


def main_menu() -> InlineKeyboardMarkup:
    return kb([
        [("☕ Заказать к приходу", "order")],
        [("📅 Забронировать столик", "book")],
        [("🎟 Афиша", "events"), ("📚 Книги", "books")],
        [("🫘 На фильтре", "filter"), ("💚 Клубы", "clubs")],
        [("👤 Профиль", "profile"), ("ℹ️ Контакты", "info")],
    ])


def points_kb(prefix: str) -> InlineKeyboardMarkup:
    return kb([[(f"📍 {p['name']}", f"{prefix}:{code}")] for code, p in POINTS.items()] + [[HOME_BTN]])


def human_date(d: Union[date, datetime]) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}"


def human_dt(dt: datetime) -> str:
    return f"{human_date(dt)}, {dt:%H:%M}"


async def edit_or_send(event: Union[Message, CallbackQuery], text: str,
                       markup: Optional[InlineKeyboardMarkup] = None) -> None:
    """Для кнопок редактируем сообщение, для текста — отправляем новое."""
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:  # на кнопку уже ответили (например, всплывающим уведомлением)
            pass
        try:
            await event.message.edit_text(text, reply_markup=markup)
            return
        except Exception:  # сообщение без текста или не изменилось
            await event.message.answer(text, reply_markup=markup)
            return
    await event.answer(text, reply_markup=markup)


async def notify_staff(bot: Bot, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    targets = [config.admin_chat_id] if config.admin_chat_id else await db.admin_ids()
    if not targets:
        log.warning("Нет получателей для уведомления персонала: %s", text[:80])
    for chat_id in targets:
        try:
            await bot.send_message(chat_id, text, reply_markup=markup)
        except Exception as e:
            log.warning("Не удалось уведомить %s: %s", chat_id, e)


async def user_label(user_id: int) -> str:
    u = await db.user(user_id)
    if not u:
        return f"id {user_id}"
    parts = [u["name"] or "без имени"]
    if u["phone"]:
        parts.append(u["phone"])
    if u["username"]:
        parts.append(f"@{u['username']}")
    return ", ".join(parts)


# --- сбор имени и телефона с согласием на обработку ПДн ---

class ContactSG(StatesGroup):
    consent = State()
    name = State()
    phone = State()


Continuation = Callable[[Message, FSMContext, Bot, int], Awaitable[None]]
CONTINUATIONS: Dict[str, Continuation] = {}


def continuation(key: str):
    def deco(fn: Continuation) -> Continuation:
        CONTINUATIONS[key] = fn
        return fn
    return deco


CONSENT_TEXT = (
    "Чтобы оформить заявку, нам понадобятся имя и телефон.\n\n"
    "Нажимая «Согласен», вы даёте согласие на обработку персональных данных "
    "для связи по вашей заявке (152-ФЗ). <i>Демо-текст — финальную формулировку согласовать.</i>"
)


async def ask_contact(message: Message, state: FSMContext, bot: Bot, user_id: int, next_key: str) -> None:
    """Если контакты уже есть — сразу продолжаем, иначе спрашиваем."""
    u = await db.user(user_id)
    await state.update_data(next_key=next_key)
    if u and u["consent"] and u["phone"]:
        await CONTINUATIONS[next_key](message, state, bot, user_id)
        return
    if not (u and u["consent"]):
        await state.set_state(ContactSG.consent)
        await message.answer(CONSENT_TEXT, reply_markup=kb([[("✅ Согласен", "consent:yes")], [("Отмена", "home")]]))
        return
    await _ask_name(message, state)


async def _ask_name(message: Message, state: FSMContext) -> None:
    await state.set_state(ContactSG.name)
    await message.answer("Как к вам обращаться?")


@router.callback_query(ContactSG.consent, F.data == "consent:yes")
async def on_consent(cb: CallbackQuery, state: FSMContext) -> None:
    await db.run("UPDATE users SET consent=1 WHERE id=?", cb.from_user.id)
    await cb.answer()
    await _ask_name(cb.message, state)


@router.message(ContactSG.name, F.text)
async def on_name(message: Message, state: FSMContext) -> None:
    await db.run("UPDATE users SET name=? WHERE id=?", message.text.strip()[:60], message.from_user.id)
    await state.set_state(ContactSG.phone)
    await message.answer(
        "Оставьте телефон — кнопкой ниже или сообщением.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True))


@router.message(ContactSG.phone, F.contact | F.text)
async def on_phone(message: Message, state: FSMContext, bot: Bot) -> None:
    phone = message.contact.phone_number if message.contact else message.text.strip()
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 10:
        await message.answer("Похоже, в номере не хватает цифр. Попробуйте ещё раз.")
        return
    await db.run("UPDATE users SET phone=? WHERE id=?", phone, message.from_user.id)
    await message.answer("Спасибо, сохранили ✅", reply_markup=ReplyKeyboardRemove())
    data = await state.get_data()
    await CONTINUATIONS[data["next_key"]](message, state, bot, message.from_user.id)


def day_buttons(prefix: str, days: int = 7, start: Optional[date] = None) -> List[Tuple[str, str]]:
    start = start or datetime.now(config.tz).date()
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        label = "Сегодня" if i == 0 else "Завтра" if i == 1 else f"{WEEKDAYS[d.weekday()]}, {d:%d.%m}"
        out.append((label, f"{prefix}:{d.isoformat()}"))
    return out
