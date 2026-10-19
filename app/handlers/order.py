"""Предзаказ к приходу: меню точки → карточка → корзина → время → оплата (демо)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..common import (FULL_WIDTH, HOME_BTN, ask_contact, choice_list, chunks, continuation, edit_or_send, grid, human_date, human_dt,
                      kb,
                      main_menu,
                      notify_staff, points_kb, user_label)
from ..config import config
from ..content import POINTS
from ..db import db, iso, now, parse
from ..order_card import send_card
from ..menu import BY_IDX, Item, addons_for, items_in, sections

router = Router(name="order")


@dataclass
class Line:
    idx: int
    variant: int
    option: Optional[str]
    addons: List[int]
    qty: int = 1

    @property
    def item(self) -> Item:
        return BY_IDX[self.idx]

    @property
    def unit_price(self) -> int:
        return self.item.variants[self.variant].price + sum(BY_IDX[a].variants[0].price for a in self.addons)

    def title(self) -> str:
        it = self.item
        parts = [it.name]
        if len(it.variants) > 1:
            parts.append(it.variants[self.variant].label)
        if self.option:
            parts.append(self.option)
        if self.addons:
            parts.append("+ " + ", ".join(BY_IDX[a].name for a in self.addons))
        return " · ".join(parts)


@dataclass
class Cart:
    point: Optional[str] = None
    lines: List[Line] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(l.unit_price * l.qty for l in self.lines)


@dataclass
class Draft:
    idx: int
    variant: int = 0
    option: Optional[str] = None
    addons: Set[int] = field(default_factory=set)


CARTS: Dict[int, Cart] = {}
DRAFTS: Dict[int, Draft] = {}


def cart_of(uid: int) -> Cart:
    return CARTS.setdefault(uid, Cart())


class OrderSG(StatesGroup):
    comment = State()


def short_name(it: Item) -> str:
    """Короткая подпись для кнопки: первая часть названия и цена."""
    price = f" · {it.variants[0].price} ₽"
    budget = FULL_WIDTH + 2 - 4 - len(price)  # 4 — место под номер кнопки
    words, head = it.name.split(" / ")[0].split(" (")[0].split(), []
    for w in words:  # целыми словами, без обрыва на середине
        if len(" ".join(head + [w])) > budget:
            break
        head.append(w)
    while len(head) > 1 and head[-1] in ("с", "и", "из", "в", "на", "по", "со", "от"):
        head.pop()  # не заканчиваем подпись предлогом
    return f"{' '.join(head) or words[0]}{price}"


def cart_btn(uid: int) -> List[tuple]:
    c = cart_of(uid)
    return [(f"🛒 Корзина · {c.total} ₽", "o:cart")] if c.lines else []


@router.callback_query(F.data == "order")
async def cb_order(cb: CallbackQuery) -> None:
    c = cart_of(cb.from_user.id)
    if c.lines:
        await show_sections(cb, c.point)
        return
    await edit_or_send(cb, "☕ <b>Заказ к приходу</b>\n\nВ какой кофейне заберёте заказ?", points_kb("o:pt"))


@router.callback_query(F.data.startswith("o:pt:"))
async def cb_point(cb: CallbackQuery) -> None:
    point = cb.data.split(":")[2]
    c = cart_of(cb.from_user.id)
    if c.point != point:
        c.point, c.lines = point, []
    await show_sections(cb, point)


async def show_sections(cb: CallbackQuery, point: str) -> None:
    secs = sections(point)
    rows = grid([(s, f"o:sec:{i}") for i, s in enumerate(secs)])
    extra = cart_btn(cb.from_user.id)
    await edit_or_send(
        cb, f"📍 <b>{POINTS[point]['name']}</b>\nВыберите раздел меню:",
        kb(rows + ([extra] if extra else []) + [[("🔄 Другая кофейня", "o:change")], [HOME_BTN]]))


@router.callback_query(F.data == "o:change")
async def cb_change(cb: CallbackQuery) -> None:
    c = cart_of(cb.from_user.id)
    note = "\n\n<i>Корзина будет очищена — меню у точек разное.</i>" if c.lines else ""
    await edit_or_send(cb, "В какой кофейне заберёте заказ?" + note, points_kb("o:pt"))


@router.callback_query(F.data.startswith("o:sec:"))
async def cb_section(cb: CallbackQuery) -> None:
    point = cart_of(cb.from_user.id).point
    if point is None:
        await cb_order(cb)
        return
    i = int(cb.data.split(":")[2])
    section = sections(point)[i]
    stopped = await db.stopped(point)
    items = [it for it in items_in(point, section) if it.idx not in stopped]
    entries = [(f"{it.name} — {it.price_raw} ₽", short_name(it), f"o:it:{it.idx}") for it in items]
    listing, rows = choice_list(entries)
    text = f"<b>{section}</b>" + (f"\n\n{listing}" if listing else "")
    if any("12:00" in it.serve for it in items):
        text += f"\n🕛 Готовим {items[0].serve}"
    if not items:
        text += "\n\nСейчас в этом разделе всё закончилось 😔"
    extra = cart_btn(cb.from_user.id)
    await edit_or_send(cb, text, kb(rows + ([extra] if extra else []) + [[("⬅️ Разделы", f"o:pt:{point}")]]))


def item_kb(d: Draft, uid: int) -> list:
    it = BY_IDX[d.idx]
    rows = []
    if len(it.variants) > 1:
        rows += grid([(("✅ " if i == d.variant else "") + f"{v.label} · {v.price} ₽", f"o:v:{i}")
                      for i, v in enumerate(it.variants)])
    if it.options:
        rows += grid([(("✅ " if o == d.option else "") + o, f"o:op:{i}") for i, o in enumerate(it.options)])
    addon_btns = [(("✅ " if a.idx in d.addons else "➕ ") + str(n), f"o:ad:{a.idx}")
                  for n, a in enumerate(addons_for(it), 1)]
    rows += chunks(addon_btns, 5)
    line = Line(d.idx, d.variant, d.option, sorted(d.addons))
    rows.append([(f"🛒 Добавить · {line.unit_price} ₽", "o:add")])
    sec_i = sections(it.point).index(it.section)
    if cart_btn(uid):
        rows.append(cart_btn(uid))
    rows.append([("⬅️ Назад к разделу", f"o:sec:{sec_i}")])
    return rows


async def render_item(cb: CallbackQuery) -> None:
    d = DRAFTS[cb.from_user.id]
    it = BY_IDX[d.idx]
    text = it.card()
    addons = addons_for(it)
    if addons:
        text += "\n\n<b>Добавки</b> (нажмите номер, чтобы добавить или убрать):\n" + "\n".join(
            f"{n}. {'✅ ' if a.idx in d.addons else ''}{a.name}{' ' + a.size if a.size else ''} · +{a.variants[0].price} ₽"
            for n, a in enumerate(addons, 1))
    if it.options and not d.option:
        text += "\n\nВыберите вариант подачи 👇"
    await edit_or_send(cb, text, kb(item_kb(d, cb.from_user.id)))


@router.callback_query(F.data.startswith("o:it:"))
async def cb_item(cb: CallbackQuery) -> None:
    idx = int(cb.data.split(":")[2])
    if idx in await db.stopped(BY_IDX[idx].point):
        await cb.answer("Эта позиция закончилась", show_alert=True)
        return
    DRAFTS[cb.from_user.id] = Draft(idx=idx)
    await render_item(cb)


@router.callback_query(F.data.regexp(r"^o:(v|op|ad):\d+$"))
async def cb_tweak(cb: CallbackQuery) -> None:
    d = DRAFTS.get(cb.from_user.id)
    if not d:
        await cb.answer("Откройте позицию заново", show_alert=True)
        return
    _, what, n = cb.data.split(":")
    n = int(n)
    if what == "v":
        d.variant = n
    elif what == "op":
        d.option = BY_IDX[d.idx].options[n]
    else:
        d.addons ^= {n}
    await render_item(cb)


@router.callback_query(F.data == "o:add")
async def cb_add(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    d = DRAFTS.get(uid)
    if not d:
        await cb.answer()
        return
    it = BY_IDX[d.idx]
    if it.options and not d.option:
        await cb.answer("Сначала выберите вариант подачи", show_alert=True)
        return
    c = cart_of(uid)
    new = Line(d.idx, d.variant, d.option, sorted(d.addons))
    for l in c.lines:
        if (l.idx, l.variant, l.option, l.addons) == (new.idx, new.variant, new.option, new.addons):
            l.qty += 1
            break
    else:
        c.lines.append(new)
    DRAFTS.pop(uid, None)
    await cb.answer(f"Добавили: {it.name}")
    sec_i = sections(it.point).index(it.section)
    await edit_or_send(cb, f"✅ <b>{new.title()}</b> в корзине.\nИтого: {c.total} ₽",
                       kb([[("🛒 Оформить заказ", "o:cart")],
                           [("➕ Ещё из раздела", f"o:sec:{sec_i}")],
                           [("📋 Все разделы меню", f"o:pt:{it.point}")]]))


def line_block(i: int, l: Line) -> str:
    """Позиция с разбивкой цены: блюдо, каждая добавка, количество и сумма."""
    it = l.item
    v = it.variants[l.variant]
    details = [d for d in (v.label, l.option) if d]
    out = [f"{i}. {it.name}" + (f" ({', '.join(details)})" if details else "") + f" — {v.price} ₽"]
    for a in l.addons:
        out.append(f"     + {BY_IDX[a].name} — {BY_IDX[a].variants[0].price} ₽")
    if l.qty > 1:
        out.append(f"     {l.qty} × {l.unit_price} ₽ = <b>{l.unit_price * l.qty} ₽</b>")
    elif l.addons:
        out.append(f"     за позицию: <b>{l.unit_price} ₽</b>")
    return "\n".join(out)


def items_text(c: Cart) -> str:
    return "\n".join(line_block(i, l) for i, l in enumerate(c.lines, 1))


def cart_text(c: Cart) -> str:
    return (f"🛒 <b>Корзина</b> · {POINTS[c.point]['name']}\n\n{items_text(c)}\n\n"
            f"<b>Итого: {c.total} ₽</b>")


async def summary_text(c: Cart, data: dict, user_id: int) -> str:
    """Полная сводка заказа перед оплатой."""
    at = parse(data["pickup_at"])
    day = "сегодня" if at.date() == now().date() else "завтра"
    parts = [
        "🧾 <b>Проверьте заказ</b>\n",
        f"📍 {POINTS[c.point]['name']}",
        f"⏰ Приготовим к {at:%H:%M}, {day} — {human_date(at)}",
        f"👤 {await user_label(user_id)}",
        "",
        items_text(c),
        "",
        f"💰 <b>Итого к оплате: {c.total} ₽</b>",
    ]
    if data.get("comment"):
        parts.insert(4, f"💬 {data['comment']}")
    return "\n".join(parts)


@router.callback_query(F.data == "o:cart")
async def cb_cart(cb: CallbackQuery) -> None:
    c = cart_of(cb.from_user.id)
    if not c.lines:
        await edit_or_send(cb, "Корзина пуста.", kb([[("☕ К меню", "order"), HOME_BTN]]))
        return
    rows = [[("✅ Оформить заказ", "o:checkout")]]
    rows += chunks([(f"➖ {i + 1}", f"o:rm:{i}") for i in range(len(c.lines))], 5)
    rows += [[("➕ Добавить ещё", f"o:pt:{c.point}"), ("🗑 Очистить", "o:clear")]]
    await edit_or_send(cb, cart_text(c) + "\n\n<i>➖ и номер — убрать одну порцию позиции</i>", kb(rows))


@router.callback_query(F.data.startswith("o:rm:"))
async def cb_remove(cb: CallbackQuery) -> None:
    c = cart_of(cb.from_user.id)
    i = int(cb.data.split(":")[2])
    if i < len(c.lines):
        c.lines[i].qty -= 1
        if c.lines[i].qty <= 0:
            c.lines.pop(i)
    await cb_cart(cb)


@router.callback_query(F.data == "o:clear")
async def cb_clear(cb: CallbackQuery) -> None:
    CARTS.pop(cb.from_user.id, None)
    await edit_or_send(cb, "Корзина очищена.", main_menu())


def pickup_slots(c: Cart) -> List[datetime]:
    """Слоты каждые 15 минут: не раньше чем через 20 минут и в часы подачи всех позиций."""
    start_t = max(l.item.window[0] for l in c.lines)
    end_t = min(l.item.window[1] for l in c.lines)
    t0 = now() + timedelta(minutes=20)
    t0 = t0.replace(second=0) + timedelta(minutes=(15 - t0.minute % 15) % 15)
    slots: List[datetime] = []
    for day in range(2):
        d = (now() + timedelta(days=day)).date()
        t = datetime.combine(d, start_t)
        end = datetime.combine(d, end_t) - timedelta(minutes=15)
        while t <= end:
            if t >= t0:
                slots.append(t)
            t += timedelta(minutes=15)
        if slots:
            break
    return slots[:12]


@router.callback_query(F.data == "o:checkout")
async def cb_checkout(cb: CallbackQuery) -> None:
    c = cart_of(cb.from_user.id)
    if not c.lines:
        await cb_cart(cb)
        return
    slots = pickup_slots(c)
    if not slots:
        await edit_or_send(cb, "Не нашли подходящего времени выдачи — позиции подаются в разные часы. "
                               "Попробуйте разделить заказ.", kb([[("🛒 Корзина", "o:cart")]]))
        return
    day = "сегодня" if slots[0].date() == now().date() else "завтра"
    rows = chunks([(f"{s:%H:%M}", f"o:t:{s:%Y%m%d%H%M}") for s in slots], 4)
    await edit_or_send(cb, f"⏰ Ко скольки приготовить ({day})?", kb(rows + [[("🛒 Корзина", "o:cart")]]))


@router.callback_query(F.data.startswith("o:t:"))
async def cb_time(cb: CallbackQuery, state: FSMContext) -> None:
    at = datetime.strptime(cb.data.split(":")[2], "%Y%m%d%H%M")
    await state.set_state(OrderSG.comment)
    await state.update_data(pickup_at=iso(at))
    await edit_or_send(cb, f"Готовим к {human_dt(at)}.\n\nЕсть пожелания к заказу? Напишите сообщением "
                           "(например, «без сахара», «с собой»).", kb([[("Без комментария", "o:nocomment")]]))


@router.callback_query(OrderSG.comment, F.data == "o:nocomment")
async def cb_nocomment(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await cb.answer()
    await state.update_data(comment="")
    await ask_contact(cb.message, state, bot, cb.from_user.id, "order")


@router.message(OrderSG.comment, F.text)
async def on_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.update_data(comment=message.text[:300])
    await ask_contact(message, state, bot, message.from_user.id, "order")


REVIEW_KB = [
    [("✅ Всё верно, к оплате", "o:confirm")],
    [("✏️ Изменить состав", "o:cart")],
    [("⏰ Изменить время", "o:checkout")],
]


@continuation("order")
async def order_review(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    await state.set_state(None)  # данные заказа (время, комментарий) остаются в state
    c = cart_of(user_id)
    await message.answer(await summary_text(c, await state.get_data(), user_id), reply_markup=kb(REVIEW_KB))


@router.callback_query(F.data == "o:review")
async def cb_review(cb: CallbackQuery, state: FSMContext) -> None:
    c, data = cart_of(cb.from_user.id), await state.get_data()
    if not c.lines or "pickup_at" not in data:
        await edit_or_send(cb, "Сессия заказа устарела, начните заново.", main_menu())
        return
    await edit_or_send(cb, await summary_text(c, data, cb.from_user.id), kb(REVIEW_KB))


@router.callback_query(F.data == "o:confirm")
async def cb_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    c, data = cart_of(cb.from_user.id), await state.get_data()
    if not c.lines or "pickup_at" not in data:
        await edit_or_send(cb, "Сессия заказа устарела, начните заново.", main_menu())
        return
    await edit_or_send(cb, f"💰 К оплате: <b>{c.total} ₽</b>\n\nКак удобнее оплатить?", kb([
        [("💳 Оплатить онлайн (демо)", "o:pay:online")],
        [("💵 При получении", "o:pay:cash")],
        [("⬅️ Назад к заказу", "o:review")]]))


@router.callback_query(F.data.startswith("o:pay:"))
async def cb_pay(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    uid = cb.from_user.id
    c = cart_of(uid)
    data = await state.get_data()
    if not c.lines or "pickup_at" not in data:
        await edit_or_send(cb, "Сессия заказа устарела, начните заново.", main_menu())
        return
    pay = cb.data.split(":")[2]
    items = [{"title": l.title(), "qty": l.qty, "price": l.unit_price} for l in c.lines]
    oid = await db.run(
        "INSERT INTO orders (user_id, point, items, items_text, total, pickup_at, pay, comment, status, created) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        uid, c.point, json.dumps(items, ensure_ascii=False), items_text(c), c.total, data["pickup_at"],
        "онлайн (оплачено, демо)" if pay == "online" else "при получении",
        data.get("comment", ""), "new", iso(now()))
    text = items_text(c) + f"\n\n<b>Итого: {c.total} ₽</b>"
    CARTS.pop(uid, None)
    await state.clear()
    paid = "Тестовая оплата прошла ✅ " if pay == "online" else ""
    await edit_or_send(cb, f"{paid}Заказ <b>#{oid}</b> отправлен в кофейню 🙌")
    # Отдельное сообщение с заказом: его не перезапишет навигация по меню, и оно закреплено до выдачи
    await send_card(bot, oid)
    await cb.message.answer("Сообщим, когда начнём готовить и когда будет готово. Чем ещё помочь?",
                            reply_markup=main_menu())
    await notify_staff(
        bot,
        f"☕ <b>Новый заказ #{oid}</b>\n{await user_label(uid)}\n"
        f"К: <b>{data['pickup_at'][11:]}</b> ({data['pickup_at'][:10]})\n"
        f"Оплата: {'онлайн (демо)' if pay == 'online' else 'при получении'}\n\n"
        f"{text}" + (f"\n\n💬 {data['comment']}" if data.get("comment") else ""),
        kb([[("👨‍🍳 Принять", f"adm:o:accepted:{oid}"), ("❌ Отклонить", f"adm:o:declined:{oid}")],
            [("✅ Готов", f"adm:o:ready:{oid}"), ("✔️ Выдан", f"adm:o:done:{oid}")]]))
