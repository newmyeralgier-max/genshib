#!/usr/bin/env python3
"""
Мониторинг PayGame — аккаунты Genshin Impact.

Парсинг через Schema.org `application/ld+json` (ItemList) — устойчив
к смене авто-генерируемых CSS-классов Next.js. Сервер берётся из HTML
возле карточки (JSON-LD его не содержит).

Фильтрация — та же, что для FunPay (см. genshin_monitor.py).
"""
from __future__ import annotations

import json
import os
import re
import sys
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
    fetch,
    has_event_5star,
    is_europe,
    is_garbage,
    load_seen,
    save_seen,
    update_seen,
)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paygame_seen.json")
PAYGAME_URL = "https://paygame.ru/games/genshin-impact/offers?type=account"


# --- JSON-LD helpers ------------------------------------------------
def _find_item_list(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if node.get("@type") == "ItemList":
            return node
        for v in node.values():
            r = _find_item_list(v)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_item_list(v)
            if r is not None:
                return r
    return None


def _parse_desc_attrs(description: str) -> dict[str, str]:
    """
    Из описания JSON-LD извлекаем хвост после "Аккаунты Genshin Impact." —
    именно там лежат атрибуты вида "Ключ: значение; Ключ: значение".
    """
    m = re.search(r"Аккаунты Genshin Impact\.\s*(.*)$", description or "")
    tail = m.group(1) if m else (description or "")
    out: dict[str, str] = {}
    for part in tail.split(";"):
        if ":" in part:
            k, _, v = part.partition(":")
            out[k.strip()] = v.strip()
    return out


def _server_from_html(html: str, offer_id: str) -> str:
    """
    Найти значок сервера в HTML рядом с `href="/offers/<id>"`.
    Структура: `<span>Сервер<!-- -->:</span><span>Европа</span>`.

    Важно: в HTML ссылка на оффер встречается несколько раз (JSON-LD,
    schema.org Product, и сама карточка). Нам нужна именно карточка —
    ищем через `href="/offers/<id>"`.
    """
    idx = html.find(f'href="/offers/{offer_id}"')
    if idx < 0:
        # fallback на любую упоминание — маловероятно, но вдруг разметка сменилась
        idx = html.find(f'/offers/{offer_id}')
        if idx < 0:
            return ""
    ctx = html[idx: idx + 3000]
    m = re.search(
        r"Сервер(?:<!--[^>]*-->)?\s*:\s*</span>\s*<span[^>]*>([^<]+)</span>",
        ctx,
    )
    if m:
        return m.group(1).strip()
    # fallback — просто найти слово
    m2 = re.search(r"(Европа|Азия|Америка|США|EU|ASIA|AM)", ctx)
    return m2.group(1) if m2 else ""


def parse_paygame(html: str | None = None) -> list[dict[str, Any]]:
    if html is None:
        html = fetch(PAYGAME_URL)
    if not html:
        return []

    # Все JSON-LD блоки, ищем среди них ItemList (вложенный в CollectionPage).
    blocks = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    items_meta: list[dict[str, Any]] = []
    for b in blocks:
        try:
            parsed = json.loads(b)
        except Exception:
            continue
        il = _find_item_list(parsed)
        if il:
            items_meta = il.get("itemListElement", []) or []
            break

    results: list[dict[str, Any]] = []
    for entry in items_meta:
        p = entry.get("item") or {}
        url = p.get("url", "")
        offer_id = url.rsplit("/", 1)[-1] if url else ""
        if not offer_id:
            continue

        name = (p.get("name") or "").strip()
        description = (p.get("description") or "").strip()
        attrs = _parse_desc_attrs(description)

        ar = None
        ar_raw = attrs.get("AR", "")
        m = re.search(r"(\d+)", ar_raw)
        if m:
            ar = int(m.group(1))
        if ar is None:
            # fallback — ищем в имени/описании
            m = re.search(r"AR\s*[:\-]?\s*(\d+)", description, re.I)
            if m:
                ar = int(m.group(1))

        offer = p.get("offers") or {}
        price_val = offer.get("price")
        currency = offer.get("priceCurrency", "RUB")
        price_rub = None
        try:
            if price_val is not None and str(currency).upper() == "RUB":
                price_rub = float(price_val)
        except Exception:
            pass

        seller = ((offer.get("seller") or {}).get("name") or "").strip() or "?"
        server = _server_from_html(html, offer_id)

        # Объединяем name + description в полный текст для фильтров 5★.
        full_text = f"{name}. {description}"

        neroll = (attrs.get("Неролл", "").lower() == "да")

        results.append({
            "source": "PayGame",
            "id": offer_id,
            "ar": ar,
            "server": server,
            "desc": name if name else description,
            "desc_full": full_text,
            "neroll": neroll,
            "price_orig": f"{price_val} ₽" if price_val is not None else "",
            "price_rub": price_rub,
            "seller": seller,
            "url": url,
        })
    return results


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
    raw = parse_paygame()
    cat1, cat2 = categorize(raw)

    if reset and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    seen_before = load_seen(STATE_FILE)
    first_run = (
        not os.path.exists(STATE_FILE)
        or (not seen_before.get("cat1") and not seen_before.get("cat2"))
    )

    seen = seen_before
    new_cat1, drop_cat1 = update_seen(seen, "cat1", cat1)
    new_cat2, drop_cat2 = update_seen(seen, "cat2", cat2)
    save_seen(STATE_FILE, seen)

    if first_run:
        new_cat1, new_cat2 = [], []
        drop_cat1, drop_cat2 = [], []

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
                    lines.append(
                        f"    {i+1}. AR{a['ar']} | {a['price_rub']:.0f}₽ | "
                        f"👤 {a.get('seller','?')} | {a.get('server','?')}"
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
        f"\n📊 Всего на PayGame (сырьё): {data['total_raw']}, "
        f"в фильтрах: {len(data['cat1'])}+{len(data['cat2'])}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="PayGame monitor — Genshin accounts")
    p.add_argument("--reset", action="store_true", help="очистить seen и начать сначала")
    args = p.parse_args(argv)

    data = run(reset=args.reset)
    print(_format_report(data))


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass
    main()
