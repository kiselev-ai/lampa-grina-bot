"""Деловая библиотека, челлендж «12 книг за год», книжный магазин и «Свидание с книгой вслепую»."""
from __future__ import annotations

from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..common import (HOME_BTN, ask_contact, continuation, edit_or_send, grid, kb, main_menu, notify_staff, num,
                      user_label)
from ..content import (BLIND_DATE_PRICE, CHALLENGE_GOAL, LIBRARY_BOOKS, LIBRARY_DAYS, LIBRARY_PRICE, POINTS,
                       SHOP_BOOKS)
from ..db import db, now

router = Router(name="books")

BLIND_QUESTIONS = [
    ("Какое настроение хочется?", ["Уютное", "Захватывающее", "Задуматься", "Посмеяться"]),
    ("Что ближе?", ["Классика", "Современная проза", "Нон-фикшн", "Удивите меня"]),
    ("Это подарок?", ["Да, в подарок", "Нет, для себя"]),
]


@router.callback_query(F.data == "books")
async def cb_books(cb: CallbackQuery) -> None:
    await edit_or_send(cb, "📚 <b>Книги в «Лампе Грина»</b>\n\n💼 Деловая библиотека — на Ленина, 54\n"
                           "🛍 Книжный магазин и «Свидание вслепую» — на Московской, 3", kb([
        [("💼 Деловая библиотека", "lib")],
        [("🎯 Челлендж «12 книг за год»", "lib:challenge")],
        [("🛍 Книжный магазин", "shop")],
        [("🎁 Свидание с книгой вслепую", "blind")],
        [HOME_BTN]]))


# --- библиотека ---

@router.callback_query(F.data == "lib")
async def cb_lib(cb: CallbackQuery) -> None:
    lib = await db.one("SELECT * FROM library WHERE user_id=?", cb.from_user.id)
    text = ("💼 <b>Деловая библиотека</b> на Ленина, 54\n\nКниги о бизнесе, лидерстве, мышлении, привычках и "
            f"развитии.\n\n• Абонемент — {LIBRARY_PRICE} ₽ в месяц\n• Личный онлайн-читательский билет\n"
            f"• Книгу можно взять домой на срок до {LIBRARY_DAYS} дней")
    if lib and lib["card"]:
        text += f"\n\n✅ Ваш билет: <b>{lib['card']}</b>, действует до {lib['active_until']}"
        buttons = [[("📖 Каталог и бронь книги", "lib:cat")]]
    elif lib:
        text += "\n\n⏳ Заявка на абонемент на рассмотрении."
        buttons = [[("📖 Каталог", "lib:cat")]]
    else:
        buttons = [[("🪪 Оформить абонемент", "lib:apply")], [("📖 Каталог", "lib:cat")]]
    await edit_or_send(cb, text, kb(buttons + [[("⬅️ Книги", "books"), HOME_BTN]]))


@router.callback_query(F.data == "lib:apply")
async def cb_lib_apply(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await cb.answer()
    await ask_contact(cb.message, state, bot, cb.from_user.id, "library")


@continuation("library")
async def lib_finish(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    await state.clear()
    await db.run("INSERT OR IGNORE INTO library (user_id) VALUES (?)", user_id)
    rid = await db.add_request(user_id, "library_card", {})
    await message.answer(
        f"Заявка на абонемент отправлена 📚\nОплатить {LIBRARY_PRICE} ₽ можно на Ленина, 54 или онлайн после "
        "подтверждения (в демо — без оплаты). Билет придёт сюда.", reply_markup=main_menu())
    await notify_staff(bot, f"🪪 <b>Заявка на читательский билет #{rid}</b>\n{await user_label(user_id)}",
                       kb([[("✅ Выдать билет", f"adm:lib:{user_id}")]]))


@router.callback_query(F.data == "lib:cat")
async def cb_lib_cat(cb: CallbackQuery) -> None:
    lib = await db.one("SELECT * FROM library WHERE user_id=?", cb.from_user.id)
    can_book = bool(lib and lib["card"])
    text = "📖 <b>Каталог деловой библиотеки</b> <i>(демо-список)</i>\n\n" + "\n".join(
        f"{i + 1}. {b}" for i, b in enumerate(LIBRARY_BOOKS))
    if not can_book:
        text += "\n\nБронировать книги можно после оформления абонемента."
    if can_book:
        text += "\n\nНажмите номер книги, чтобы отложить её:"
    buttons = grid([(f"📌 {i + 1}", f"lib:book:{i}") for i in range(len(LIBRARY_BOOKS))]) if can_book else []
    await edit_or_send(cb, text, kb(buttons + [[("⬅️ Библиотека", "lib"), HOME_BTN]]))


@router.callback_query(F.data.startswith("lib:book:"))
async def cb_lib_book(cb: CallbackQuery, bot: Bot) -> None:
    title = LIBRARY_BOOKS[int(cb.data.split(":")[2])]
    lib = await db.one("SELECT * FROM library WHERE user_id=?", cb.from_user.id)
    rid = await db.add_request(cb.from_user.id, "library_book", {"book": title})
    back = (now() + timedelta(days=LIBRARY_DAYS)).strftime("%d.%m")
    await edit_or_send(cb, f"Книга «{title}» отложена для вас на Ленина, 54 📌\n"
                           f"Если заберёте сегодня, вернуть нужно до {back} — напомним заранее.", main_menu())
    await notify_staff(bot, f"📌 <b>Бронь книги #{rid}</b>: {title}\nБилет {lib['card']} · "
                            f"{await user_label(cb.from_user.id)}")


@router.callback_query(F.data == "lib:challenge")
async def cb_challenge(cb: CallbackQuery) -> None:
    lib = await db.one("SELECT * FROM library WHERE user_id=?", cb.from_user.id)
    read = lib["books_read"] if lib else 0
    bar = "📗" * read + "▫️" * (CHALLENGE_GOAL - read)
    text = (f"🎯 <b>Челлендж «12 книг за год»</b>\n\nОдна деловая книга в месяц — и за год целая коллекция "
            f"новых идей.\n\n{bar}\nПрочитано: <b>{read} из {CHALLENGE_GOAL}</b>")
    await edit_or_send(cb, text, kb([[("✅ Я дочитал(а) книгу", "lib:read")], [("⬅️ Книги", "books"), HOME_BTN]]))


@router.callback_query(F.data == "lib:read")
async def cb_read(cb: CallbackQuery) -> None:
    await db.run("INSERT OR IGNORE INTO library (user_id) VALUES (?)", cb.from_user.id)
    await db.run("UPDATE library SET books_read = MIN(books_read + 1, ?) WHERE user_id=?",
                 CHALLENGE_GOAL, cb.from_user.id)
    await cb.answer("Отлично! Засчитали 📗")
    await cb_challenge(cb)


# --- магазин ---

@router.callback_query(F.data == "shop")
async def cb_shop(cb: CallbackQuery) -> None:
    listing = "\n".join(f"{num(i)} {t} — {p} ₽" for i, (t, p) in enumerate(SHOP_BOOKS))
    text = (f"🛍 <b>Книжный магазин</b> на Московской, 3\n\n{listing}\n\n"
            "Нажмите номер книги — отложим её для вас:")
    buttons = grid([(num(i), f"shop:{i}") for i in range(len(SHOP_BOOKS))])
    await edit_or_send(cb, text, kb(buttons + [[("⬅️ Книги", "books"), HOME_BTN]]))


@router.callback_query(F.data.regexp(r"^shop:\d+$"))
async def cb_shop_item(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    title, price = SHOP_BOOKS[int(cb.data.split(":")[1])]
    await state.update_data(shop_title=title, shop_price=price)
    await cb.answer()
    await ask_contact(cb.message, state, bot, cb.from_user.id, "shop")


@continuation("shop")
async def shop_finish(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    d = await state.get_data()
    await state.clear()
    rid = await db.add_request(user_id, "shop", {"book": d["shop_title"], "price": d["shop_price"]})
    await message.answer(f"Отложили «{d['shop_title']}» на Московской, 3 на 3 дня 📚\n"
                         f"Оплата при получении: {d['shop_price']} ₽.", reply_markup=main_menu())
    await notify_staff(bot, f"🛍 <b>Отложить книгу #{rid}</b>: {d['shop_title']} ({d['shop_price']} ₽)\n"
                            f"{POINTS['M']['name']} · {await user_label(user_id)}")


# --- свидание с книгой вслепую ---

@router.callback_query(F.data == "blind")
async def cb_blind(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(blind=[])
    await ask_blind(cb, 0)


async def ask_blind(cb: CallbackQuery, step: int) -> None:
    q, opts = BLIND_QUESTIONS[step]
    head = (f"🎁 <b>Свидание с книгой вслепую</b> — {BLIND_DATE_PRICE} ₽\nКнига в крафтовой обёртке: "
            "ответьте на 3 вопроса, а мы подберём.\n\n") if step == 0 else ""
    await edit_or_send(cb, f"{head}{step + 1}/3. {q}",
                       kb(grid([(o, f"bl:{step}:{i}") for i, o in enumerate(opts)])))


@router.callback_query(F.data.startswith("bl:"))
async def cb_blind_answer(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    _, step, i = cb.data.split(":")
    step, i = int(step), int(i)
    d = await state.get_data()
    answers = d.get("blind", [])[:step] + [BLIND_QUESTIONS[step][1][i]]
    await state.update_data(blind=answers)
    if step + 1 < len(BLIND_QUESTIONS):
        await ask_blind(cb, step + 1)
        return
    await cb.answer()
    await ask_contact(cb.message, state, bot, cb.from_user.id, "blind")


@continuation("blind")
async def blind_finish(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    d = await state.get_data()
    await state.clear()
    prefs = ", ".join(d.get("blind", []))
    rid = await db.add_request(user_id, "blind_date", {"prefs": prefs})
    await message.answer(f"Подберём книгу и отложим на Московской, 3 💌\nВаши ответы: {prefs}\n"
                         f"Оплата при получении: {BLIND_DATE_PRICE} ₽.", reply_markup=main_menu())
    await notify_staff(bot, f"🎁 <b>Свидание вслепую #{rid}</b>\nПредпочтения: {prefs}\n{await user_label(user_id)}")
