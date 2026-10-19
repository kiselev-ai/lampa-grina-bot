"""«Что на фильтре», клубы и книжный квиз."""
from __future__ import annotations

import random

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..common import HOME_BTN, ask_contact, continuation, edit_or_send, kb, main_menu, notify_staff, user_label
from ..content import CLUBS, FILTER, POINTS, QUIZ
from ..db import db

router = Router(name="community")


@router.callback_query(F.data == "filter")
async def cb_filter(cb: CallbackQuery) -> None:
    parts = ["🫘 <b>Что на фильтре</b>\n"]
    for code, coffees in FILTER.items():
        parts.append(f"📍 <b>{POINTS[code]['name']}</b>")
        for name, roaster, notes in coffees:
            parts.append(f"• {name}\n  Обжарщик: {roaster}\n  Во вкусе: {notes}")
        parts.append("")
    u = await db.user(cb.from_user.id)
    sub = u and u["filter_sub"]
    await edit_or_send(cb, "\n".join(parts), kb([
        [("🔕 Отписаться от новинок" if sub else "🔔 Сообщать о новом зерне", "filter:sub")],
        [("☕ Заказать", "order"), HOME_BTN]]))


@router.callback_query(F.data == "filter:sub")
async def cb_filter_sub(cb: CallbackQuery) -> None:
    await db.run("UPDATE users SET filter_sub = 1 - filter_sub WHERE id=?", cb.from_user.id)
    await cb.answer("Готово!")
    await cb_filter(cb)


# --- клубы ---

@router.callback_query(F.data == "clubs")
async def cb_clubs(cb: CallbackQuery) -> None:
    text = "💚 <b>Клубы и сообщество</b>\n\n" + "\n".join(f"{t} — {d}" for _, t, d in CLUBS)
    buttons = [[(f"Вступить: {t}", f"club:{key}")] for key, t, _ in CLUBS]
    await edit_or_send(cb, text, kb([[("🔍 Квиз «Книжный детектив»", "quiz")]] + buttons + [[HOME_BTN]]))


@router.callback_query(F.data.startswith("club:"))
async def cb_club(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.update_data(club=cb.data.split(":")[1])
    await cb.answer()
    await ask_contact(cb.message, state, bot, cb.from_user.id, "club")


@continuation("club")
async def club_finish(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    d = await state.get_data()
    await state.clear()
    title = next(t for k, t, _ in CLUBS if k == d["club"])
    rid = await db.add_request(user_id, "club", {"club": title})
    await message.answer(f"Заявка в {title} отправлена! Куратор клуба напишет вам 💚", reply_markup=main_menu())
    await notify_staff(bot, f"💚 <b>Заявка в клуб #{rid}</b>: {title}\n{await user_label(user_id)}")


# --- квиз «угадай книгу по трём подсказкам» ---

@router.callback_query(F.data == "quiz")
async def cb_quiz(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(quiz_score=0)
    await edit_or_send(cb, "🔍 <b>Пятничный книжный детектив</b>\nУгадайте книгу по трём подсказкам. "
                           "Не гуглим и не звоним другу-филологу 😏", kb([[("Начать", "qz:0")]]))


async def show_round(cb: CallbackQuery, n: int, prefix: str = "") -> None:
    clues, answers = QUIZ[n]
    order = list(range(len(answers)))
    random.Random(n * 7 + cb.from_user.id).shuffle(order)
    letters = "АБВГ"
    listing = "\n".join(f"<b>{letters[k]}</b> — {answers[i]}" for k, i in enumerate(order))
    buttons = [[(letters[k], f"qa:{n}:{i}") for k, i in enumerate(order)]]
    await edit_or_send(cb, f"{prefix}Раунд {n + 1} из {len(QUIZ)}\n\n• {clues}\n\nЧто это за книга?\n\n{listing}",
                       kb(buttons))


@router.callback_query(F.data.regexp(r"^qz:\d+$"))
async def cb_round(cb: CallbackQuery) -> None:
    await show_round(cb, int(cb.data.split(":")[1]))


@router.callback_query(F.data.startswith("qa:"))
async def cb_answer(cb: CallbackQuery, state: FSMContext) -> None:
    _, n, i = cb.data.split(":")
    n, i = int(n), int(i)
    d = await state.get_data()
    score = d.get("quiz_score", 0) + (1 if i == 0 else 0)
    await state.update_data(quiz_score=score)
    verdict = "✅ Верно!" if i == 0 else f"❌ Это {QUIZ[n][1][0]}"
    if n + 1 < len(QUIZ):
        await show_round(cb, n + 1, prefix=f"{verdict}\n\n")
        return
    await db.run("UPDATE users SET quiz_best = MAX(quiz_best, ?) WHERE id=?", score, cb.from_user.id)
    top = await db.all("SELECT name, quiz_best FROM users WHERE quiz_best>0 ORDER BY quiz_best DESC LIMIT 5")
    board = "\n".join(f"{k + 1}. {r['name'] or 'Гость'} — {r['quiz_best']}" for k, r in enumerate(top))
    praise = "\nВсе пять — официально разрешаем собой погордиться! 😁" if score == len(QUIZ) else ""
    await edit_or_send(cb, f"{verdict}\n\nВаш результат: <b>{score} из {len(QUIZ)}</b>{praise}\n\n"
                           f"🏆 Лучшие детективы:\n{board}",
                       kb([[("🔁 Ещё раз", "quiz"), HOME_BTN]]))
