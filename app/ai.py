"""ИИ-консультант на Claude: отвечает на свободные вопросы гостей по меню, афише и адресам."""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

import anthropic

from .config import config
from .content import FAQ, FILTER, LIBRARY_PRICE, POINTS
from .db import db, iso, now
from .menu import menu_as_text

log = logging.getLogger(__name__)

_client: Optional[anthropic.AsyncAnthropic] = (
    anthropic.AsyncAnthropic(api_key=config.anthropic_key) if config.anthropic_key else None)
_history: Dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=8))

STATIC_PROMPT = f"""Ты — дружелюбный ассистент кофеен «Лампа Грина» в Кирове (спешелти кофе + книжный клуб).
Отвечай гостям по-русски, коротко (2–5 предложений), тепло, без канцелярита. Можно 1–2 эмодзи.

Ты НЕ оформляешь заказы, брони и записи сам — для этого в боте есть кнопки главного меню:
«☕ Заказать к приходу», «📅 Забронировать столик», «🎟 Афиша и запись», «📚 Библиотека и книги»,
«🫘 Что на фильтре», «💚 Клубы и квизы», «ℹ️ Контакты и вопросы». Подсказывай, какую кнопку нажать.
Используй только факты ниже. Если ответа в них нет (наличие мест, аллергены конкретного блюда,
скидки и т.п.) — честно скажи, что уточнит администратор, и предложи кнопку «ℹ️ Контакты и вопросы».
Не выдумывай цены, блюда и события. Пиши обычным текстом без Markdown-разметки.

# Контакты и часы
{FAQ}

# Деловая библиотека (Ленина, 54)
Абонемент {LIBRARY_PRICE} ₽/мес, книга домой до 30 дней, челлендж «12 книг за год».

# Меню по точкам (цена в ₽; «12:00–22:00» — блюдо подаётся только в эти часы)
{menu_as_text()}
"""


def enabled() -> bool:
    return _client is not None


async def _dynamic_context() -> str:
    events = await db.all("SELECT * FROM events WHERE active=1 AND starts_at>=? ORDER BY starts_at", iso(now()))
    ev = "\n".join(f"- {e['starts_at']} · {e['title']} · {POINTS[e['point']]['name']} · "
                   f"{'бесплатно' if not e['price'] else str(e['price']) + ' ₽'}" for e in events) or "нет"
    flt = "\n".join(f"- {POINTS[c]['name']}: " + "; ".join(f"{n} ({notes})" for n, _, notes in v)
                    for c, v in FILTER.items())
    return f"Сейчас: {now():%d.%m.%Y %H:%M}.\n\n# Ближайшие мероприятия\n{ev}\n\n# Что на фильтре\n{flt}"


async def answer(user_id: int, text: str) -> Optional[str]:
    """Ответ консультанта или None, если ИИ недоступен — тогда вопрос уходит администратору."""
    if not _client:
        return None
    history = _history[user_id]
    history.append({"role": "user", "content": text})
    try:
        response = await _client.beta.messages.create(
            model=config.claude_model,
            max_tokens=2000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[
                {"type": "text", "text": STATIC_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": await _dynamic_context()},
            ],
            messages=list(history),
        )
    except anthropic.RateLimitError:
        log.warning("Claude: превышен лимит запросов")
        history.pop()
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude API %s: %s", e.status_code, e.message)
        history.pop()
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude: нет соединения")
        history.pop()
        return None
    if response.stop_reason == "refusal":
        history.pop()
        return None
    reply = "".join(b.text for b in response.content if b.type == "text").strip()
    if not reply:
        history.pop()
        return None
    history.append({"role": "assistant", "content": reply})
    return reply
