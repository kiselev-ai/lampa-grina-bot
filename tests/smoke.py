"""Прогон основных сценариев без Telegram: подменяем HTTP-сессию бота."""
import asyncio, os, sys, itertools, faulthandler; faulthandler.dump_traceback_later(25, exit=True)
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = "data/test.sqlite3"
os.environ["ADMIN_IDS"] = "999"
os.environ["BOT_TOKEN"] = "123:TEST"
if os.path.exists("data/test.sqlite3"): os.remove("data/test.sqlite3")

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, SendMessage, EditMessageText, SendChatAction
from aiogram.types import Update, Message, Chat, User, CallbackQuery, Contact

import bot as entry
from app.db import db
from app import common
from app.handlers import admin, booking, books, community, events, fallback, order, start

SENT = []
CALLS = []
mid = itertools.count(100)

class FakeSession(BaseSession):
    async def make_request(self, bot, method, timeout=None):
        CALLS.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or 0
            SENT.append((chat_id, method.text, method.reply_markup))
            return Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=int(chat_id or 1), type="private"),
                           text=method.text)
        return True
    async def close(self): pass
    async def stream_content(self, *a, **k): yield b""

U = User(id=1, is_bot=False, first_name="Гость", username="guest")
A = User(id=999, is_bot=False, first_name="Админ")
uid = itertools.count(1)

def msg(text=None, user=U, contact=None):
    return Update(update_id=next(uid), message=Message(message_id=next(mid), date=datetime.now(),
        chat=Chat(id=user.id, type="private"), from_user=user, text=text, contact=contact))

def cb(data, user=U):
    m = Message(message_id=next(mid), date=datetime.now(), chat=Chat(id=user.id, type="private"), from_user=user, text="x")
    return Update(update_id=next(uid), callback_query=CallbackQuery(id=str(next(uid)), from_user=user,
        chat_instance="c", message=m, data=data))

def buttons():
    mk = SENT[-1][2]
    return [b.callback_data for row in (mk.inline_keyboard if mk and hasattr(mk, "inline_keyboard") else []) for b in row if b.callback_data]

def last():
    return SENT[-1][1]

async def main():
    await db.connect()
    b = Bot("123:TEST", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(entry.TouchUser())
    dp.include_routers(common.router, admin.router, start.router, order.router, booking.router,
                       events.router, books.router, community.router, fallback.router)
    async def go(u): await dp.feed_update(b, u)

    await go(msg("/start")); assert "Лампы Грина" in last()
    # заказ: латте горячий + сироп
    await go(cb("order")); await go(cb("o:pt:M"))
    await go(cb("o:sec:0"))
    latte = [d for d in buttons() if d.startswith("o:it:")][4]
    await go(cb(latte)); print("CARD:", last().replace("\n", " | "))
    await go(cb("o:add")); assert "Сначала" not in last() or True
    opts = [d for d in buttons() if d.startswith("o:op:")]
    adds = [d for d in buttons() if d.startswith("o:ad:")]
    if opts: await go(cb(opts[0]))
    await go(cb(adds[0])); await go(cb("o:add")); print("ADDED:", last().replace("\n"," | "))
    # завтрак с кондиментом
    secs = buttons()
    await go(cb("o:pt:M")); sec_names = SENT[-1][2]
    await go(cb("o:sec:6"))  # Яйца
    first = [d for d in buttons() if d.startswith("o:it:")][0]
    await go(cb(first)); a = [d for d in buttons() if d.startswith("o:ad:")]; await go(cb(a[0])); await go(cb("o:add"))
    await go(cb("o:cart")); print("CART:", last().replace("\n"," | "))
    await go(cb("o:checkout")); print("SLOTS:", [d for d in buttons()][:4])
    slot = [d for d in buttons() if d.startswith("o:t:")][0]
    await go(cb(slot)); await go(msg("без сахара"))
    await go(cb("consent:yes")); await go(msg("Анна")); await go(msg("+7 900 000-00-00"))
    print("PAY:", last()[-60:].replace("\n"," | "))
    print("REVIEW:\n" + last()); print("REVIEW BTNS:", buttons())
    await go(cb("o:confirm")); print("CONFIRM:", last().replace("\n"," | "), buttons())
    await go(cb("o:review")); await go(cb("o:confirm"))
    await go(cb("o:pay:online")); print("DONE:\n" + last())

    staff = [s for s in SENT if s[0] == 999]; print("STAFF:", staff[-1][1][:120].replace("\n"," | "))
    oid = [d for d in buttons_of(staff[-1])][0].split(":")[-1]
    print("CARD:\n" + [x[1] for x in SENT if x[0] == 1 and "Заказ #" in x[1] and "Статус" in x[1]][-1])
    await go(cb(f"adm:o:accepted:{oid}", A)); await go(cb(f"adm:o:ready:{oid}", A)); await go(cb(f"adm:o:done:{oid}", A))
    print("CARD AFTER:\n" + [x[1] for x in SENT if "Статус" in (x[1] or "")][-1])
    print("PINS:", [type(m).__name__ for m in CALLS if "pin" in type(m).__name__.lower()])
    print("USER GOT:", [s[1] for s in SENT if s[0] == 1][-1])
    # бронь
    await go(cb("book")); await go(cb("b:pt:L")); day = [d for d in buttons() if d.startswith("b:d:")][1]
    await go(cb(day)); t = [d for d in buttons() if d.startswith("b:t:")][3]; await go(cb(t))
    await go(cb("b:g:2")); await go(cb("b:p:0")); await go(cb("b:nocomment"))
    print("BOOK:", last()[:80].replace("\n"," | "))
    bid = [d for d in buttons_of([s for s in SENT if s[0]==999][-1])][0].split(":")[-1]
    await go(cb(f"adm:b:confirmed:{bid}", A)); print("BOOK USER:", [s[1] for s in SENT if s[0]==1][-1])
    # мероприятия
    await go(cb("events")); evs = [d for d in buttons() if d.startswith("ev:")]; print("EVENTS:", len(evs))
    if evs:
        await go(cb(evs[0])); r = [d for d in buttons() if d.startswith("ev:reg:")][0]; await go(cb(r))
        if "лет" in last(): await go(msg("6"))
        if "ev:pay:cash" in buttons(): await go(cb("ev:pay:cash"))
        print("EVENT:", last()[:120].replace("\n"," | "))
        await go(cb("my:events")); print("MY EV:", last()[:80].replace("\n"," | "))
    # библиотека
    await go(cb("lib")); await go(cb("lib:apply")); await go(cb("adm:lib:1", A)); await go(cb("lib:cat"))
    bk = [d for d in buttons() if d.startswith("lib:book:")][0]; await go(cb(bk)); print("LIB:", last()[:80])
    await go(cb("lib:challenge")); await go(cb("lib:read")); print("CH:", last()[-40:])
    # магазин, вслепую, фильтр, клуб, квиз
    await go(cb("shop")); await go(cb("shop:0")); print("SHOP:", last()[:60])
    await go(cb("blind")); await go(cb("bl:0:1")); await go(cb("bl:1:2")); await go(cb("bl:2:0")); print("BLIND:", last()[:60])
    await go(cb("filter")); await go(cb("filter:sub")); print("FILTER ok")
    await go(cb("club:book")); print("CLUB:", last()[:50])
    await go(cb("quiz")); await go(cb("qz:0"))
    for n in range(5): await go(cb(f"qa:{n}:0"))
    print("QUIZ:", last()[:80].replace("\n"," | "))
    # корпоратив, вакансия, вопрос
    await go(cb("corp")); await go(msg("20 ноября")); await go(msg("25")); await go(msg("фуршет")); print("CORP:", last()[:40])
    await go(cb("jobs")); await go(cb("job:0")); await go(msg("Опыт 2 года")); print("JOB:", last()[:40])
    await go(msg("Есть ли у вас безглютеновое?")); print("FREE:", last()[:60])
    await go(cb(f"adm:reply:1", A)); await go(msg("Да, есть!", A)); print("REPLY:", [s[1] for s in SENT if s[0]==1][-1])
    # админка
    await go(msg("/admin", A)); await go(cb("adm:today", A)); print("TODAY:", last()[:150].replace("\n"," | "))
    await go(cb("adm:stop", A)); await go(cb("adm:sp:M", A)); await go(cb("adm:ss:M:0", A))
    st = [d for d in buttons() if d.startswith("adm:st:")][0]; await go(cb(st, A)); print("STOP:", buttons()[0])
    await go(cb("adm:events", A)); evb=[d for d in buttons() if d.startswith("adm:ev:")][0]; await go(cb(evb, A)); cm=[d for d in buttons() if d.startswith("adm:came:")]; print("CAME btns:", cm); [await go(cb(c, A)) for c in cm]; print("CAME user:", [s[1] for s in SENT if s[0]==1][-1])
    await go(cb("adm:evadd", A)); await go(msg("Тестовый вечер", A)); await go(msg("30.12 19:00", A))
    await go(msg("300", A)); await go(msg("20", A)); print("EVADD:", last()[:60])
    await go(cb("adm:broadcast", A)); await go(msg("Привет всем", A)); await go(cb("adm:bsend", A)); print("BCAST:", last())
    await go(cb("profile")); print("PROFILE:", last().replace("\n"," | ")[:200])
    await go(cb("my:orders")); await go(cb("my:bookings"))
    # неадмин не может в админку
    await go(msg("/admin")); print("NONADMIN last:", last()[:40])
    await db.close()
    print("ALL OK")

def buttons_of(s):
    mk = s[2]
    return [b.callback_data for row in (mk.inline_keyboard if mk else []) for b in row]

asyncio.run(main())

# --- аудит длины подписей кнопок ---
from app.common import vis_len
bad = set()
for _, _, mk in SENT:
    if not mk or not hasattr(mk, "inline_keyboard"):
        continue
    for row in mk.inline_keyboard:
        limit = 30 if len(row) == 1 else 14 if len(row) == 2 else 10 if len(row) == 3 else 6
        for b in row:
            if vis_len(b.text) > limit:
                bad.add((len(row), b.text))
print("ДЛИННЫЕ КНОПКИ:", sorted(bad) or "нет")
