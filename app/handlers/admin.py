"""Панель администратора: статусы заявок, стоп-лист, мероприятия, ответы гостям, рассылки."""
from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..common import edit_or_send, grid, human_dt, kb, main_menu, num, user_label
from ..config import config
from ..content import CHALLENGE_GOAL, DEFAULT_SEATS, LIBRARY_DAYS, LOYALTY_EVENT_BONUS, LOYALTY_PERCENT, POINTS
from ..db import db, iso, now, parse
from ..menu import items_in, sections
from ..order_card import refresh_card

router = Router(name="admin")


async def guard(event) -> bool:
    if await db.is_admin(event.from_user.id):
        return True
    if isinstance(event, CallbackQuery):
        await event.answer("Только для администраторов", show_alert=True)
    return False


@router.message(Command("demo_admin"))
async def cmd_demo_admin(message: Message) -> None:
    if not config.demo_mode:
        return
    await db.run("INSERT OR IGNORE INTO admins VALUES (?)", message.from_user.id)
    await message.answer("Демо-режим: вы назначены администратором. Уведомления о заказах и заявках будут "
                         "приходить сюда. Панель — /admin")


def admin_menu():
    return kb([
        [("📋 Сводка на сегодня", "adm:today")],
        [("🚫 Стоп-лист", "adm:stop"), ("🎟 Мероприятия", "adm:events")],
        [("📣 Рассылка", "adm:broadcast")],
    ])


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not await guard(message):
        return
    await state.clear()
    await message.answer("🛠 <b>Панель администратора</b>", reply_markup=admin_menu())


@router.callback_query(F.data == "adm:home")
async def cb_admin_home(cb: CallbackQuery, state: FSMContext) -> None:
    if not await guard(cb):
        return
    await state.clear()
    await edit_or_send(cb, "🛠 <b>Панель администратора</b>", admin_menu())


BACK = ("⬅️ Панель", "adm:home")


@router.callback_query(F.data == "adm:today")
async def cb_today(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    day = now().strftime("%Y-%m-%d")
    orders = await db.all("SELECT * FROM orders WHERE pickup_at LIKE ? ORDER BY pickup_at", day + "%")
    bookings = await db.all("SELECT * FROM bookings WHERE at LIKE ? AND status!='cancelled' ORDER BY at", day + "%")
    reqs = await db.all("SELECT kind, COUNT(*) c FROM requests WHERE status='new' GROUP BY kind")
    lines = [f"📋 <b>Сегодня, {now():%d.%m}</b>\n", f"☕ Заказов: {len(orders)}"]
    lines += [f"  #{o['id']} {o['pickup_at'][11:]} · {POINTS[o['point']]['name']} · {o['total']} ₽ · "
              f"{STATUS_RU.get(o['status'], 'новый')}"
              for o in orders]
    lines.append(f"\n📅 Броней: {len(bookings)}")
    lines += [f"  #{b['id']} {b['at'][11:]} · {POINTS[b['point']]['name']} · {b['guests']} чел. · "
              f"{STATUS_RU.get(b['status'], 'ждёт ответа')}"
              for b in bookings]
    if reqs:
        lines.append("\n📨 Необработанные заявки:\n" + "\n".join(
            f"  {KIND_RU.get(r['kind'], r['kind'])} — {r['c']}" for r in reqs))
    await edit_or_send(cb, "\n".join(lines), kb([[BACK]]))


# --- смена статусов ---

STATUS_RU = {"accepted": "принят, готовится", "ready": "готов", "done": "выдан", "declined": "отклонён",
             "confirmed": "подтверждена"}

KIND_RU = {"question": "вопросы", "corporate": "корпоративы", "job": "отклики на вакансии",
           "library_card": "читательские билеты", "library_book": "брони книг", "shop": "отложить книгу",
           "blind_date": "свидание вслепую", "club": "вступление в клубы", "waitlist": "лист ожидания"}

ORDER_MSG = {
    "accepted": "👨‍🍳 Заказ #{id} принят и готовится — к {time}, {point}.",
    "ready": "✅ Заказ #{id} готов — ждём вас: {point}!",
    "done": "✔️ Заказ #{id} выдан. Приятного аппетита! Начислили {pts} бонусов 🎁",
    "declined": "😔 К сожалению, кофейня не может выполнить заказ #{id}. Свяжитесь с нами для деталей.",
}


@router.callback_query(F.data.startswith("adm:o:"))
async def cb_order_status(cb: CallbackQuery, bot: Bot) -> None:
    if not await guard(cb):
        return
    _, _, status, oid = cb.data.split(":")
    o = await db.one("SELECT * FROM orders WHERE id=?", int(oid))
    if o["status"] == status:
        await cb.answer("Уже в этом статусе")
        return
    await db.run("UPDATE orders SET status=? WHERE id=?", status, o["id"])
    pts = 0
    if status == "done":
        pts = o["total"] * LOYALTY_PERCENT // 100
        await db.add_points(o["user_id"], pts)
    # Короткое уведомление со звуком + обновление закреплённой карточки заказа
    await bot.send_message(o["user_id"], ORDER_MSG[status].format(
        id=o["id"], pts=pts, time=o["pickup_at"][11:], point=POINTS[o["point"]]["name"]))
    await refresh_card(bot, o["id"])
    await cb.answer(f"Статус: {STATUS_RU[status]}")
    await cb.message.edit_text(cb.message.html_text + f"\n\n➡️ <b>{STATUS_RU[status]}</b> ({cb.from_user.first_name}, "
                                                      f"{now():%H:%M})", reply_markup=cb.message.reply_markup)


@router.callback_query(F.data.startswith("adm:b:"))
async def cb_booking_status(cb: CallbackQuery, bot: Bot) -> None:
    if not await guard(cb):
        return
    _, _, status, bid = cb.data.split(":")
    b = await db.one("SELECT * FROM bookings WHERE id=?", int(bid))
    await db.run("UPDATE bookings SET status=? WHERE id=?", status, b["id"])
    when = human_dt(parse(b["at"]))
    if status == "confirmed":
        text = f"✅ Бронь #{b['id']} подтверждена: {POINTS[b['point']]['name']}, {when}. Ждём вас!"
    else:
        text = (f"😔 На {when} в {POINTS[b['point']]['name']} свободных столов нет. "
                "Попробуйте другое время или другую кофейню.")
    await bot.send_message(b["user_id"], text, reply_markup=main_menu())
    await cb.answer("Готово")
    await cb.message.edit_text(cb.message.html_text + f"\n\n➡️ <b>{STATUS_RU[status]}</b> ({cb.from_user.first_name})")


@router.callback_query(F.data.startswith("adm:lib:"))
async def cb_library_card(cb: CallbackQuery, bot: Bot) -> None:
    if not await guard(cb):
        return
    uid = int(cb.data.split(":")[2])
    card = f"LG-L-{uid % 100000:05d}"
    until = (now() + timedelta(days=LIBRARY_DAYS)).strftime("%d.%m.%Y")
    await db.run("UPDATE library SET card=?, active_until=? WHERE user_id=?", card, until, uid)
    await db.run("UPDATE requests SET status='done' WHERE user_id=? AND kind='library_card'", uid)
    await bot.send_message(uid, f"🪪 Ваш читательский билет: <b>{card}</b>, действует до {until}.\n"
                                f"Добро пожаловать в деловую библиотеку! Челлендж — {CHALLENGE_GOAL} книг за год 📚",
                           reply_markup=main_menu())
    await cb.answer("Билет выдан")
    await cb.message.edit_text(cb.message.html_text + f"\n\n➡️ Выдан билет {card}")


# --- ответ на вопрос гостя ---

class ReplySG(StatesGroup):
    text = State()


@router.callback_query(F.data.startswith("adm:reply:"))
async def cb_reply(cb: CallbackQuery, state: FSMContext) -> None:
    if not await guard(cb):
        return
    await state.set_state(ReplySG.text)
    await state.update_data(reply_to=int(cb.data.split(":")[2]))
    await cb.answer()
    await cb.message.answer("Напишите ответ гостю одним сообщением:")


@router.message(ReplySG.text, F.text)
async def on_reply(message: Message, state: FSMContext, bot: Bot) -> None:
    d = await state.get_data()
    await state.clear()
    await bot.send_message(d["reply_to"], f"💬 <b>Ответ администратора:</b>\n{message.html_text}",
                           reply_markup=main_menu())
    await message.answer("Ответ отправлен ✅")


# --- стоп-лист ---

@router.callback_query(F.data == "adm:stop")
async def cb_stop(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    await edit_or_send(cb, "🚫 <b>Стоп-лист</b>\nВыберите кофейню:",
                       kb([[(p["name"], f"adm:sp:{c}")] for c, p in POINTS.items()] + [[BACK]]))


@router.callback_query(F.data.startswith("adm:sp:"))
async def cb_stop_point(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    point = cb.data.split(":")[2]
    secs = sections(point)
    stopped = await db.stopped(point)
    await edit_or_send(cb, f"🚫 {POINTS[point]['name']}\nСейчас в стоп-листе: {len(stopped)}. Раздел:",
                       kb(grid([(s, f"adm:ss:{point}:{i}") for i, s in enumerate(secs)]) +
                          [[("⬅️ Точки", "adm:stop")]]))


@router.callback_query(F.data.startswith("adm:ss:"))
async def cb_stop_section(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    _, _, point, i = cb.data.split(":")
    await render_stop_section(cb, point, i)


async def render_stop_section(cb: CallbackQuery, point: str, i: str) -> None:
    section = sections(point)[int(i)]
    stopped = await db.stopped(point)
    items = items_in(point, section)
    listing = "\n".join(f"{n}. {'⛔️' if it.idx in stopped else '✅'} {it.name}" for n, it in enumerate(items, 1))
    rows = grid([(f"{'⛔️' if it.idx in stopped else '✅'} {n}", f"adm:st:{point}:{i}:{it.idx}")
                 for n, it in enumerate(items, 1)])
    await edit_or_send(cb, f"<b>{section}</b>\n\n{listing}\n\n✅ в меню · ⛔️ в стоп-листе.\n"
                           "Нажмите номер, чтобы убрать или вернуть позицию:",
                       kb(rows + [[("⬅️ Разделы", f"adm:sp:{point}")]]))


@router.callback_query(F.data.startswith("adm:st:"))
async def cb_stop_toggle(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    _, _, point, i, idx = cb.data.split(":")
    now_stopped = await db.toggle_stop(point, int(idx))
    await cb.answer("Убрано из меню" if now_stopped else "Возвращено в меню")
    await render_stop_section(cb, point, i)


# --- мероприятия ---

class EventAddSG(StatesGroup):
    title = State()
    when = State()
    price = State()
    seats = State()


@router.callback_query(F.data == "adm:events")
async def cb_events(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    rows = await db.all("SELECT * FROM events WHERE active=1 AND starts_at>=? ORDER BY starts_at", iso(now()))
    lines, buttons = [], []
    for i, e in enumerate(rows):
        taken = await db.seats_taken(e["id"])
        lines.append(f"{num(i)} {human_dt(parse(e['starts_at']))} — {e['title']} · записано {taken}/{e['seats']}")
        buttons.append((f"{num(i)} {parse(e['starts_at']):%d.%m %H:%M}", f"adm:ev:{e['id']}"))
    await edit_or_send(cb, "🎟 <b>Ближайшие мероприятия</b>\n\n" + ("\n".join(lines) or "Пока пусто.") +
                       "\n\nНажмите на мероприятие, чтобы увидеть участников:",
                       kb(grid(buttons) + [[("➕ Добавить мероприятие", "adm:evadd")], [BACK]]))


@router.callback_query(F.data.regexp(r"^adm:ev:\d+$"))
async def cb_event_people(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    eid = int(cb.data.split(":")[2])
    e = await db.one("SELECT * FROM events WHERE id=?", eid)
    regs = await db.all("SELECT * FROM registrations WHERE event_id=? AND status='registered'", eid)
    lines = [f"<b>{e['title']}</b>\n{human_dt(parse(e['starts_at']))}\n"]
    for r in regs:
        lines.append(f"LG-{r['id']:04d} · {r['people']} мест · {r['pay']} · {await user_label(r['user_id'])}")
    if not regs:
        lines.append("Пока никто не записался.")
    came = [[(f"✅ Пришёл LG-{r['id']:04d}", f"adm:came:{r['id']}")] for r in regs]
    await edit_or_send(cb, "\n".join(lines), kb(came + [[("🗑 Снять с афиши", f"adm:evoff:{eid}")],
                                                         [("⬅️ Мероприятия", "adm:events")]]))


@router.callback_query(F.data.startswith("adm:came:"))
async def cb_came(cb: CallbackQuery, bot: Bot) -> None:
    if not await guard(cb):
        return
    r = await db.one("SELECT * FROM registrations WHERE id=?", int(cb.data.split(":")[2]))
    if r["status"] != "registered":
        await cb.answer("Уже отмечено")
        return
    await db.run("UPDATE registrations SET status='attended' WHERE id=?", r["id"])
    await db.add_points(r["user_id"], LOYALTY_EVENT_BONUS)
    await bot.send_message(r["user_id"], f"Спасибо, что были с нами! Начислили {LOYALTY_EVENT_BONUS} бонусов 🎁")
    await cb.answer("Отмечено, бонусы начислены")


@router.callback_query(F.data.startswith("adm:evoff:"))
async def cb_event_off(cb: CallbackQuery) -> None:
    if not await guard(cb):
        return
    await db.run("UPDATE events SET active=0 WHERE id=?", int(cb.data.split(":")[2]))
    await cb.answer("Снято с афиши")
    await cb_events(cb)


@router.callback_query(F.data == "adm:evadd")
async def cb_event_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not await guard(cb):
        return
    await state.set_state(EventAddSG.title)
    await edit_or_send(cb, "Название мероприятия (можно с ведущим через запятую):", kb([[BACK]]))


@router.message(EventAddSG.title, F.text)
async def ev_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(EventAddSG.when)
    await message.answer("Дата и время в формате ДД.ММ ЧЧ:ММ, например 24.10 18:30")


@router.message(EventAddSG.when, F.text)
async def ev_when(message: Message, state: FSMContext) -> None:
    try:
        dt = datetime.strptime(f"{message.text.strip()} {now().year}", "%d.%m %H:%M %Y")
    except ValueError:
        await message.answer("Не понял дату. Пример: 24.10 18:30")
        return
    if dt < now():
        dt = dt.replace(year=dt.year + 1)
    await state.update_data(when=iso(dt))
    await state.set_state(EventAddSG.price)
    await message.answer("Цена в рублях (0 — бесплатно):")


@router.message(EventAddSG.price, F.text.regexp(r"^\d+$"))
async def ev_price(message: Message, state: FSMContext) -> None:
    await state.update_data(price=int(message.text))
    await state.set_state(EventAddSG.seats)
    await message.answer(f"Сколько мест? (по умолчанию {DEFAULT_SEATS})")


@router.message(EventAddSG.seats, F.text.regexp(r"^\d+$"))
async def ev_seats(message: Message, state: FSMContext) -> None:
    d = await state.get_data()
    await state.clear()
    kids = 1 if "лампочк" in d["title"].lower() or "дет" in d["title"].lower() else 0
    await db.run("INSERT INTO events (title, speaker, starts_at, point, price, price_note, seats, kids) "
                 "VALUES (?,?,?,?,?,?,?,?)", d["title"], "", d["when"], "L", d["price"], "", int(message.text), kids)
    await message.answer(f"Добавлено в афишу: <b>{d['title']}</b>, {human_dt(parse(d['when']))}",
                         reply_markup=admin_menu())


# --- рассылка ---

class BroadcastSG(StatesGroup):
    text = State()


@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast(cb: CallbackQuery, state: FSMContext) -> None:
    if not await guard(cb):
        return
    await state.set_state(BroadcastSG.text)
    await edit_or_send(cb, "Отправьте текст рассылки. Перед отправкой покажу предпросмотр.\n"
                           "Чтобы разослать только подписчикам «Что на фильтре», начните текст с #фильтр",
                       kb([[BACK]]))


@router.message(BroadcastSG.text, F.text)
async def on_broadcast_text(message: Message, state: FSMContext) -> None:
    only_filter = message.text.startswith("#фильтр")
    text = message.html_text.replace("#фильтр", "", 1).strip() if only_filter else message.html_text
    sql = "SELECT COUNT(*) FROM users" + (" WHERE filter_sub=1" if only_filter else "")
    count = (await db.one(sql))[0]
    await state.update_data(b_text=text, b_filter=only_filter)
    await message.answer(f"Предпросмотр ({count} получателей):\n\n{text}",
                         reply_markup=kb([[("📣 Отправить", "adm:bsend"), ("Отмена", "adm:home")]]))


@router.callback_query(BroadcastSG.text, F.data == "adm:bsend")
async def cb_broadcast_send(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await guard(cb):
        return
    d = await state.get_data()
    await state.clear()
    sql = "SELECT id FROM users" + (" WHERE filter_sub=1" if d["b_filter"] else "")
    sent = 0
    for r in await db.all(sql):
        try:
            await bot.send_message(r["id"], d["b_text"])
            sent += 1
        except Exception:
            pass
    await edit_or_send(cb, f"Рассылка отправлена: {sent} получателей ✅", admin_menu())
