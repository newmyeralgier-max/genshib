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
from common import EVENT_5STAR  # noqa: E402

# Тип отпечатка. ar_bucket=None для лотов с неизвестным AR.
Fingerprint = tuple[int | None, frozenset[str], int]


def fingerprint(item: dict[str, Any]) -> Fingerprint:
    """Считать отпечаток одного лота. См. модуль-доку."""
    ar = item.get("ar")
    bucket: int | None = None
    if isinstance(ar, int):
        # //5 * 5: 50→50, 51→50, 54→50, 55→55, 59→55, 60→60.
        bucket = (ar // 5) * 5

    text = (item.get("desc_full") or item.get("desc") or "").lower()
    raw = [name for name in EVENT_5STAR if name in text]
    # Дедуп: если матч-имя X — собственный подстрочный кусок другого
    # матч-имени Y (например, «фурин» внутри «фурина», «ке цин»
    # внутри «ке цинн» и т.п.), оставляем только Y. Иначе один и
    # тот же персонаж даёт +2 к множеству и портит jaccard.
    raw_set = set(raw)
    deduped: set[str] = set()
    for x in raw_set:
        if any(x != y and x in y for y in raw_set):
            continue  # X — подстрока какого-то Y, X отбрасываем
        deduped.add(x)
    chars = frozenset(deduped)
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
