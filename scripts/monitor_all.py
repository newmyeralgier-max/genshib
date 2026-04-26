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
import match  # noqa: E402
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

# Порог «горячего» лота для тега в .md (гл. 2).
HOT_DISCOUNT_THRESHOLD = float(os.environ.get("GENSHIB_HOT_THRESHOLD", "30"))

# Параметры топ-блока в шапке отчёта (гл. 6).
HOT_TOP_THRESHOLD = float(os.environ.get("GENSHIB_HOT_TOP_THRESHOLD", "20"))
HOT_TOP_N = int(os.environ.get("GENSHIB_HOT_TOP_N", "10"))

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


def _fmt_item_md(
    a: dict[str, Any],
    is_drop: bool = False,
    *,
    ctx: dict[str, Any] | None = None,
) -> list[str]:
    """
    Отрисовать один лот в Markdown. ctx (опц.):
    - 'medians': dict[(ar_bucket, n_event), float] — медианы FunPay;
    - 'matches': dict[id -> list[other_lot]] — встречные совпадения.
    Если ctx нет — вывод базовый (без дисконта и без аналогов).
    """
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

    # Гл. 2: тег «🔥 -X% от рынка» в начале строки.
    medians = (ctx or {}).get("medians") or {}
    disc_prefix = ""
    if medians:
        disc = match.discount_pct(a, medians)
        if disc is not None and disc >= HOT_DISCOUNT_THRESHOLD:
            disc_prefix = f"🔥 -{disc:.0f}% от рынка · "

    lines: list[str] = []
    head = f"- {disc_prefix}**AR{ar}** · **{price_s}**{tag} · {source} · server: {server}"
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

    # Гл. 1: «🔁 На <other> аналоги:».
    matches = (ctx or {}).get("matches") or {}
    sid = str(a.get("id") or "")
    sims = matches.get(sid) or []
    if sims:
        other_label = "FunPay" if source != "FunPay" else "PayGame"
        lines.append(f"  - 🔁 На {other_label} аналоги:")
        for o in sims:
            op = o.get("price_rub")
            ops = f"{op:.0f}₽" if isinstance(op, (int, float)) else "?"
            j = o.get("_jaccard", 0.0)
            ourl = o.get("url") or ""
            lines.append(f"    - {ops} — {ourl} (jaccard {j:.2f})")
    return lines


def _section(
    title: str,
    new_items: list[dict[str, Any]],
    drops: list[dict[str, Any]],
    total_in_cat: int,
    *,
    ctx: dict[str, Any] | None = None,
) -> list[str]:
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
            lines.extend(_fmt_item_md(a, ctx=ctx))
        lines.append("")
    if drops:
        lines.append("**📉 Цена упала:**")
        lines.append("")
        for a in drops:
            lines.extend(_fmt_item_md(a, is_drop=True, ctx=ctx))
        lines.append("")
    if not new_items and not drops:
        lines.append("_ничего нового_")
        lines.append("")
    return lines


def hot_pick(
    fp_data: dict[str, Any],
    pg_data: dict[str, Any],
    medians: dict[tuple[int | None, int], float],
    *,
    threshold: float = HOT_TOP_THRESHOLD,
    n: int = HOT_TOP_N,
) -> list[dict[str, Any]]:
    """
    Собрать самые «горячие» новые лоты с обоих источников.
    Только из new_cat1/new_cat2 (иначе шапка дублирует общий список).
    Сортировка — по дисконту DESC.
    """
    pool: list[dict[str, Any]] = []
    for d, src in ((fp_data, "FunPay"), (pg_data, "PayGame")):
        for cat in ("new_cat1", "new_cat2"):
            for lot in d.get(cat) or []:
                disc = match.discount_pct(lot, medians)
                if disc is None or disc < threshold:
                    continue
                pool.append({**lot, "_disc": disc, "_cat": cat, "_src": src})
    pool.sort(key=lambda x: -x["_disc"])
    return pool[:n]


def _format_hot_table(hot: list[dict[str, Any]]) -> list[str]:
    """Markdown-таблица «🔥 Top-N hot lots»."""
    if not hot:
        return [
            "## 🔥 Top hot lots",
            "",
            f"_сейчас горячих лотов нет (порог −{int(HOT_TOP_THRESHOLD)}%)._",
            "",
        ]
    lines = ["## 🔥 Top hot lots", ""]
    lines.append("| disc | source | AR | price | event 5★ | url |")
    lines.append("|---|---|---|---|---|---|")
    for h in hot:
        ar = h.get("ar", "?")
        price = h.get("price_rub")
        ps = f"{price:.0f}₽" if isinstance(price, (int, float)) else "?"
        _, chars, _ = match.fingerprint(h)
        # Названия персонажей по-русски с большой буквы для читаемости.
        chars_pretty = ", ".join(c.title() for c in sorted(chars)) or "—"
        url = h.get("url", "") or ""
        lines.append(
            f"| -{h['_disc']:.0f}% | {h['_src']} | {ar} | {ps} | "
            f"{chars_pretty} | {url} |"
        )
    lines.append("")
    return lines


def _build_context(fp_data: dict[str, Any], pg_data: dict[str, Any]) -> dict[str, Any]:
    """
    Построить вспомогательный контекст для отчёта:
    - medians: медианы FunPay по классу лота;
    - matches_pg / matches_fp: словари id → [аналоги с другого источника].
    """
    fp_pool = (fp_data.get("cat1") or []) + (fp_data.get("cat2") or [])
    pg_pool = (pg_data.get("cat1") or []) + (pg_data.get("cat2") or [])
    medians = match.fp_median_by_class(fp_pool) if fp_pool else {}
    matches_pg = match.build_matches(pg_pool, fp_pool) if fp_pool and pg_pool else {}
    matches_fp = match.build_matches(fp_pool, pg_pool) if fp_pool and pg_pool else {}
    return {
        "medians": medians,
        "matches_pg": matches_pg,
        "matches_fp": matches_fp,
    }


def build_markdown(fp_data: dict[str, Any], pg_data: dict[str, Any], generated_at: str) -> str:
    ctx_full = _build_context(fp_data, pg_data)
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

    # Топ-блок с горячими лотами (см. главу 6 в docs/roadmap.md).
    hot = hot_pick(fp_data, pg_data, ctx_full["medians"])
    lines.extend(_format_hot_table(hot))

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
        # Какие matches подсунуть в ctx данной секции: для FunPay → PayGame-аналоги
        # лежат в ctx_full["matches_fp"], и наоборот.
        section_ctx = {
            "medians": ctx_full["medians"],
            "matches": ctx_full["matches_fp"] if label == "FunPay" else ctx_full["matches_pg"],
        }
        lines.extend(_section(
            f"cat1: AR {CAT1_AR_MIN}-{CAT1_AR_MAX}, ≤ {int(CAT1_MAX_PRICE)}₽",
            data["new_cat1"], data["drop_cat1"], len(data["cat1"]),
            ctx=section_ctx,
        ))
        lines.extend(_section(
            f"cat2: AR {CAT2_AR_MIN}-{CAT2_AR_MAX}, ≤ {int(CAT2_MAX_PRICE)}₽, только с ивентовыми 5★",
            data["new_cat2"], data["drop_cat2"], len(data["cat2"]),
            ctx=section_ctx,
        ))
        meta = (
            f"> 📊 всего сырых лотов {data['total_raw']}, "
            f"прошло фильтры: {len(data['cat1'])}+{len(data['cat2'])}"
        )
        if data.get("rentals_filtered"):
            meta += f" · аренд отсеяно: {data['rentals_filtered']}"
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


def _open_in_default_app(path: str) -> None:
    """
    Открыть файл в дефолтном приложении ОС. На Windows запускается
    через `start "" path`, что подхватит зарегистрированный для .md
    хэндлер (например, Antigravity).
    """
    import subprocess
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"[open] не удалось открыть {path}: {e}", file=sys.stderr)


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
    parser.add_argument("--debug", action="store_true",
                        help="выводить постраничный прогресс PayGame API")
    parser.add_argument("--open", action="store_true",
                        dest="open_after",
                        help="открыть отчёт в дефолтном приложении после генерации (Windows: start, macOS: open, Linux: xdg-open)")
    args = parser.parse_args(argv)
    if args.debug:
        os.environ["GENSHIB_DEBUG"] = "1"

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

    if args.open_after:
        _open_in_default_app(args.out)

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
