"""Карточка заказа у гостя: закреплена в чате и обновляется, пока заказ не выдан."""
from __future__ import annotations

import logging

from aiogram import Bot

from .common import human_date
from .content import POINTS
from .db import db, now, parse

log = logging.getLogger(__name__)

STATUS_LINE = {
    "new": "⏳ Ждём подтверждения кофейни",
    "accepted": "👨‍🍳 Готовим",
    "ready": "✅ Готов — ждём вас!",
    "done": "✔️ Выдан. Приятного аппетита!",
    "declined": "❌ Кофейня не может выполнить заказ",
}
FINAL = ("done", "declined")


def card_text(o) -> str:
    at = parse(o["pickup_at"])
    day = "сегодня" if at.date() == now().date() else human_date(at)
    lines = [
        f"🧾 <b>Заказ #{o['id']}</b>",
        f"Статус: <b>{STATUS_LINE.get(o['status'], o['status'])}</b>",
        "",
        f"📍 {POINTS[o['point']]['name']}",
        f"⏰ К {at:%H:%M}, {day}",
        f"💳 Оплата: {o['pay']}",
    ]
    if o["comment"]:
        lines.append(f"💬 {o['comment']}")
    lines += ["", o["items_text"] or "", "", f"💰 <b>Итого: {o['total']} ₽</b>"]
    if o["status"] not in FINAL:
        lines.append("\n<i>Карточка закреплена вверху чата и обновится, когда изменится статус.</i>")
    return "\n".join(lines)


async def send_card(bot: Bot, order_id: int) -> None:
    o = await db.one("SELECT * FROM orders WHERE id=?", order_id)
    msg = await bot.send_message(o["user_id"], card_text(o))
    await db.run("UPDATE orders SET card_msg_id=? WHERE id=?", msg.message_id, order_id)
    try:
        await bot.pin_chat_message(o["user_id"], msg.message_id, disable_notification=True)
    except Exception as e:
        log.warning("Не удалось закрепить заказ #%s: %s", order_id, e)


async def refresh_card(bot: Bot, order_id: int) -> None:
    """Обновляет закреплённую карточку; после выдачи или отказа — открепляет."""
    o = await db.one("SELECT * FROM orders WHERE id=?", order_id)
    if not o["card_msg_id"]:
        return
    try:
        await bot.edit_message_text(card_text(o), chat_id=o["user_id"], message_id=o["card_msg_id"])
    except Exception as e:
        log.warning("Не удалось обновить карточку заказа #%s: %s", order_id, e)
    if o["status"] in FINAL:
        try:
            await bot.unpin_chat_message(o["user_id"], message_id=o["card_msg_id"])
        except Exception as e:
            log.warning("Не удалось открепить заказ #%s: %s", order_id, e)
