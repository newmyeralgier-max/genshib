"""
Общие утилиты для genshib-мониторов.

- Списки стандартных / ивентовых 5★ персонажей.
- Словарь стоп-слов (договорная, под заказ и т.п.).
- Загрузка / сохранение state-файла с форматом
  {id: {price, first_seen, last_seen}} + обратная совместимость
  со старым форматом {id: price}.
- Обёртка над urllib с gzip и ретраями.
- Конвертация валюты в рубли (оставлена как fallback; на практике
  и FunPay, и PayGame нам отдают прайсы сразу в ₽).
- Чистка устаревших seen-записей.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any


# --- валюта ---------------------------------------------------------
# Курсы оставлены как fallback-конверсия на случай, если сайт внезапно
# отдаст не-рублёвую цену. В нормальном режиме мы заставляем сайт
# отдавать ₽ напрямую (см. genshin_monitor.py / paygame_monitor.py).
EUR_TO_RUB = float(os.environ.get("GENSHIB_EUR_RUB", "105"))
USD_TO_RUB = float(os.environ.get("GENSHIB_USD_RUB", "95"))


# --- 5★ персонажи ---------------------------------------------------
# STANDARD_5STAR остался для исторической совместимости (пользователь
# когда-то просил отсекать "только стандартных" — эту роль теперь
# выполняет требование "есть ивентовый 5★" в cat2, см. categorize()).
STANDARD_5STAR = {
    "дилюк", "diluc", "джинн", "jean", "кэ цин", "кэцин", "ке цин", "кецин",
    "keqing", "мона", "mona", "цици", "ци ци", "qiqi", "тигнари", "tighnari",
    "дехья", "dehya", "мидзуки", "midzuki", "мидуки",
}

# --- Группы алиасов 5★ ивентов ----------------------------------------
# Источник правды — список групп. Каждая группа = все известные написания
# одного и того же персонажа (рус. русский транслит, англ.). Нужно для
# fingerprint в match.py: если в описании встречаются два разных написания
# одного персонажа («Скирк (skirk)»), мы должны считать это ОДНИМ героем,
# а не двумя — иначе ломается медиана рынка и jaccard-сравнение лотов.
#
# Канонический вариант для каждой группы — первый элемент группы (обычно
# короткое русское написание). Подмена на каноническое имя делается в
# match.fingerprint().
#
# ВАЖНО: «камисато» намеренно не включаем — это фамилия, ambiguous между
# Аякой и Аято; чтобы не выдать ложный +1 к составу.
EVENT_5STAR_GROUPS: tuple[tuple[str, ...], ...] = (
    ("венти", "венди", "venti"),
    ("эола", "еола", "eula"),
    ("кадзуха", "kazuha"),
    ("чжунли", "чжун ли", "zhongli"),
    ("ганьюй", "гань юй", "ganyu"),
    ("сяо", "xiao"),
    ("ху тао", "хутао", "hu tao"),
    ("йоимия", "ёимия", "yoimiya"),
    ("шэньхэ", "шэнь хэ", "шень хэ", "шеньхэ", "shenhe"),
    ("аяка", "ayaka"),
    ("райден", "райдэн", "raiden"),
    ("аято", "ayato"),
    ("итто", "itto"),
    ("кокоми", "kokomi"),
    ("яэ мико", "яэмико", "ямико", "yae miko"),
    ("нахида", "nahida"),
    ("сайно", "cyno"),
    ("вандерер", "wanderer", "скиталец", "странник"),
    ("альхаитам", "альхаисам", "alhaitham"),
    ("фурина", "furina", "фурин"),
    ("нёвиллет", "neuvillette", "невиллет"),
    ("навия", "navia"),
    ("клоринда", "clorinde"),
    ("сигвин", "sigewinne"),
    ("ризли", "рисли", "wriothesley"),
    ("линей", "lyney"),
    ("фремине", "freminet"),
    ("муалани", "mualani"),
    ("кинич", "kinich"),
    ("часка", "chasca"),
    ("мавуика", "mavuika"),
    ("ситлали", "citlali"),
    ("шилонен", "xilonen"),
    ("арлекино", "arlecchino"),
    ("тарталья", "tartaglia", "чайлд", "childe"),
    ("альбедо", "albedo"),
    ("нилу", "nilou"),
    ("эмилия", "emilie"),
    ("коломбина", "columbina", "инеффа", "иннефа"),
    ("эскофье", "эскоф", "escoffier"),
    ("тиори", "chiori"),
    ("скирк", "skirk"),
    ("елань", "е лань", "yelan"),
    ("линнея", "линея", "linnea"),
    ("дурин", "durin"),
    ("флинс", "flins"),
    ("лаум", "laum"),
)

# Плоский набор для has_event_5star() — back-compat.
EVENT_5STAR: frozenset[str] = frozenset(
    alias for group in EVENT_5STAR_GROUPS for alias in group
)

# alias → canonical_name (первое имя в группе).
EVENT_5STAR_CANONICAL: dict[str, str] = {
    alias: group[0] for group in EVENT_5STAR_GROUPS for alias in group
}

# --- стоп-слова мусора ----------------------------------------------
# Эти фразы встречаются в "договорная цена" заглушках (2₽),
# сервисах фарма / прокачки, услугах и т.п.
# Также ловим варианты "цена договорная / цена дог. / торг".
GARBAGE_PHRASES = (
    "договорн",           # "ДОГОВОРНАЯ ЦЕНА", "договорная"
    "цена дог",           # "цена договорная", "цена дог."
    "торг умест",         # "торг уместен"
    "под заказ", "на заказ",
    "фарм",               # услуга фарма
    "прокач ак",          # услуга прокачки
    "буст ",              # буст абиссa и т.п.
    "услуг",              # "услуги прокачки"
    "сборк",              # "сборка аккаунта"
    # --- «Куплю ваш аккаунт» и прочие байеры, маскирующиеся
    # под продавцов (выкладывают «виртуальный» лот с обратным
    # смыслом). В легитных описаниях продавцов "куплю/выкуп/
    # скуплю/обменяю" не встречаются — бить по субстроке
    # безопасно.
    "куплю",             # "Куплю ваш аккаунт!"
    "выкуп",             # "выкуп аккаунтов", "выкупаю"
    "скуп",              # "скуплю", "скупка"
    "продайте мне",      # реже, но бывает
    "обменяю",          # бартер-лоты
)

# --- чёрный список продавцов ---------------------------
# Ник продавца любого из источников (регистронезависимо).
# Расширяется через GENSHIB_SELLER_BLACKLIST=«a,b,c».
DEFAULT_SELLER_BLACKLIST = (
    "aurafarm",  # ритейл-фарма с искусственно заниженными ценами
)

_extra = os.environ.get("GENSHIB_SELLER_BLACKLIST", "").strip()
SELLER_BLACKLIST: frozenset[str] = frozenset(
    list(DEFAULT_SELLER_BLACKLIST)
    + [s.strip().lower() for s in _extra.split(",") if s.strip()]
)


def is_blacklisted_seller(seller: str | None) -> bool:
    if not seller:
        return False
    return seller.strip().lower() in SELLER_BLACKLIST

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

# какие значения считать "Европой" (точное совпадение после нормализации)
EUROPE_VALUES = {"европа", "europe", "eu"}


# --- фильтры --------------------------------------------------------
def has_event_5star(text: str) -> bool:
    t = (text or "").lower()
    return any(name in t for name in EVENT_5STAR)


def is_garbage(desc: str) -> bool:
    t = (desc or "").lower()
    return any(p in t for p in GARBAGE_PHRASES)


def is_europe(server: str) -> bool:
    """
    Строгое совпадение значения поля 'сервер'. Не подстрока —
    иначе "eu" подтягивало бы случайные подстроки в длинных названиях.
    """
    s = (server or "").strip().lower()
    if not s:
        return False
    # часто в HTML есть косые варианты "Европа (EU)" — дробим
    for token in re.split(r"[\s,()/|]+", s):
        if token in EUROPE_VALUES:
            return True
    return s in EUROPE_VALUES


def price_to_rub(price_num: float | None, currency: str) -> float | None:
    if price_num is None:
        return None
    c = (currency or "").strip().lower()
    if c in ("₽", "rub", "rur", "руб", "руб.", "р", "р."):
        return float(price_num)
    if c == "€" or "eur" in c:
        return float(price_num) * EUR_TO_RUB
    if c == "$" or "usd" in c:
        return float(price_num) * USD_TO_RUB
    return None


# --- HTTP fetch -----------------------------------------------------
# Accept-Encoding: gzip only — некоторые площадки отдают brotli,
# а питон stdlib его не расшифровывает, приходит мусор.
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
    # ВАЖНО: НЕ отправляем Accept-Language. FunPay с Accept-Language=ru
    # игнорирует ?currency=RUR и принудительно отдаёт цены в долларах
    # (видимо, по геолокации). Без этого заголовка ?currency=RUR работает
    # корректно и приходят рубли.
    "Accept-Encoding": "gzip",
}


def fetch(
    url: str,
    timeout: int = 30,
    *,
    retries: int = 2,
    retry_delay: float = 2.0,
    headers: dict[str, str] | None = None,
    accept_json: bool = False,
) -> str:
    """
    Скачать ресурс, вернуть decoded текст.

    Делает до `1 + retries` попыток с паузой `retry_delay` сек
    между ошибочными. На итоговом провале возвращает "" (а не None),
    чтобы вызывающий мог делать if not resp: ....
    """
    hdrs = dict(DEFAULT_HEADERS)
    if accept_json:
        hdrs["Accept"] = "application/json, */*;q=0.1"
    if headers:
        hdrs.update(headers)

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(retry_delay)
                continue
    print(f"[fetch] ошибка {url}: {last_err}")
    return ""


def fetch_json(url: str, timeout: int = 30, *, retries: int = 2) -> Any:
    """Скачать JSON. Возвращает распарсенное значение или None при ошибке."""
    body = fetch(url, timeout=timeout, retries=retries, accept_json=True)
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception as e:
        print(f"[fetch_json] не удалось распарсить {url}: {e}")
        return None


# --- seen state -----------------------------------------------------
# Поддерживаем три формата seen-файла, читаются прозрачно:
#   v1 (legacy):      {"cat1": {"id": 123.0}, "cat2": {...}}
#   v2:               {"cat1": {"id": {"price": 123.0, "first_seen": 17..., "last_seen": 17...}}, ...}
#   v3 (текущий):     то же что v2 + опц. поле "history": [{"ts": 17..., "price": 123.0}, ...]
# Запись всегда производится в формате v3.
SEEN_TTL_SECONDS = int(os.environ.get("GENSHIB_SEEN_TTL", str(14 * 24 * 3600)))

# Кап на длину истории. Не хочу, чтобы seen.json пухло на 1 МБ за месяц
# при 1000+ лотов.
SEEN_HISTORY_MAX = int(os.environ.get("GENSHIB_HISTORY_MAX", "50"))


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
                rec: dict[str, Any] = {
                    "price": v.get("price"),
                    "first_seen": v.get("first_seen"),
                    "last_seen": v.get("last_seen"),
                }
                # v2 → v3: если history нет, инициализируем её одной
                # точкой по last_seen. Это не идеально (мы не знаем
                # промежуточных цен), но даёт корректный «нижний край»
                # тренда от которого пойдут будущие точки.
                hist = v.get("history")
                if isinstance(hist, list):
                    rec["history"] = [
                        x for x in hist
                        if isinstance(x, dict)
                        and isinstance(x.get("ts"), int)
                    ][-SEEN_HISTORY_MAX:]
                elif rec.get("price") is not None and rec.get("last_seen") is not None:
                    rec["history"] = [
                        {"ts": int(rec["last_seen"]), "price": rec["price"]}
                    ]
                else:
                    rec["history"] = []
                out[cat][k] = rec
            else:
                # v1: было просто число-цена — никакой истории, никаких ts.
                out[cat][k] = {
                    "price": v,
                    "first_seen": None,
                    "last_seen": None,
                    "history": [],
                }
    return out


def save_seen(path: str, seen: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Атомарно сохранить seen-файл.

    Записываем во временный файл рядом с целевым, потом os.replace —
    это не оставляет частично записанного seen.json при крэше скрипта
    (на больших seen с историей запись неатомарна → если упасть в
    середине, файл побьётся и придётся делать --reset). os.replace
    атомарен на любых платформах, где запускается этот скрипт.
    """
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        tmp = os.path.join(d, f".{os.path.basename(path)}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[seen] ошибка сохранения {path}: {e}")
        # подчищаем мусор, если он остался
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def update_seen(
    seen: dict[str, dict[str, dict[str, Any]]],
    cat: str,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Обновить seen по текущему срезу.

    Возвращает (new_items, price_drop_items):
      - new_items: id, которых не было в seen (с дополнительным полем
        "_first_seen_ts" — unix-ts, когда лот впервые зарегистрирован).
      - price_drop_items: id с ценой ниже прошлого раза (с доп. полями
        "_prev_price" и "_drop_pct").

    Важно: если текущая цена не известна (price_rub=None), мы НЕ
    затираем прошлую цену — ждём следующего прогона.
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
            history: list[dict[str, Any]] = []
            if price is not None:
                history.append({"ts": now, "price": price})
            bucket[iid] = {
                "price": price,
                "first_seen": now,
                "last_seen": now,
                "history": history,
            }
            it2 = dict(it)
            it2["_first_seen_ts"] = now
            new_items.append(it2)
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
                # Прокидываем накопленную историю — пригодится в отчёте.
                hist_copy = list(prev.get("history") or [])
                if hist_copy:
                    it2["_history"] = hist_copy
                drops.append(it2)
            # цену обновляем только если она известна
            if price is not None:
                prev["price"] = price
                # дописать в history, если цена реально изменилась
                hist = prev.setdefault("history", [])
                last = hist[-1] if hist else None
                if last is None or last.get("price") != price:
                    hist.append({"ts": now, "price": price})
                    if len(hist) > SEEN_HISTORY_MAX:
                        # обрезаем по краю — самые старые точки уходят
                        del hist[: len(hist) - SEEN_HISTORY_MAX]
            prev["last_seen"] = now
            if prev.get("first_seen") is None:
                prev["first_seen"] = now
    return new_items, drops


def mark_disappeared(
    seen: dict[str, dict[str, dict[str, Any]]],
    cat: str,
    fresh_ids: set[str],
    *,
    grace_misses: int = 1,
    source_ok: bool = True,
) -> list[dict[str, Any]]:
    """
    Гл. 8: «исчезли = продали».

    Для каждого id в seen[cat]:
    - если id в fresh_ids → сбросить missed_runs=0;
    - иначе инкрементировать missed_runs. Если он превысил
      grace_misses (по умолч. 1 — т.е. на 2-м промахе) → считаем
      «лот ушёл» и удаляем из seen, возвращаем запись в список.

    source_ok=False (например, источник вернул ошибку или 0 лотов)
    → ничего не делаем: мы не уверены, что список реально пуст.
    Возвращаем пустой список, missed_runs не трогаем.
    """
    if not source_ok:
        return []
    bucket = seen.setdefault(cat, {})
    out: list[dict[str, Any]] = []
    for iid in list(bucket.keys()):
        rec = bucket[iid]
        if not isinstance(rec, dict):
            continue
        if iid in fresh_ids:
            if rec.get("missed_runs"):
                rec["missed_runs"] = 0
            continue
        misses = int(rec.get("missed_runs", 0)) + 1
        rec["missed_runs"] = misses
        if misses > grace_misses:
            out.append({"id": iid, **rec})
            del bucket[iid]
    return out


def prune_seen(
    seen: dict[str, dict[str, dict[str, Any]]],
    ttl_seconds: int = SEEN_TTL_SECONDS,
    now: int | None = None,
) -> int:
    """
    Выкинуть записи, last_seen которых старше ttl_seconds.

    Возвращает количество удалённых записей. У записей с last_seen=None
    TTL не применяется (legacy), а проставляется текущее время —
    на следующем прогоне они будут отсчитываться нормально.
    """
    if now is None:
        now = int(time.time())
    removed = 0
    for cat_bucket in seen.values():
        if not isinstance(cat_bucket, dict):
            continue
        for iid in list(cat_bucket.keys()):
            rec = cat_bucket[iid]
            ls = rec.get("last_seen") if isinstance(rec, dict) else None
            if ls is None:
                if isinstance(rec, dict):
                    rec["last_seen"] = now
                continue
            try:
                if now - int(ls) > ttl_seconds:
                    del cat_bucket[iid]
                    removed += 1
            except Exception:
                continue
    return removed


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
    "EUROPE_VALUES",
    "SEEN_TTL_SECONDS",
    "has_event_5star",
    "is_garbage",
    "is_europe",
    "price_to_rub",
    "fetch",
    "fetch_json",
    "load_seen",
    "save_seen",
    "update_seen",
    "prune_seen",
    "mark_disappeared",
]
