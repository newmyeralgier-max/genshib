#!/usr/bin/env python3
"""
Единая точка входа — опрашивает FunPay и PayGame одним запуском,
печатает краткий отчёт в консоль и сохраняет детальный Markdown-отчёт.

По умолчанию пишет в `scripts/report.md` (рядом со скриптом).
Можно переопределить через `--out` или env `GENSHIB_REPORT`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import genshin_monitor  # noqa: E402
import paygame_monitor  # noqa: E402
from common import (  # noqa: E402
    CAT1_AR_MAX,
    CAT1_AR_MIN,
    CAT1_MAX_PRICE,
    CAT1_MIN_PRICE,
    CAT2_AR_MAX,
    CAT2_AR_MIN,
    CAT2_MAX_PRICE,
    CAT2_MIN_PRICE,
)

DEFAULT_REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.md")


def _fmt_ago(ts: int | None, now: int | None = None) -> str:
    if not ts:
        return ""
    if now is None:
        now = int(time.time())
    delta = max(0, int(now) - int(ts))
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{delta // 60}м назад"
    if delta < 86400:
        return f"{delta // 3600}ч назад"
    return f"{delta // 86400}д назад"


def _fmt_item_md(a: dict[str, Any], is_drop: bool = False) -> list[str]:
    ar = a.get("ar", "?")
    price = a.get("price_rub")
    price_s = f"{price:.0f}₽" if isinstance(price, (int, float)) else "?"
    tag = ""
    if is_drop:
        tag = f" — ↓ с {a['_prev_price']:.0f}₽ (−{a['_drop_pct']}%)"
    source = a.get("source", "?")
    mail = a.get("mail")
    seller = a.get("seller", "?") or "?"
    server = a.get("server", "?") or "?"
    desc = (a.get("desc") or "").strip()
    desc_short = desc[:180]
    url = a.get("url", "")
    ago = _fmt_ago(a.get("_created_ts") or a.get("_first_seen_ts"))
    lines: list[str] = []
    head = f"- **AR{ar}** · **{price_s}**{tag} · {source} · server: {server}"
    if ago:
        head += f" · 🕒 {ago}"
    if mail:
        head += f" · 📧 {mail}"
    head += f" · 👤 {seller}"
    lines.append(head)
    if desc_short:
        lines.append(f"  - {desc_short}")
    if url:
        lines.append(f"  - {url}")
    return lines


def _section(title: str, new_items: list[dict[str, Any]], drops: list[dict[str, Any]], total_in_cat: int) -> list[str]:
    lines: list[str] = []
    lines.append(f"### {title}")
    lines.append("")
    lines.append(
        f"_всего в сегменте: **{total_in_cat}**, "
        f"новых: **{len(new_items)}**, "
        f"подешевевших: **{len(drops)}**_"
    )
    lines.append("")
    if new_items:
        lines.append("**🆕 Новые:**")
        lines.append("")
        for a in new_items:
            lines.extend(_fmt_item_md(a))
        lines.append("")
    if drops:
        lines.append("**📉 Цена упала:**")
        lines.append("")
        for a in drops:
            lines.extend(_fmt_item_md(a, is_drop=True))
        lines.append("")
    if not new_items and not drops:
        lines.append("_ничего нового_")
        lines.append("")
    return lines


def build_markdown(fp_data: dict[str, Any], pg_data: dict[str, Any], generated_at: str) -> str:
    lines: list[str] = []
    lines.append("# Genshin Accounts Monitor")
    lines.append("")
    lines.append(f"_сгенерировано: {generated_at} UTC_")
    lines.append("")
    lines.append("Фильтры:")
    lines.append(
        f"- **cat1**: AR {CAT1_AR_MIN}-{CAT1_AR_MAX}, цена {int(CAT1_MIN_PRICE)}-{int(CAT1_MAX_PRICE)}₽, "
        "сервер Европа, без нероллов, без мусора"
    )
    lines.append(
        f"- **cat2**: AR {CAT2_AR_MIN}-{CAT2_AR_MAX}, цена {int(CAT2_MIN_PRICE)}-{int(CAT2_MAX_PRICE)}₽, "
        "сервер Европа, обязательно хотя бы один ивентовый 5★, без нероллов, без мусора"
    )
    lines.append("")

    any_first_run = False
    for label, data in (("FunPay", fp_data), ("PayGame", pg_data)):
        lines.append(f"## {label}")
        lines.append("")
        if data["first_run"]:
            any_first_run = True
            lines.append(
                f"⚠️ Первый запуск — сохранили {len(data['cat1'])} + {len(data['cat2'])} "
                "лотов в seen-файл. Новые/подешевевшие покажутся со следующего запуска."
            )
            lines.append("")
            continue
        lines.extend(_section(
            f"cat1: AR {CAT1_AR_MIN}-{CAT1_AR_MAX}, ≤ {int(CAT1_MAX_PRICE)}₽",
            data["new_cat1"], data["drop_cat1"], len(data["cat1"]),
        ))
        lines.extend(_section(
            f"cat2: AR {CAT2_AR_MIN}-{CAT2_AR_MAX}, ≤ {int(CAT2_MAX_PRICE)}₽, только с ивентовыми 5★",
            data["new_cat2"], data["drop_cat2"], len(data["cat2"]),
        ))
        meta = (
            f"> 📊 всего сырых лотов {data['total_raw']}, "
            f"прошло фильтры: {len(data['cat1'])}+{len(data['cat2'])}"
        )
        if not data.get("ok", True):
            meta += " · ⚠️ источник вернул ошибку (данные неполные)"
        if data.get("pruned"):
            meta += f" · seen pruned: {data['pruned']}"
        lines.append(meta)
        lines.append("")

    lines.append("---")
    lines.append("")
    if any_first_run:
        lines.append("_Первый запуск — seen-файл инициализирован, запусти ещё раз чтобы увидеть новые._")
    else:
        total_new = (
            len(fp_data.get("new_cat1", []))
            + len(fp_data.get("new_cat2", []))
            + len(pg_data.get("new_cat1", []))
            + len(pg_data.get("new_cat2", []))
        )
        total_drops = (
            len(fp_data.get("drop_cat1", []))
            + len(fp_data.get("drop_cat2", []))
            + len(pg_data.get("drop_cat1", []))
            + len(pg_data.get("drop_cat2", []))
        )
        lines.append(f"_итого за прогон: **{total_new}** новых, **{total_drops}** подешевевших._")
    lines.append("")
    return "\n".join(lines)


def _short_console_summary(fp: dict[str, Any], pg: dict[str, Any], report_path: str) -> str:
    def _counts(d: dict[str, Any]) -> str:
        if d["first_run"]:
            return f"first_run ({len(d['cat1'])}+{len(d['cat2'])} в seen)"
        return (
            f"cat1 {len(d['cat1'])} (new {len(d['new_cat1'])}, ↓ {len(d['drop_cat1'])}) "
            f"| cat2 {len(d['cat2'])} (new {len(d['new_cat2'])}, ↓ {len(d['drop_cat2'])})"
        )

    return (
        f"FunPay:  {_counts(fp)}\n"
        f"PayGame: {_counts(pg)}\n"
        f"→ отчёт: {report_path}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genshin accounts monitor (FunPay + PayGame)")
    parser.add_argument("--reset", action="store_true", help="очистить seen и начать сначала (оба сайта)")
    parser.add_argument(
        "--out",
        default=os.environ.get("GENSHIB_REPORT", DEFAULT_REPORT),
        help="путь до Markdown-отчёта (по умолчанию scripts/report.md)",
    )
    parser.add_argument("--only", choices=["funpay", "paygame"], default=None,
                        help="запустить только один сайт")
    args = parser.parse_args(argv)

    empty = {
        "source": "", "total_raw": 0, "cat1": [], "cat2": [],
        "new_cat1": [], "new_cat2": [], "drop_cat1": [], "drop_cat2": [],
        "first_run": False, "ok": True,
    }
    fp_data = dict(empty)
    pg_data = dict(empty)

    if args.only != "paygame":
        fp_data = genshin_monitor.run(reset=args.reset)
    if args.only != "funpay":
        pg_data = paygame_monitor.run(reset=args.reset)

    now_utc = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    md = build_markdown(fp_data, pg_data, now_utc)

    try:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception as e:
        print(f"[report] не удалось записать {args.out}: {e}", file=sys.stderr)

    print(_short_console_summary(fp_data, pg_data, args.out))
    # ненулевой код, если хотя бы один источник провалился
    fp_ok = fp_data.get("ok", True)
    pg_ok = pg_data.get("ok", True)
    return 0 if (fp_ok and pg_ok) else 2


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(main())
