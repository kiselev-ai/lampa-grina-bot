"""Настройки бота из переменных окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _ids(raw: str) -> List[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


@dataclass
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: List[int] = field(default_factory=lambda: _ids(os.getenv("ADMIN_IDS", "")))
    # Чат (или группа) для уведомлений персонала; по умолчанию — личные сообщения админам
    admin_chat_id: Optional[int] = int(os.getenv("ADMIN_CHAT_ID")) if os.getenv("ADMIN_CHAT_ID") else None
    # Демо-режим: команда /demo_admin делает отправителя администратором
    demo_mode: bool = os.getenv("DEMO_MODE", "1") == "1"
    tz: ZoneInfo = ZoneInfo(os.getenv("TZ_NAME", "Europe/Kirov"))
    db_path: Path = BASE_DIR / os.getenv("DB_PATH", "data/bot.sqlite3")
    menu_path: Path = BASE_DIR / "data" / "menu.csv"
    # ИИ-консультант включается, только если задан ключ Anthropic
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")


config = Config()
