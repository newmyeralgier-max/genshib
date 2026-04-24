"""
Общие утилиты для genshib-мониторов.

- Списки стандартных / ивентовых 5★ персонажей.
- Словарь стоп-слов (договорная, под заказ и т.п.).
- Загрузка / сохранение state-файла с форматом
  {id: {price, first_seen, last_seen}} + обратная совместимость
  со старым форматом {id: price}.
- Обёртка над urllib с gzip и edge-case'ами FunPay/PayGame.
- Конвертация валюты в рубли.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.request
from typing import Any


# --- валюта ---------------------------------------------------------
# FunPay периодически отдаёт цены то в EUR, то в USD.
# Пользователь просил строго отображение в рублях.
# Курсы вынесены в одно место; можно переопределить через env-vars.
EUR_TO_RUB = float(os.environ.get("GENSHIB_EUR_RUB", "105"))
USD_TO_RUB = float(os.environ.get("GENSHIB_USD_RUB", "95"))


# --- 5★ персонажи ---------------------------------------------------
STANDARD_5STAR = {
    "дилюк", "diluc", "джинн", "jean", "кэ цин", "кэцин", "ке цин", "кецин",
    "keqing", "мона", "mona", "цици", "ци ци", "qiqi", "тигнари", "tighnari",
    "дехья", "dehya", "мидзуки", "midzuki", "мидуки",
}

EVENT_5STAR = {
    "венди", "венти", "venti", "эола", "еола", "eula", "кадзуха", "kazuha",
    "чжун ли", "чжунли", "zhongli", "гань юй", "ганьюй", "ganyu", "сяо", "xiao",
    "ху тао", "хутао", "hu tao", "йоимия", "ёимия", "yoimiya",
    "шэнь хэ", "шэньхэ", "шень хэ", "шеньхэ", "shenhe",
    "аяка", "ayaka", "камисато", "райдэн", "райден", "raiden",
    "аято", "ayato", "итто", "itto", "кокоми", "kokomi",
    "яэ мико", "яэмико", "ямико", "yae miko",
    "нахида", "nahida", "сайно", "cyno",
    "вандерер", "wanderer", "скиталец", "странник", "альхаисам", "alhaitham",
    "фурина", "furina", "фурин", "нёвиллет", "neuvillette", "невиллет",
    "навия", "navia", "клоринда", "clorinde", "сигвин", "sigewinne",
    "ризли", "рисли", "wriothesley", "линей", "lyney", "фремине", "freminet",
    "муалани", "mualani", "кинич", "kinich", "часка", "chasca",
    "мавуика", "mavuika", "ситлали", "citlali", "шилонен", "xilonen",
    "арлекино", "arlecchino",
    "тарталья", "tartaglia", "чайлд", "childe", "альбедо", "albedo",
    "нилу", "nilou",
    "эмилия", "emilie", "коломбина", "columbina", "инеффа", "иннефа",
    "эскофье", "эскоф", "escoffier",
    "тиори", "chiori", "скирк", "skirk",
    "е лань", "елань", "yelan",
    "линнея", "линея", "linnea",
    "дурин", "durin",
    "флинс", "flins",
    "лаум", "laum",
}

# --- стоп-слова мусора ----------------------------------------------
# Эти фразы встречаются в "договорная цена" заглушках (2₽),
# сервисах фарма / прокачки, услугах и т.п.
GARBAGE_PHRASES = (
    "договорн",           # "ДОГОВОРНАЯ ЦЕНА", "договорная"
    "под заказ", "на заказ",
    "фарм",               # услуга фарма
    "прокач ак",          # услуга прокачки
    "буст ",              # буст абиссa и т.п.
    "услуг",              # "услуги прокачки"
    "сборк",              # "сборка аккаунта"
)

# --- минимальная / максимальная цена для сегментов ------------------
# cat1 — AR 50-60, "дорогой" сегмент, фильтр по верхнему пределу ≤ 3000₽.
# cat2 — AR 0-20, "стартовый" сегмент, фильтр ≤ 700₽.
# Минимум 50₽ отсекает "договорная 1-2₽" заглушки в обеих категориях.
CAT1_MIN_PRICE = 50.0
CAT1_MAX_PRICE = 3000.0
CAT1_AR_MIN, CAT1_AR_MAX = 50, 60

CAT2_MIN_PRICE = 50.0
CAT2_MAX_PRICE = 700.0
CAT2_AR_MIN, CAT2_AR_MAX = 0, 20

# какой сервер считать "Европой"
EUROPE_TOKENS = ("европ", "europe", "eu ", " eu", "eu,")


# --- фильтры --------------------------------------------------------
def has_event_5star(text: str) -> bool:
    t = (text or "").lower()
    return any(name in t for name in EVENT_5STAR)


def has_standard_5star(text: str) -> bool:
    t = (text or "").lower()
    return any(name in t for name in STANDARD_5STAR)


def is_garbage(desc: str) -> bool:
    t = (desc or "").lower()
    return any(p in t for p in GARBAGE_PHRASES)


def is_europe(server: str) -> bool:
    s = (server or "").lower().strip()
    if not s:
        return False
    return any(tok in s for tok in EUROPE_TOKENS)


def price_to_rub(price_num: float, currency: str) -> float | None:
    if price_num is None:
        return None
    c = (currency or "").strip().lower()
    if c in ("₽", "rub", "руб", "руб.", "р", "р."):
        return float(price_num)
    if c == "€" or "eur" in c:
        return float(price_num) * EUR_TO_RUB
    if c == "$" or "usd" in c:
        return float(price_num) * USD_TO_RUB
    return None


# --- HTTP fetch -----------------------------------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
}


def fetch(url: str, timeout: int = 30) -> str:
    """Скачать страницу, вернуть decoded HTML. На ошибке вернуть "" и напечатать."""
    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if enc == "gzip":
            data = gzip.decompress(data)
        elif enc == "br":
            try:
                import brotli  # type: ignore
                data = brotli.decompress(data)
            except Exception:
                pass
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[fetch] ошибка {url}: {e}")
        return ""


# --- seen state -----------------------------------------------------
# Новый формат:
#   {"cat1": {"id": {"price": 123.0, "first_seen": 17..., "last_seen": 17...}},
#    "cat2": {...}}
# Старый формат (обратная совместимость):
#   {"cat1": {"id": 123.0}, "cat2": {...}}
def load_seen(path: str) -> dict[str, dict[str, dict[str, Any]]]:
    if not os.path.exists(path):
        return {"cat1": {}, "cat2": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {"cat1": {}, "cat2": {}}

    out: dict[str, dict[str, dict[str, Any]]] = {"cat1": {}, "cat2": {}}
    for cat in ("cat1", "cat2"):
        src = raw.get(cat, {}) or {}
        for k, v in src.items():
            if isinstance(v, dict):
                out[cat][k] = {
                    "price": v.get("price"),
                    "first_seen": v.get("first_seen"),
                    "last_seen": v.get("last_seen"),
                }
            else:
                # legacy: было просто число-цена
                out[cat][k] = {
                    "price": v,
                    "first_seen": None,
                    "last_seen": None,
                }
    return out


def save_seen(path: str, seen: dict[str, dict[str, dict[str, Any]]]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[seen] ошибка сохранения {path}: {e}")


def update_seen(
    seen: dict[str, dict[str, dict[str, Any]]],
    cat: str,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Обновить seen по текущему срезу.

    Возвращает (new_items, price_drop_items):
      - new_items: id, которых не было в seen
      - price_drop_items: id с ценой ниже прошлого раза (с доп. полем
        "_prev_price" и "_drop_pct")
    """
    now = int(time.time())
    bucket = seen.setdefault(cat, {})
    new_items: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []
    for it in items:
        iid = it.get("id")
        if not iid:
            continue
        price = it.get("price_rub")
        prev = bucket.get(iid)
        if prev is None:
            bucket[iid] = {"price": price, "first_seen": now, "last_seen": now}
            new_items.append(it)
        else:
            prev_price = prev.get("price")
            if (
                price is not None
                and prev_price is not None
                and isinstance(prev_price, (int, float))
                and price < float(prev_price) * 0.95  # >5% падения
            ):
                it2 = dict(it)
                it2["_prev_price"] = float(prev_price)
                it2["_drop_pct"] = round(
                    (1 - price / float(prev_price)) * 100.0, 1
                )
                drops.append(it2)
            prev["price"] = price
            prev["last_seen"] = now
            if prev.get("first_seen") is None:
                prev["first_seen"] = now
    return new_items, drops


__all__ = [
    "EUR_TO_RUB",
    "USD_TO_RUB",
    "STANDARD_5STAR",
    "EVENT_5STAR",
    "GARBAGE_PHRASES",
    "CAT1_MIN_PRICE",
    "CAT1_MAX_PRICE",
    "CAT1_AR_MIN",
    "CAT1_AR_MAX",
    "CAT2_MIN_PRICE",
    "CAT2_MAX_PRICE",
    "CAT2_AR_MIN",
    "CAT2_AR_MAX",
    "EUROPE_TOKENS",
    "has_event_5star",
    "has_standard_5star",
    "is_garbage",
    "is_europe",
    "price_to_rub",
    "fetch",
    "load_seen",
    "save_seen",
    "update_seen",
]
