"""Меню кофейни: загрузка из data/menu.csv (точная копия Google Таблицы с фото меню)."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import time
from typing import Dict, List, Optional, Tuple

from .config import config
from .content import POINT_BY_NAME, POINTS

ADDON_SECTIONS = {
    "eggs": "Кондименты к яйцам",
    "pancakes": "Кондименты к блинам",
    "drinks": "Добавки к напиткам",
}
# К каким разделам какие добавки предлагать
ADDONS_FOR_SECTION = {
    "Сеты": "eggs", "Яйца": "eggs", "Завтрак": "eggs",
    "Блины": "pancakes",
}
MILK_OPTIONS = ["коровье", "банановое", "кокосовое", "миндальное"]


@dataclass
class Variant:
    label: str
    price: int


@dataclass
class Item:
    idx: int
    point: str          # код точки: M / O / L
    kind: str           # блюдо / напиток / добавка
    section: str
    name: str
    size: str
    price_raw: str
    kbju: str
    tags: str
    serve: str          # время подачи как в меню
    variants: List[Variant] = field(default_factory=list)

    @property
    def is_addon(self) -> bool:
        return self.kind == "добавка" and self.section != "Хлеб"

    @property
    def window(self) -> Tuple[time, time]:
        """Часы, в которые позицию можно получить."""
        p = POINTS[self.point]
        if "12:00" in self.serve:
            start, end = p["kitchen"]
        else:
            start, end = p["open"], p["close"]
        return _t(start), _t(end)

    @property
    def options(self) -> List[str]:
        """Бесплатные опции без изменения цены."""
        if "гор./хол." in self.tags:
            return ["горячий", "холодный"]
        if "молоко на выбор" in self.tags:
            return [f"молоко {m}" for m in MILK_OPTIONS]
        return []

    @property
    def addon_group(self) -> Optional[str]:
        if self.kind == "напиток":
            return "drinks"
        return ADDONS_FOR_SECTION.get(self.section)

    def card(self) -> str:
        lines = [f"<b>{self.name}</b>"]
        if len(self.variants) > 1:
            lines.append(" · ".join(f"{v.label} — {v.price} ₽" for v in self.variants))
        else:
            lines.append(f"{self.size + ' · ' if self.size else ''}{self.variants[0].price} ₽")
        if self.kbju:
            lines.append(f"КБЖУ на порцию: {self.kbju}")
        extra = [t for t in (self.tags,) if t]
        if extra:
            lines.append("🏷 " + ", ".join(extra))
        if "12:00" in self.serve:
            lines.append(f"🕛 Готовим {self.serve}")
        return "\n".join(lines)


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _variants(name: str, size: str, price: str) -> List[Variant]:
    prices = [int(p.strip()) for p in price.split("/")]
    if len(prices) == 1:
        return [Variant(size or "", prices[0])]
    unit = size.split()[-1] if size else ""
    sizes = [s.strip() for s in size.replace(unit, "").split("/")] if size else []
    if len(sizes) == len(prices):
        return [Variant(f"{s} {unit}".strip(), p) for s, p in zip(sizes, prices)]
    names = [n.strip() for n in name.split("/")]
    if len(names) == len(prices):
        return [Variant(n, p) for n, p in zip(names, prices)]
    return [Variant(f"вариант {i + 1}", p) for i, p in enumerate(prices)]


def load_menu() -> List[Item]:
    items: List[Item] = []
    with open(config.menu_path, encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f)):
            it = Item(
                idx=i,
                point=POINT_BY_NAME[row["Точка"]],
                kind=row["Тип"],
                section=row["Раздел"],
                name=row["Позиция"],
                size=row["Объём / вес"],
                price_raw=row["Цена, ₽"],
                kbju=row["КБЖУ на порцию (ккал/Б/Ж/У)"],
                tags=row["Метки"],
                serve=row["Время подачи"],
            )
            it.variants = _variants(it.name, it.size, it.price_raw)
            items.append(it)
    return items


MENU: List[Item] = load_menu()
BY_IDX: Dict[int, Item] = {it.idx: it for it in MENU}


def sections(point: str) -> List[str]:
    """Разделы точки в порядке меню (без добавок)."""
    seen: List[str] = []
    for it in MENU:
        if it.point == point and not it.is_addon and it.section not in seen:
            seen.append(it.section)
    return seen


def items_in(point: str, section: str) -> List[Item]:
    return [it for it in MENU if it.point == point and it.section == section]


def addons_for(item: Item) -> List[Item]:
    group = item.addon_group
    if not group:
        return []
    return items_in(item.point, ADDON_SECTIONS[group])


def menu_as_text() -> str:
    """Компактная выгрузка меню для ИИ-консультанта."""
    out: List[str] = []
    for code, p in POINTS.items():
        out.append(f"## {p['name']}")
        current = None
        for it in MENU:
            if it.point != code:
                continue
            if it.section != current:
                current = it.section
                out.append(f"### {current}")
            out.append(f"- {it.name} | {it.size} | {it.price_raw} ₽ | {it.tags} | {it.serve}".replace("|  |", "|"))
    return "\n".join(out)
