#!/usr/bin/env python3
"""
Мониторинг PayGame — аккаунты Genshin Impact.

Работает через публичный JSON-API площадки:
`https://api.paygame.ru/api/v1/offers/item-offer/` с курсорной
пагинацией (параметр `next` в ответе — base64-токен checkpoint-а).

Это даёт:
- полный обход витрины за ~10 запросов (size=100, ~1000 лотов),
  вместо 25 штук из первой страницы JSON-LD;
- готовые структурированные поля — без ручного парсинга CSS / JSON-LD:
  price (₽), game_server, props_data[AR / Неролл / Смена данных / Леги],
  created_date, last_raised, seller.username, title.

Фильтрация — та же, что для FunPay (см. genshin_monitor.py).
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import time
import urllib.parse
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    CAT1_AR_MAX,
    CAT1_AR_MIN,
    CAT1_MAX_PRICE,
    CAT1_MIN_PRICE,
    CAT2_AR_MAX,
    CAT2_AR_MIN,
    CAT2_MAX_PRICE,
    CAT2_MIN_PRICE,
    fetch_json,
    has_event_5star,
    is_europe,
    is_garbage,
    load_seen,
    prune_seen,
    save_seen,
    update_seen,
)

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "paygame_seen.json"
)

API_BASE = "https://api.paygame.ru/api/v1/offers/item-offer/"
API_QUERY_BASE = "game=genshin-impact&type=account&size=100"
# Жёсткий потолок страниц — страховка от бесконечной петли, если
# API однажды сломает пагинацию. 50 страниц × 100 = 5000 лотов,
# при реальных ~1000 лотов это заведомо достаточно.
MAX_PAGES = 50


def _api_url(cursor: str | None = None) -> str:
    base = f"{API_BASE}?{API_QUERY_BASE}"
    if cursor:
        return f"{base}&cursor={urllib.parse.quote(cursor)}"
    return base


def _extract_prop(props: list[dict[str, Any]], name: str) -> Any:
    """Вытащить из props_data значение проп-а с именем `name`."""
    for p in props or []:
        prop = p.get("prop") or {}
        if prop.get("name") == name:
            v = p.get("val")
            if isinstance(v, dict):
                # int_value приоритетнее — это "честное" число
                iv = v.get("int_value")
                if iv is not None:
                    return iv
                return v.get("value")
            return v
    return None


def _parse_iso(ts: str | None) -> int | None:
    """ISO-8601 → unix timestamp (секунды). None если не получилось."""
    if not ts:
        return None
    try:
        # Python 3.11 умеет `fromisoformat` с 'Z' и миллисекундами.
        # Для совместимости с 3.10 подменяем финальный 'Z' на '+00:00'.
        s = ts.replace("Z", "+00:00")
        return int(_dt.datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


def fetch_all_offers(max_pages: int = MAX_PAGES) -> tuple[list[dict[str, Any]], bool]:
    """
    Обойти витрину PayGame через cursor-API. Возвращает
    (все_найденные_результаты, успех_прохода). При ошибке сети на
    первой же странице возвращает ([], False); если падение произошло
    посередине — отдаёт что успели набрать + `False`, чтобы вызывающий
    знал, что данные неполные.
    """
    results: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_ids: set[int] = set()
    for page in range(max_pages):
        url = _api_url(cursor)
        data = fetch_json(url)
        if not isinstance(data, dict):
            return results, False
        page_results = data.get("results") or []
        if not page_results:
            break
        for it in page_results:
            iid = it.get("id")
            if iid in seen_ids:
                # страховка от зацикливания на одном курсоре
                continue
            seen_ids.add(iid)
            results.append(it)
        nxt = data.get("next")
        if not nxt:
            break
        # API возвращает `next` как чистый токен-курсор (base64),
        # но иногда (в других эндпоинтах) приходит полная ссылка.
        if isinstance(nxt, str) and nxt.startswith("http"):
            # не поддерживаем — чтобы не ходить наружу.
            return results, True
        cursor = nxt
    return results, True


def parse_paygame(items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """
    Преобразовать ответ API в плоский список, пригодный для categorize().
    Если items не передан — сходить в API и собрать всё.
    """
    if items is None:
        items, _ok = fetch_all_offers()

    out: list[dict[str, Any]] = []
    for r in items:
        iid = r.get("id")
        if iid is None:
            continue
        iid = str(iid)

        servers = [s.get("title") for s in (r.get("game_server") or []) if s]
        server = servers[0] if servers else ""

        ar_raw = _extract_prop(r.get("props_data"), "AR")
        ar: int | None = None
        try:
            if ar_raw is not None:
                ar = int(ar_raw)
        except Exception:
            ar = None

        neroll_raw = _extract_prop(r.get("props_data"), "Неролл")
        neroll = str(neroll_raw or "").strip().lower() in ("да", "yes", "true")

        title = (r.get("title") or "").strip()

        price_raw = r.get("price")
        price_rub: float | None = None
        try:
            if price_raw is not None:
                price_rub = float(price_raw)
        except Exception:
            price_rub = None

        created_ts = _parse_iso(r.get("created_date"))
        raised_ts = _parse_iso(r.get("last_raised"))

        seller = ((r.get("seller") or {}).get("username") or "").strip() or "?"

        out.append({
            "source": "PayGame",
            "id": iid,
            "ar": ar,
            "server": server,
            "desc": title,
            "desc_full": title,
            "neroll": neroll,
            "price_orig": f"{price_raw} ₽" if price_raw is not None else "",
            "price_rub": price_rub,
            "seller": seller,
            "url": f"https://paygame.ru/offers/{iid}",
            "_created_ts": created_ts,
            "_raised_ts": raised_ts,
        })
    return out


def _passes_common(a: dict[str, Any]) -> bool:
    if not is_europe(a.get("server", "")):
        return False
    if a.get("neroll"):
        return False
    if is_garbage(a.get("desc_full") or a.get("desc", "")):
        return False
    if a.get("price_rub") is None:
        return False
    return True


def categorize(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cat1: list[dict[str, Any]] = []
    cat2: list[dict[str, Any]] = []
    for a in items:
        if not _passes_common(a):
            continue
        ar = a.get("ar")
        price = a.get("price_rub")
        if ar is None or price is None:
            continue

        if CAT1_AR_MIN <= ar <= CAT1_AR_MAX and CAT1_MIN_PRICE <= price <= CAT1_MAX_PRICE:
            cat1.append(a)

        if CAT2_AR_MIN <= ar <= CAT2_AR_MAX and CAT2_MIN_PRICE <= price <= CAT2_MAX_PRICE:
            text = a.get("desc_full") or a.get("desc", "")
            if not has_event_5star(text):
                continue
            cat2.append(a)

    cat1.sort(key=lambda x: x.get("price_rub") or float("inf"))
    cat2.sort(key=lambda x: x.get("price_rub") or float("inf"))
    return cat1, cat2


def run(reset: bool = False) -> dict[str, Any]:
    raw_api, ok = fetch_all_offers()
    raw = parse_paygame(raw_api)
    cat1, cat2 = categorize(raw)

    if reset and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    seen_before = load_seen(STATE_FILE)
    first_run = (
        not os.path.exists(STATE_FILE)
        or (not seen_before.get("cat1") and not seen_before.get("cat2"))
    )

    seen = seen_before
    pruned = prune_seen(seen)
    new_cat1, drop_cat1 = update_seen(seen, "cat1", cat1)
    new_cat2, drop_cat2 = update_seen(seen, "cat2", cat2)
    save_seen(STATE_FILE, seen)

    if first_run:
        new_cat1, new_cat2 = [], []
        drop_cat1, drop_cat2 = [], []

    # Новые — сортируем по created_date DESC (самые свежие сверху),
    # а если timestamp-а нет — по first_seen (обратной совместимостью).
    def _sort_key_new(x: dict[str, Any]) -> int:
        return -int(x.get("_created_ts") or x.get("_first_seen_ts") or 0)

    new_cat1.sort(key=_sort_key_new)
    new_cat2.sort(key=_sort_key_new)
    drop_cat1.sort(key=lambda x: -float(x.get("_drop_pct", 0) or 0))
    drop_cat2.sort(key=lambda x: -float(x.get("_drop_pct", 0) or 0))

    return {
        "source": "PayGame",
        "total_raw": len(raw),
        "cat1": cat1,
        "cat2": cat2,
        "new_cat1": new_cat1,
        "new_cat2": new_cat2,
        "drop_cat1": drop_cat1,
        "drop_cat2": drop_cat2,
        "first_run": first_run,
        "pruned": pruned,
        "ok": ok and len(raw) > 0,
    }


def _format_report(data: dict[str, Any]) -> str:
    lines = ["--- PAYGAME MONITOR ---"]
    if data["first_run"]:
        lines.append(
            f"[init] Первый запуск — сохранили {len(data['cat1'])} + {len(data['cat2'])} "
            "лотов в seen. Новые/подешевевшие покажутся со следующего запуска."
        )
    else:
        for cat_key, title, cat in (
            ("cat1", f"AR {CAT1_AR_MIN}-{CAT1_AR_MAX}, {int(CAT1_MIN_PRICE)}-{int(CAT1_MAX_PRICE)}₽, Европа", data["cat1"]),
            ("cat2", f"AR {CAT2_AR_MIN}-{CAT2_AR_MAX}, {int(CAT2_MIN_PRICE)}-{int(CAT2_MAX_PRICE)}₽, Европа, с ивентовыми 5★", data["cat2"]),
        ):
            new_items = data[f"new_{cat_key}"]
            drops = data[f"drop_{cat_key}"]
            lines.append(f"\n🔎 {title} — всего {len(cat)}, новых {len(new_items)}, подешевевших {len(drops)}")
            if new_items:
                lines.append("  [NEW]")
                for i, a in enumerate(new_items[:25]):
                    ar = a.get("ar", "?")
                    ts = a.get("_created_ts")
                    ago = ""
                    if ts:
                        delta = max(0, int(time.time()) - int(ts))
                        if delta < 3600:
                            ago = f" [{delta // 60}м назад]"
                        elif delta < 86400:
                            ago = f" [{delta // 3600}ч назад]"
                        else:
                            ago = f" [{delta // 86400}д назад]"
                    lines.append(
                        f"    {i+1}. AR{ar} | {a['price_rub']:.0f}₽{ago} | {a.get('server','?')}"
                    )
                    lines.append(f"       {a.get('desc','')[:120]}")
                    lines.append(f"       🔗 {a['url']}")
            if drops:
                lines.append("  [↓ цена упала]")
                for i, a in enumerate(drops[:25]):
                    lines.append(
                        f"    {i+1}. AR{a.get('ar','?')} | {a['price_rub']:.0f}₽"
                        f" (было {a['_prev_price']:.0f}₽, −{a['_drop_pct']}%)"
                    )
                    lines.append(f"       {a.get('desc','')[:120]}")
                    lines.append(f"       🔗 {a['url']}")
            if not new_items and not drops:
                lines.append("  ничего нового")
    lines.append(
        f"\n📊 Всего на PayGame (сырьё): {data['total_raw']}, "
        f"в фильтрах: {len(data['cat1'])}+{len(data['cat2'])}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="PayGame monitor — Genshin accounts")
    p.add_argument("--reset", action="store_true", help="очистить seen и начать сначала")
    args = p.parse_args(argv)

    data = run(reset=args.reset)
    print(_format_report(data))
    return 0 if data.get("ok") else 2


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
