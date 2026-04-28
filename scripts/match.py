"""
Утилиты для сопоставления лотов: «отпечаток» (fingerprint) + jaccard.

Используется в:
- scripts/monitor_all.py — кросс-сорс матчинг и дедуп между сайтами;
- scripts/monitor_all.py — медианы по классу лота (см. discount_pct).

Принцип:
  fingerprint(item) -> (ar_bucket, frozenset_event_5stars, count_event_5)

  ar_bucket — округление AR вниз до 5 (50, 55, 60). Это и есть «класс
    лота по AR» — на 1-2 ранга разница между лотами не существенна, а
    точное равенство AR слишком жёсткое.
  event_5stars — пересечение описания с EVENT_5STAR из common.py.
    Берём именно frozenset, чтобы можно было класть в словари /
    использовать в jaccard.
  count_event_5 — мощность множества; полезна для классификации лота
    «насколько он насыщенный».

similar()/jaccard() — простые помощники для матчинга.
"""
from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import EVENT_5STAR, EVENT_5STAR_CANONICAL  # noqa: E402

# Тип отпечатка. ar_bucket=None для лотов с неизвестным AR.
Fingerprint = tuple[int | None, frozenset[str], int]


def fingerprint(item: dict[str, Any]) -> Fingerprint:
    """Считать отпечаток одного лота. См. модуль-доку.

    Канонизация: каждый матч-алиас (e.g. «skirk», «фурин») заменяется на
    каноническое имя своей группы из EVENT_5STAR_GROUPS. Это гарантирует,
    что «Скирк (skirk)» и «Фурина = фурин» считаются одним персонажем
    (а не двумя), что важно и для медиан, и для jaccard-сравнения.
    """
    ar = item.get("ar")
    bucket: int | None = None
    # bool — подкласс int, явно фильтруем.
    if isinstance(ar, (int, float)) and not isinstance(ar, bool):
        # //5 * 5: 50→50, 51→50, 54→50, 55→55, 59→55, 60→60.
        bucket = (int(ar) // 5) * 5

    text = (item.get("desc_full") or item.get("desc") or "").lower()
    canonicals = {
        EVENT_5STAR_CANONICAL[name]
        for name in EVENT_5STAR
        if name in text
    }
    chars = frozenset(canonicals)
    return bucket, chars, len(chars)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """
    Жаккар двух множеств: |A ∩ B| / |A ∪ B|.

    Edge cases:
    - оба пусты → 0.0 (не хотим считать «совпадает», когда оба «без героев»);
    - один пуст, второй нет → 0.0.
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def similar(left: dict[str, Any], right: dict[str, Any], *, min_jaccard: float = 0.5) -> bool:
    """
    Грубая «похожесть» двух лотов:
    - один и тот же AR-бакет (или у обоих unknown, что редко полезно);
    - оба имеют ≥1 ивентовый 5★ (иначе jaccard == 0);
    - jaccard состава ≥ порога (по умолчанию 0.5 — половина персонажей общие).
    """
    al, cl, _ = fingerprint(left)
    ar, cr, _ = fingerprint(right)
    if al != ar:
        return False
    if min(len(cl), len(cr)) == 0:
        return False
    return jaccard(cl, cr) >= min_jaccard


# --- Глава 2: медианы по классу лота + discount_pct ----------------

# Кап «насыщенности» по числу ивентовых 5★. Лоты с ≥4 5★ кладём в один
# класс, иначе хвост распадается на классы с 1-2 наблюдениями.
_MAX_EVENT_5_BUCKET = 4

# Минимум наблюдений в классе, чтобы доверять медиане. Меньше — NULL.
DEFAULT_MIN_SAMPLE = 5


def market_class(item: dict[str, Any]) -> tuple[int | None, int]:
    """
    «Класс лота» для подсчёта медианы рынка: (ar_bucket, n_event_capped).
    Лоты без AR попадают в класс (None, n) — у них своя медиана.
    """
    bucket, _, n = fingerprint(item)
    return bucket, min(n, _MAX_EVENT_5_BUCKET)


def fp_median_by_class(
    fp_items: list[dict[str, Any]],
    *,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> dict[tuple[int | None, int], float]:
    """
    Медиана FunPay-цены по классу лота (см. market_class).

    Считаем именно по FunPay, потому что FunPay — это та цена, по
    которой лот **продаётся**, и она есть baseline арбитражной
    наценки. Считать медиану по объединению источников некорректно
    (мы и так знаем, что PayGame дешевле).

    Классы с числом наблюдений < min_sample выкидываем — медиана по
    1-2 точкам не информативна.
    """
    from statistics import median
    buckets: dict[tuple[int | None, int], list[float]] = {}
    for it in fp_items:
        price = it.get("price_rub")
        if price is None:
            continue
        try:
            p = float(price)
        except (TypeError, ValueError):
            continue
        cls = market_class(it)
        buckets.setdefault(cls, []).append(p)
    return {k: float(median(v)) for k, v in buckets.items() if len(v) >= min_sample}


def discount_pct(
    item: dict[str, Any],
    medians: dict[tuple[int | None, int], float],
) -> float | None:
    """
    Скидка лота относительно медианы своего класса, в процентах.
    Положительное число — лот дешевле медианы, отрицательное — дороже.

    Возвращает None, если медианы для класса нет (мало сэмплов) или у
    лота нет цены.
    """
    cls = market_class(item)
    m = medians.get(cls)
    if m is None or m <= 0:
        return None
    price = item.get("price_rub")
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    return round((1 - p / m) * 100, 1)


# --- Глава 1: кросс-сорс матчинг -----------------------------------

def build_matches(
    src: list[dict[str, Any]],
    other: list[dict[str, Any]],
    *,
    top_k: int = 3,
    min_jaccard: float = 0.5,
) -> dict[str, list[dict[str, Any]]]:
    """
    Для каждого лота из `src` найти top_k самых похожих лотов в `other`.

    Возвращает {src_id: [other_lot, ...]}.

    Сортировка кандидатов: jaccard DESC, потом цена ASC (если price_rub
    есть). Лот без AR-бакета или без ивентовых 5★ — не в `src` (просто
    skip-аем такие).
    """
    if not src or not other:
        return {}

    # Индекс «по AR-бакету» — n×m → n×k (k мало внутри одного бакета).
    by_bucket: dict[int | None, list[tuple[dict[str, Any], frozenset[str]]]] = {}
    for o in other:
        b, chars, n = fingerprint(o)
        if b is None or n == 0:
            continue
        by_bucket.setdefault(b, []).append((o, chars))

    out: dict[str, list[dict[str, Any]]] = {}
    for s in src:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        sb, schars, sn = fingerprint(s)
        if sb is None or sn == 0:
            continue
        cands = by_bucket.get(sb) or []
        scored: list[tuple[float, float, dict[str, Any]]] = []
        for o, ochars in cands:
            j = jaccard(schars, ochars)
            if j < min_jaccard:
                continue
            price = o.get("price_rub")
            try:
                p = float(price) if price is not None else float("inf")
            except (TypeError, ValueError):
                p = float("inf")
            scored.append((-j, p, o))
        if not scored:
            continue
        scored.sort(key=lambda x: (x[0], x[1]))
        out[sid] = [
            {**o, "_jaccard": -score} for (score, _p, o) in scored[:top_k]
        ]
    return out


# --- Глава 10: дедуп между источниками -----------------------------

# Дефолты для дедупа. Подобраны эмпирически:
# - max_price_diff_ratio=0.15 — цены различаются ≤ 15% (дороже/дешевле);
# - jaccard_strong=0.85 — почти полное совпадение состава 5★ при разных
#   селлерах (когда совпадает селлер — порог ниже, см. is_dup);
# - jaccard_with_seller=0.7 — порог при совпадении ника продавца.
DEFAULT_DUP_PRICE_DIFF = 0.15
DEFAULT_DUP_JACCARD_STRONG = 0.85
DEFAULT_DUP_JACCARD_WITH_SELLER = 0.7


def _norm_seller(s: object) -> str:
    return (str(s) or "").strip().lower()


def is_dup(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    max_price_diff_ratio: float = DEFAULT_DUP_PRICE_DIFF,
    jaccard_strong: float = DEFAULT_DUP_JACCARD_STRONG,
    jaccard_with_seller: float = DEFAULT_DUP_JACCARD_WITH_SELLER,
) -> bool:
    """
    Считаем два лота дублями, если они с разных источников и:
    - тот же AR-бакет;
    - цены различаются не более чем на max_price_diff_ratio;
    - jaccard 5★ ≥ jaccard_with_seller И селлеры совпадают,
      ИЛИ jaccard 5★ ≥ jaccard_strong (когда селлеры разные).
    """
    if a.get("source") and a.get("source") == b.get("source"):
        return False
    al, ach, an = fingerprint(a)
    bl, bch, bn = fingerprint(b)
    if al is None or bl is None or al != bl:
        return False
    if an == 0 or bn == 0:
        return False
    pa = a.get("price_rub")
    pb = b.get("price_rub")
    if not isinstance(pa, (int, float)) or not isinstance(pb, (int, float)):
        return False
    if pa <= 0 or pb <= 0:
        return False
    if abs(pa - pb) / max(pa, pb) > max_price_diff_ratio:
        return False
    j = jaccard(ach, bch)
    sa, sb = _norm_seller(a.get("seller")), _norm_seller(b.get("seller"))
    if sa and sa == sb:
        return j >= jaccard_with_seller
    return j >= jaccard_strong


def find_cross_source_dups(
    fp_items: list[dict[str, Any]],
    pg_items: list[dict[str, Any]],
    **kwargs: Any,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Найти все пары (fp_lot, pg_lot), которые is_dup() считает дублями.
    """
    if not fp_items or not pg_items:
        return []
    # Индексируем PG по AR-бакету для O(n*k) вместо O(n*m).
    by_bucket: dict[int | None, list[dict[str, Any]]] = {}
    for p in pg_items:
        b, _, n = fingerprint(p)
        if b is None or n == 0:
            continue
        by_bucket.setdefault(b, []).append(p)
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for f in fp_items:
        b, _, n = fingerprint(f)
        if b is None or n == 0:
            continue
        for p in by_bucket.get(b, []):
            if is_dup(f, p, **kwargs):
                out.append((f, p))
    return out
