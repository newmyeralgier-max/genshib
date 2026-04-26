#!/usr/bin/env python3
"""
Мониторинг FunPay — аккаунты Genshin Impact.

Фильтрация:
- строго сервер Европа;
- цены забираем сразу в рублях (FunPay умеет отдавать ₽ через
  ?currency=RUR / Cookie `currency=RUR`);
- отсечка по ценам: cat1 (AR 50-60): 50-3000₽, cat2 (AR 0-20): 50-700₽;
- пропускаем "нероллы" (data-f-type="Нероленный");
- пропускаем мусор (договорная/под заказ/фарм/услуги);
- cat2 требует хотя бы одного ивентового 5★ в описании;
- пропускаем "только стандартные 5★" (без ивентовых).

Модуль импортируется из monitor_all.py; запускается и отдельно.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

# Когда запускаем отдельно — делаем возможным импорт common.py.
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
    fetch,
    has_event_5star,
    is_blacklisted_seller,
    is_europe,
    is_garbage,
    load_seen,
    price_to_rub,
    prune_seen,
    save_seen,
    update_seen,
    mark_disappeared,
)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genshin_seen.json")
# ?currency=RUR принуждает FunPay отдавать цены в рублях с учётом комиссии
# площадки — это и есть настоящая "цена для покупателя" (на ~300₽ больше,
# чем USD*курс, из-за комиссий продавца и платёжки).
FUNPAY_URL = "https://funpay.com/lots/696/?currency=RUR"


def parse_funpay(html: str | None = None) -> list[dict[str, Any]]:
    """Распарсить ленту лотов FunPay. Возвращает список словарей."""
    if html is None:
        html = fetch(FUNPAY_URL)
    if not html:
        return []

    # data-f-* атрибуты лежат на внешнем <a class="tc-item" ...>.
    # Лента — плоский список, вложенных <a> нет, поэтому ленивый (.*?)
    # корректно ловит body.
    pattern = re.compile(
        r'<a\b([^>]*href="(https://funpay\.com/lots/offer\?id=(\d+))"[^>]*)>'
        r"(.*?)</a>",
        re.DOTALL,
    )
    results: list[dict[str, Any]] = []
    for m in pattern.finditer(html):
        attrs = m.group(1)
        url = m.group(2)
        item_id = m.group(3)
        body = m.group(4)
        full = attrs + " " + body  # data-f-* на внешнем теге

        ar_m = re.search(r'data-f-ar="(\d+)"', full)
        ar = int(ar_m.group(1)) if ar_m else None

        type_m = re.search(r'data-f-type="([^"]*)"', full)
        acc_type = (type_m.group(1) if type_m else "").strip().lower()

        mail_m = re.search(r'data-f-mail="([^"]*)"', full)
        mail = mail_m.group(1) if mail_m else "?"

        hero_m = re.search(r'data-f-hero="([^"]*)"', full)
        hero = hero_m.group(1) if hero_m else "?"

        srv_m = re.findall(r"tc-server-inside[^>]*>([^<]+)", body)
        if srv_m:
            server = srv_m[0].strip()
        else:
            srv_m2 = re.search(r'tc-server[^"]*"[^>]*>([^<]+)</div>', body)
            server = srv_m2.group(1).strip() if srv_m2 else ""

        desc_m = re.findall(r"tc-desc-text[^>]*>([^<]+)", body)
        desc = (
            desc_m[0].replace("&nbsp;", " ").strip().replace("\u00a0", " ")
            if desc_m
            else ""
        )

        price_m = re.search(r'tc-price[^>]*>\s*<div>([^<]+)<', body)
        unit_m = re.search(r'<span class="unit">([^<]+)<', body)
        price_str = price_m.group(1).strip() if price_m else ""
        currency = unit_m.group(1).strip() if unit_m else ""
        price_num = None
        try:
            price_num = float(
                price_str.replace("\u00a0", "").replace(" ", "").replace(",", ".")
            )
        except Exception:
            pass
        # Если сайт уже отдал в ₽ — price_to_rub вернёт то же число.
        # Если вдруг USD/EUR — применит курсовой fallback.
        price_rub = price_to_rub(price_num, currency) if price_num is not None else None

        seller_m = re.findall(r"media-user-name[^>]*>\s*\n?\s*([^<]+)", body)
        seller = seller_m[0].strip() if seller_m else "?"

        results.append({
            "source": "FunPay",
            "id": item_id,
            "ar": ar,
            "type": acc_type,
            "mail": mail,
            "hero": hero,
            "server": server,
            "desc": desc,
            "price_orig": f"{price_str} {currency}".strip(),
            "price_rub": price_rub,
            "seller": seller,
            "url": url,
            # FunPay в ленте 696 не отдаёт дату создания лота. Используем
            # порядок в HTML как слабую прокси-метрику "свежести":
            # чем раньше встретился — тем выше в ленте (FunPay сортирует
            # промо → остальные в обратном хронологическом порядке).
            "_feed_order": len(results),
        })
    return results


def _passes_common(a: dict[str, Any]) -> bool:
    """Единые правила: Европа, не неролл, не мусор, цена в рублях известна."""
    if not is_europe(a.get("server", "")):
        return False
    if a.get("type", "") in ("нероленный", "неролл"):
        return False
    if is_garbage(a.get("desc", "")):
        return False
    if is_blacklisted_seller(a.get("seller")):
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
        desc = a.get("desc", "")
        if ar is None or price is None:
            continue

        if CAT1_AR_MIN <= ar <= CAT1_AR_MAX and CAT1_MIN_PRICE <= price <= CAT1_MAX_PRICE:
            cat1.append(a)

        if CAT2_AR_MIN <= ar <= CAT2_AR_MAX and CAT2_MIN_PRICE <= price <= CAT2_MAX_PRICE:
            # cat2: обязательно хотя бы один ивентовый 5★ в описании.
            # Если описание вообще без имён 5★ — пропускаем (пользователь
            # хочет именно ивентовые, а не "может быть").
            if not has_event_5star(desc):
                continue
            cat2.append(a)

    cat1.sort(key=lambda x: x.get("price_rub") or float("inf"))
    cat2.sort(key=lambda x: x.get("price_rub") or float("inf"))
    return cat1, cat2


def run(reset: bool = False) -> dict[str, Any]:
    """
    Запустить мониторинг FunPay и вернуть структурированный результат:
      {
        "source": "FunPay",
        "total_raw": N,
        "cat1": [...], "cat2": [...],
        "new_cat1": [...], "new_cat2": [...],
        "drop_cat1": [...], "drop_cat2": [...],
        "first_run": bool,
        "pruned": N,
        "ok": bool,
      }
    """
    html = fetch(FUNPAY_URL)
    raw = parse_funpay(html)
    cat1, cat2 = categorize(raw)

    if reset and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    seen_before = load_seen(STATE_FILE)
    # "Первый запуск" — если state ещё не существует или оба сегмента пусты.
    # Иначе юзер получит 600+ "новых" на чистом state-файле.
    first_run = (
        not os.path.exists(STATE_FILE)
        or (not seen_before.get("cat1") and not seen_before.get("cat2"))
    )

    seen = seen_before
    pruned = prune_seen(seen)
    new_cat1, drop_cat1 = update_seen(seen, "cat1", cat1)
    new_cat2, drop_cat2 = update_seen(seen, "cat2", cat2)
    # Гл. 8: «исчезли = продали». Считаем только если источник вернул
    # осмысленный результат (иначе выпишем «продали» весь seen-файл
    # при первой же ошибке FunPay).
    src_ok = len(raw) > 0
    fresh_c1 = {it["id"] for it in cat1 if it.get("id")}
    fresh_c2 = {it["id"] for it in cat2 if it.get("id")}
    sold_c1 = mark_disappeared(seen, "cat1", fresh_c1, source_ok=src_ok)
    sold_c2 = mark_disappeared(seen, "cat2", fresh_c2, source_ok=src_ok)
    save_seen(STATE_FILE, seen)

    # На первом запуске ничего не показываем как "new" — слишком шумно.
    if first_run:
        new_cat1, new_cat2 = [], []
        drop_cat1, drop_cat2 = [], []

    # Сортировки:
    #  new — по порядку в ленте FunPay (вверху — самые свежие);
    #  drops — по проценту падения.
    new_cat1.sort(key=lambda x: x.get("_feed_order", 10**9))
    new_cat2.sort(key=lambda x: x.get("_feed_order", 10**9))
    drop_cat1.sort(key=lambda x: -float(x.get("_drop_pct", 0) or 0))
    drop_cat2.sort(key=lambda x: -float(x.get("_drop_pct", 0) or 0))

    # На первом прогоне «продали» не показываем — это бывший v1/v2 seen,
    # который впервые видит v3.
    if first_run:
        sold_c1, sold_c2 = [], []

    return {
        "source": "FunPay",
        "total_raw": len(raw),
        "cat1": cat1,
        "cat2": cat2,
        "new_cat1": new_cat1,
        "new_cat2": new_cat2,
        "drop_cat1": drop_cat1,
        "drop_cat2": drop_cat2,
        "sold_cat1": sold_c1,
        "sold_cat2": sold_c2,
        "first_run": first_run,
        "pruned": pruned,
        "ok": len(raw) > 0,
    }


def _format_report(data: dict[str, Any]) -> str:
    lines = ["--- FUNPAY MONITOR ---"]
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
                    lines.append(
                        f"    {i+1}. AR{a['ar']} | {a['price_rub']:.0f}₽ ({a['price_orig']})"
                        f" | 📧{a.get('mail','?')} | {a.get('server','?')}"
                    )
                    lines.append(f"       {a['desc'][:120]}")
                    lines.append(f"       🔗 {a['url']}")
            if drops:
                lines.append("  [↓ цена упала]")
                for i, a in enumerate(drops[:25]):
                    lines.append(
                        f"    {i+1}. AR{a['ar']} | {a['price_rub']:.0f}₽"
                        f" (было {a['_prev_price']:.0f}₽, −{a['_drop_pct']}%)"
                    )
                    lines.append(f"       {a['desc'][:120]}")
                    lines.append(f"       🔗 {a['url']}")
            if not new_items and not drops:
                lines.append("  ничего нового")
    lines.append(
        f"\n📊 Всего на FunPay (сырьё): {data['total_raw']}, "
        f"в фильтрах: {len(data['cat1'])}+{len(data['cat2'])}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="FunPay monitor — Genshin accounts")
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
