"""Старт, главное меню, контакты, профиль и бонусы."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..common import (HOME_BTN, ask_contact, continuation, edit_or_send, grid, human_dt, kb, main_menu,
                      notify_staff, user_label)
from ..content import CHALLENGE_GOAL, FAQ, JOB_POSITIONS, LOYALTY_EVENT_BONUS, LOYALTY_PERCENT, POINTS
from ..db import db, parse

router = Router(name="start")

WELCOME = (
    "Здравствуйте! Это ассистент <b>«Лампы Грина»</b> 💚🤎 — спешелти кофеен и книжного клуба в Кирове.\n\n"
    "Здесь можно заказать кофе и завтрак к приходу, забронировать столик, записаться на мероприятие, "
    "оформить абонемент в деловую библиотеку и не только.\n\n"
    "<i>Это демо-версия для согласования: оплата и часть данных — тестовые.</i>"
)

STATUS = {"new": "🆕 новый", "accepted": "👨‍🍳 готовится", "ready": "✅ готов", "done": "✔️ выдан",
          "declined": "❌ отклонён", "confirmed": "✅ подтверждена", "cancelled": "🚫 отменена",
          "registered": "✅ записаны", "attended": "🎉 посетили"}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Чем можем помочь?", reply_markup=main_menu())


@router.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send(cb, "Чем можем помочь?", main_menu())


# --- контакты, вопросы, корпоративы, вакансии ---

@router.callback_query(F.data == "info")
async def cb_info(cb: CallbackQuery) -> None:
    await edit_or_send(cb, FAQ, kb([
        [("✍️ Задать вопрос администратору", "ask")],
        [("🎉 Корпоратив или аренда зала", "corp")],
        [("💼 Вакансии", "jobs"), HOME_BTN]]))


class AskSG(StatesGroup):
    text = State()


@router.callback_query(F.data == "ask")
async def cb_ask(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AskSG.text)
    await edit_or_send(cb, "Напишите ваш вопрос одним сообщением — передадим администратору, "
                           "ответ придёт сюда же.", kb([[HOME_BTN]]))


@router.message(AskSG.text, F.text)
async def on_question(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await forward_question(message, bot)
    await message.answer("Передали администратору 👌 Ответим в рабочее время.", reply_markup=main_menu())


async def forward_question(message: Message, bot: Bot) -> None:
    rid = await db.add_request(message.from_user.id, "question", {"text": message.text})
    await notify_staff(
        bot,
        f"❓ <b>Вопрос #{rid}</b> от {await user_label(message.from_user.id)}\n\n{message.text}",
        kb([[("✍️ Ответить", f"adm:reply:{message.from_user.id}")]]))


class CorpSG(StatesGroup):
    date = State()
    guests = State()
    details = State()


@router.callback_query(F.data == "corp")
async def cb_corp(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CorpSG.date)
    await edit_or_send(cb, "🎉 <b>Корпоратив или аренда зала</b>\n\nНа какую дату планируете? "
                           "Можно примерно: «конец ноября».", kb([[HOME_BTN]]))


@router.message(CorpSG.date, F.text)
async def corp_date(message: Message, state: FSMContext) -> None:
    await state.update_data(c_date=message.text)
    await state.set_state(CorpSG.guests)
    await message.answer("Сколько будет гостей?")


@router.message(CorpSG.guests, F.text)
async def corp_guests(message: Message, state: FSMContext) -> None:
    await state.update_data(c_guests=message.text)
    await state.set_state(CorpSG.details)
    await message.answer("Расскажите о формате: точка, кейтеринг, бюджет, пожелания.")


@router.message(CorpSG.details, F.text)
async def corp_details(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.update_data(c_details=message.text)
    await ask_contact(message, state, bot, message.from_user.id, "corp")


@continuation("corp")
async def corp_finish(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    d = await state.get_data()
    await state.clear()
    rid = await db.add_request(user_id, "corporate", {k: d[k] for k in ("c_date", "c_guests", "c_details")})
    await notify_staff(bot, f"🎉 <b>Заявка на корпоратив #{rid}</b>\n{await user_label(user_id)}\n"
                            f"Дата: {d['c_date']}\nГостей: {d['c_guests']}\n{d['c_details']}")
    await message.answer("Заявка отправлена! Менеджер свяжется с вами для обсуждения деталей.",
                         reply_markup=main_menu())


class JobSG(StatesGroup):
    position = State()
    about = State()


@router.callback_query(F.data == "jobs")
async def cb_jobs(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(JobSG.position)
    await edit_or_send(cb, "💼 <b>Работа в «Лампе Грина»</b>\n\nНа какую позицию хотите откликнуться?",
                       kb(grid([(p, f"job:{i}") for i, p in enumerate(JOB_POSITIONS)]) + [[HOME_BTN]]))


@router.callback_query(JobSG.position, F.data.startswith("job:"))
async def job_position(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(j_pos=JOB_POSITIONS[int(cb.data.split(":")[1])])
    await state.set_state(JobSG.about)
    await edit_or_send(cb, "Коротко расскажите о себе: возраст, опыт, удобный график, ссылка на резюме (если есть).")


@router.message(JobSG.about, F.text)
async def job_about(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.update_data(j_about=message.text)
    await ask_contact(message, state, bot, message.from_user.id, "job")


@continuation("job")
async def job_finish(message: Message, state: FSMContext, bot: Bot, user_id: int) -> None:
    d = await state.get_data()
    await state.clear()
    rid = await db.add_request(user_id, "job", {"position": d["j_pos"], "about": d["j_about"]})
    await notify_staff(bot, f"💼 <b>Отклик #{rid}: {d['j_pos']}</b>\n{await user_label(user_id)}\n\n{d['j_about']}")
    await message.answer("Спасибо! Передали анкету управляющему 🙌", reply_markup=main_menu())


# --- профиль ---

@router.callback_query(F.data == "profile")
async def cb_profile(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    u = await db.user(uid)
    lib = await db.one("SELECT * FROM library WHERE user_id=?", uid)
    lines = [f"👤 <b>{u['name'] or 'Гость'}</b>", f"🎁 Бонусов: <b>{u['points']}</b>"]
    if lib and lib["card"]:
        lines.append(f"📚 Читательский билет {lib['card']} до {lib['active_until']}, "
                     f"прочитано {lib['books_read']}/{CHALLENGE_GOAL}")
    lines.append(f"\n<i>Демо-механика: {LOYALTY_PERCENT}% от суммы выданного заказа и "
                 f"+{LOYALTY_EVENT_BONUS} за посещение мероприятия. Списание — на кассе.</i>")
    await edit_or_send(cb, "\n".join(lines), kb([
        [("🧾 Заказы", "my:orders"), ("📅 Брони", "my:bookings")],
        [("🎟 Мои мероприятия", "my:events")],
        [HOME_BTN]]))


@router.callback_query(F.data == "my:orders")
async def my_orders(cb: CallbackQuery) -> None:
    rows = await db.all("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 5", cb.from_user.id)
    text = "\n\n".join(
        f"<b>Заказ #{r['id']}</b> · {POINTS[r['point']]['name']}\n{human_dt(parse(r['pickup_at']))} · "
        f"{r['total']} ₽ · {STATUS.get(r['status'], r['status'])}" for r in rows) or "Заказов пока нет."
    await edit_or_send(cb, text, kb([[("⬅️ Профиль", "profile"), HOME_BTN]]))


@router.callback_query(F.data == "my:bookings")
async def my_bookings(cb: CallbackQuery) -> None:
    rows = await db.all("SELECT * FROM bookings WHERE user_id=? ORDER BY at DESC LIMIT 5", cb.from_user.id)
    text = "\n\n".join(
        f"<b>Бронь #{r['id']}</b> · {POINTS[r['point']]['name']}\n{human_dt(parse(r['at']))}, "
        f"гостей: {r['guests']} · {STATUS.get(r['status'], r['status'])}" for r in rows) or "Броней пока нет."
    buttons = [[(f"🚫 Отменить #{r['id']}", f"bk:cancel:{r['id']}")]
               for r in rows if r["status"] in ("new", "confirmed")]
    await edit_or_send(cb, text, kb(buttons + [[("⬅️ Профиль", "profile"), HOME_BTN]]))


@router.callback_query(F.data == "my:events")
async def my_events(cb: CallbackQuery) -> None:
    rows = await db.all(
        "SELECT r.*, e.title, e.starts_at FROM registrations r JOIN events e ON e.id=r.event_id "
        "WHERE r.user_id=? ORDER BY e.starts_at DESC LIMIT 5", cb.from_user.id)
    text = "\n\n".join(
        f"<b>{r['title']}</b>\n{human_dt(parse(r['starts_at']))} · мест: {r['people']} · "
        f"билет LG-{r['id']:04d} · {STATUS.get(r['status'], r['status'])}" for r in rows) or "Записей пока нет."
    buttons = [[(f"🚫 Отменить LG-{r['id']:04d}", f"ev:cancel:{r['id']}")]
               for r in rows if r["status"] == "registered"]
    await edit_or_send(cb, text, kb(buttons + [[("⬅️ Профиль", "profile"), HOME_BTN]]))
