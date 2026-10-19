"""Афиша и запись на мероприятия."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..common import (HOME_BTN, ask_contact, choice_list, continuation, edit_or_send, human_dt, kb, main_menu,
                      notify_staff, user_label)
from ..content import AFISHA_URL, POINTS
from ..db import db, iso, now, parse

router = Router(name="events")


class EventSG(StatesGroup):
    child_age = State()


def price_label(e) -> str:
    if not e["price"]:
        return "бесплатно"
    return e["price_note"] or f"{e['price']} ₽"


@router.callback_query(F.data == "events")
async def cb_events(cb: CallbackQuery) -> None:
    rows = await db.all("SELECT * FROM events WHERE active=1 AND starts_at>=? ORDER BY starts_at", iso(now()))
    if not rows:
        await edit_or_send(cb, "Ближайших мероприятий пока нет — следите за анонсами!",
                           kb([[("📍 Полная афиша в VK", AFISHA_URL)], [HOME_BTN]]))
        return
    entries = [(f"{human_dt(parse(e['starts_at']))} — {e['title']} ({price_label(e)})",
                f"{parse(e['starts_at']):%d.%m %H:%M}", f"ev:{e['id']}") for e in rows]
    listing, buttons = choice_list(entries)
    await edit_or_send(cb, f"🎟 <b>Афиша «Лампы Грина»</b>\n\n{listing}\n\nВыберите мероприятие:",
                       kb(buttons + [[("📍 Полная афиша в VK", AFISHA_URL)], [HOME_BTN]]))


@router.callback_query(F.data.regexp(r"^ev:\d+$"))
async def cb_event(cb: CallbackQuery) -> None:
    e = await db.one("SELECT * FROM events WHERE id=?", int(cb.data.split(":")[1]))
    left = e["seats"] - await db.seats_taken(e["id"])
    text = (f"<b>{e['title']}</b>\n" + (f"{e['speaker']}\n" if e["speaker"] else "") +
            f"\n🕐 {human_dt(parse(e['starts_at']))}\n📍 {POINTS[e['point']]['name']}\n"
            f"💳 {price_label(e)}\n🪑 Свободных мест: {max(left, 0)}")
    if left <= 0:
        buttons = [[("Мест нет — в лист ожидания", f"ev:wait:{e['id']}")]]
    else:
        buttons = [[(f"{n} чел.", f"ev:reg:{e['id']}:{n}") for n in range(1, min(left, 4) + 1)]]
    await edit_or_send(cb, text + "\n\nСколько мест забронировать?" if left > 0 else text,
                       kb(buttons + [[("⬅️ Афиша", "events"), HOME_BTN]]))


@router.callback_query(F.data.startswith("ev:wait:"))
async def cb_wait(cb: CallbackQuery, bot: Bot) -> None:
    eid = int(cb.data.split(":")[2])
    e = await db.one("SELECT * FROM events WHERE id=?", eid)
    await db.add_request(cb.from_user.id, "waitlist", {"event_id": eid})
    await notify_staff(bot, f"⏳ Лист ожидания: «{e['title']}» — {await user_label(cb.from_user.id)}")
    await edit_or_send(cb, "Добавили вас в лист ожидания — напишем, если место освободится.", main_menu())


@router.callback_query(F.data.startswith("ev:reg:"))
async def cb_reg(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    _, _, eid, people = cb.data.split(":")
    e = await db.one("SELECT * FROM events WHERE id=?", int(eid))
    await state.update_data(event_id=int(eid), people=int(people))
    if e["kids"]:
        await state.set_state(EventSG.child_age)
        await edit_or_send(cb, "Сколько лет ребёнку (детям)? Клуб рассчитан на 5–8 лет.")
        return
    await cb.answer()
    await ask_contact(cb.message, state, bot, cb.from_user.id, "event")


@router.message(EventSG.child_age, F.text)
async def on_child_age(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.update_data(child_age=message.text[:50])
    await ask_contact(message, state, bot, message.from_user.id, "event")


@continuation("event")
async def ev_pay(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    d = await state.get_data()
    e = await db.one("SELECT * FROM events WHERE id=?", d["event_id"])
    if not e["price"]:
        await ev_register(message, state, bot, user_id, "бесплатно")
        return
    total = e["price"] * d["people"]
    await message.answer(f"К оплате: <b>{total} ₽</b> ({d['people']} × {price_label(e)})", reply_markup=kb([
        [("💳 Оплатить онлайн (демо)", "ev:pay:online")], [("💵 Оплачу на месте", "ev:pay:cash")]]))


@router.callback_query(F.data.startswith("ev:pay:"))
async def cb_ev_pay(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if "event_id" not in await state.get_data():
        await edit_or_send(cb, "Сессия записи устарела, начните заново.", main_menu())
        return
    await cb.answer()
    pay = "онлайн (демо, оплачено)" if cb.data.endswith("online") else "на месте"
    await ev_register(cb.message, state, bot, cb.from_user.id, pay)


async def ev_register(message: Message, state: FSMContext, bot: Bot, user_id: int, pay: str) -> None:
    d = await state.get_data()
    await state.clear()
    e = await db.one("SELECT * FROM events WHERE id=?", d["event_id"])
    if e["seats"] - await db.seats_taken(e["id"]) < d["people"]:
        await message.answer("Увы, места только что закончились 😔", reply_markup=main_menu())
        return
    rid = await db.run(
        "INSERT INTO registrations (event_id, user_id, people, child_age, pay, status, created) "
        "VALUES (?,?,?,?,?,?,?)",
        e["id"], user_id, d["people"], d.get("child_age", ""), pay, "registered", iso(now()))
    when = human_dt(parse(e["starts_at"]))
    await message.answer(
        f"🎟 Вы записаны!\n\n<b>{e['title']}</b>\n🕐 {when}\n📍 {POINTS[e['point']]['name']}\n"
        f"Мест: {d['people']} · оплата: {pay}\n\nВаш билет: <b>LG-{rid:04d}</b> — назовите его на входе.\n"
        "Напомним за сутки и за 2 часа до начала.", reply_markup=main_menu())
    await notify_staff(
        bot, f"🎟 <b>Запись LG-{rid:04d}</b> на «{e['title']}» ({when})\n{await user_label(user_id)}\n"
             f"Мест: {d['people']} · оплата: {pay}" +
             (f"\nВозраст ребёнка: {d['child_age']}" if d.get("child_age") else ""))


@router.callback_query(F.data.startswith("ev:cancel:"))
async def cb_cancel(cb: CallbackQuery, bot: Bot) -> None:
    rid = int(cb.data.split(":")[2])
    r = await db.one("SELECT r.*, e.title FROM registrations r JOIN events e ON e.id=r.event_id "
                     "WHERE r.id=? AND r.user_id=?", rid, cb.from_user.id)
    if not r:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    await db.run("UPDATE registrations SET status='cancelled' WHERE id=?", rid)
    await edit_or_send(cb, f"Запись LG-{rid:04d} отменена.", main_menu())
    await notify_staff(bot, f"🚫 Отмена записи LG-{rid:04d} на «{r['title']}» ({r['people']} мест)")
