"""Бронирование столика: точка → дата → время → гости → повод → контакты → подтверждение админом."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..common import (HOME_BTN, ask_contact, chunks, continuation, day_buttons, edit_or_send, grid, human_dt, kb,
                      main_menu, notify_staff, points_kb, user_label)
from ..content import BOOKING_PURPOSES, POINTS
from ..db import db, iso, now, parse

router = Router(name="booking")


class BookSG(StatesGroup):
    flow = State()
    comment = State()


@router.callback_query(F.data == "book")
async def cb_book(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookSG.flow)
    await edit_or_send(cb, "📅 <b>Бронь столика</b>\n\nВ какой кофейне?", points_kb("b:pt"))


@router.callback_query(BookSG.flow, F.data.startswith("b:pt:"))
async def b_point(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(point=cb.data.split(":")[2])
    await edit_or_send(cb, f"📍 {POINTS[cb.data.split(':')[2]]['name']}\nНа какой день бронируем столик?", kb(grid(day_buttons("b:d")) + [[HOME_BTN]]))


@router.callback_query(BookSG.flow, F.data.startswith("b:d:"))
async def b_day(cb: CallbackQuery, state: FSMContext) -> None:
    d = date.fromisoformat(cb.data.split(":")[2])
    data = await state.get_data()
    p = POINTS[data["point"]]
    t = datetime.combine(d, datetime.strptime(p["open"], "%H:%M").time())
    last = datetime.combine(d, datetime.strptime(p["close"], "%H:%M").time()) - timedelta(hours=1)
    earliest = now() + timedelta(minutes=30)
    slots = []
    while t <= last:
        if t >= earliest:
            slots.append(t)
        t += timedelta(minutes=30)
    if not slots:
        await cb.answer("На этот день свободного времени уже нет", show_alert=True)
        return
    await state.update_data(day=d.isoformat())
    rows = chunks([(f"{s:%H:%M}", f"b:t:{s:%H%M}") for s in slots], 5)
    await edit_or_send(cb, "Во сколько придёте?", kb(rows + [[("⬅️ Другой день", f"b:pt:{data['point']}")]]))


@router.callback_query(BookSG.flow, F.data.startswith("b:t:"))
async def b_time(cb: CallbackQuery, state: FSMContext) -> None:
    hm = cb.data.split(":")[2]
    data = await state.get_data()
    at = datetime.combine(date.fromisoformat(data["day"]), datetime.strptime(hm, "%H%M").time())
    await state.update_data(at=iso(at))
    guests = [(str(n), f"b:g:{n}") for n in range(1, 7)] + [("7+", "b:g:7")]
    await edit_or_send(cb, "Сколько будет гостей?", kb(chunks(guests, 4)))


@router.callback_query(BookSG.flow, F.data.startswith("b:g:"))
async def b_guests(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(guests=int(cb.data.split(":")[2]))
    await edit_or_send(cb, "Какой повод? Подберём подходящий стол.",
                       kb(grid([(p, f"b:p:{i}") for i, p in enumerate(BOOKING_PURPOSES)])))


@router.callback_query(BookSG.flow, F.data.startswith("b:p:"))
async def b_purpose(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(purpose=BOOKING_PURPOSES[int(cb.data.split(":")[2])])
    await state.set_state(BookSG.comment)
    await edit_or_send(cb, "Есть пожелания? Например, «у окна», «нужна розетка», «будет ребёнок».",
                       kb([[("Без пожеланий", "b:nocomment")]]))


@router.callback_query(BookSG.comment, F.data == "b:nocomment")
async def b_nocomment(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await cb.answer()
    await state.update_data(comment="")
    await ask_contact(cb.message, state, bot, cb.from_user.id, "booking")


@router.message(BookSG.comment, F.text)
async def b_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.update_data(comment=message.text[:300])
    await ask_contact(message, state, bot, message.from_user.id, "booking")


@continuation("booking")
async def b_finish(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    d = await state.get_data()
    await state.clear()
    bid = await db.run(
        "INSERT INTO bookings (user_id, point, at, guests, purpose, comment, status, created) "
        "VALUES (?,?,?,?,?,?,?,?)",
        user_id, d["point"], d["at"], d["guests"], d["purpose"], d.get("comment", ""), "new", iso(now()))
    when = human_dt(parse(d["at"]))
    guests = "7 и больше" if d["guests"] >= 7 else d["guests"]
    await message.answer(
        f"Заявка на бронь <b>#{bid}</b> отправлена 🙌\n📍 {POINTS[d['point']]['name']}\n🕐 {when}\n"
        f"👥 Гостей: {guests}\n\nАдминистратор подтвердит бронь — пришлём сообщение.", reply_markup=main_menu())
    await notify_staff(
        bot,
        f"📅 <b>Бронь #{bid}</b>\n{await user_label(user_id)}\n📍 {POINTS[d['point']]['name']}\n🕐 {when}\n"
        f"👥 {guests} · {d['purpose']}" + (f"\n💬 {d['comment']}" if d.get("comment") else ""),
        kb([[("✅ Подтвердить", f"adm:b:confirmed:{bid}"), ("❌ Нет мест", f"adm:b:declined:{bid}")]]))


@router.callback_query(F.data.startswith("bk:cancel:"))
async def b_cancel(cb: CallbackQuery, bot: Bot) -> None:
    bid = int(cb.data.split(":")[2])
    row = await db.one("SELECT * FROM bookings WHERE id=? AND user_id=?", bid, cb.from_user.id)
    if not row:
        await cb.answer("Бронь не найдена", show_alert=True)
        return
    await db.run("UPDATE bookings SET status='cancelled' WHERE id=?", bid)
    await edit_or_send(cb, f"Бронь #{bid} отменена.", main_menu())
    await notify_staff(bot, f"🚫 Гость отменил бронь #{bid} ({human_dt(parse(row['at']))}, "
                            f"{POINTS[row['point']]['name']})")
