"""
Telegram-нотификации (гл. 5).

Только хот-лоты (≥ HOT_TOP_THRESHOLD от рынка). Cron у пользователя
уже есть — расписание не нашему делу. Мы просто:

1) формируем компактный MarkdownV2-текст;
2) отправляем через `https://api.telegram.org/bot<token>/sendMessage`;
3) если бот-токен не настроен (нет env GENSHIB_TG_BOT_TOKEN), молча
   ничего не делаем — это явная фича, чтоб скрипт не падал у людей,
   которые телегу не подключали.

Если кто-то задаст GENSHIB_TG_DRY_RUN=1, сетевой запрос пропустим,
а текст вернём наружу — удобно для тестов и для отладки руками.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Telegram максимум 4096 символов на сообщение. Берём с запасом — кириллица
# в UTF-8 это 2 байта, и при формальном лимите длины строки до этого числа
# редко доходит, но если кто-то задаст GENSHIB_TG_LIMIT=50 — режем.
_TG_TEXT_LIMIT = 4000


# Token + chat_id — обязательны в продакшене, но если не заданы —
# уведомления отключены, и это OK.
ENV_TOKEN = "GENSHIB_TG_BOT_TOKEN"
ENV_CHAT = "GENSHIB_TG_CHAT_ID"
ENV_LIMIT = "GENSHIB_TG_LIMIT"
ENV_MIN = "GENSHIB_TG_MIN_DISCOUNT"
ENV_DRY = "GENSHIB_TG_DRY_RUN"


# MarkdownV2 спецсимволы. Их обязательно экранировать в любом тексте.
# Пропуск даже одного — и Telegram вернёт 400 «can't parse entities».
_MD_V2_ESCAPE = r"_*[]()~`>#+-=|{}.!"


def md_escape(s: str) -> str:
    out = []
    for ch in s:
        if ch in _MD_V2_ESCAPE:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def format_message(hot: list[dict[str, Any]], *, limit: int = 10) -> str:
    """
    Сделать сообщение из топ-N хот-лотов.

    Каждый лот рендерится в одну компактную строку:
    🔥 -45% · AR60 · 480₽ · FunPay — Скирк, Ху Тао · <url>
    """
    lines: list[str] = ["🔥 *Genshib hot lots*"]
    for h in hot[:limit]:
        disc = h.get("_disc")
        ar = h.get("ar", "?")
        price = h.get("price_rub")
        ps = f"{int(price)}₽" if isinstance(price, (int, float)) else "?"
        src = h.get("_src") or h.get("source") or "?"
        url = h.get("url") or ""
        chars = h.get("_chars_pretty") or ""
        d_s = f"\\-{int(disc)}%" if isinstance(disc, (int, float)) else "?"
        head = (
            f"• {d_s} · AR{ar} · {md_escape(ps)} · "
            f"{md_escape(src)}"
        )
        if chars:
            head += f" — {md_escape(chars)}"
        lines.append(head)
        if url:
            lines.append(md_escape(url))
    return "\n".join(lines)


def send(
    hot: list[dict[str, Any]],
    *,
    token: str | None = None,
    chat_id: str | None = None,
    limit: int | None = None,
    min_discount: float | None = None,
) -> tuple[bool, str]:
    """
    Послать уведомление.

    Возвращает (ok, debug_message). ok=False:
      - не задан token/chat_id (молчим, не падаем);
      - сетевая ошибка;
      - hot пуст после фильтра по min_discount.
    """
    token = token or os.environ.get(ENV_TOKEN, "")
    chat_id = chat_id or os.environ.get(ENV_CHAT, "")
    if not token or not chat_id:
        return False, "no token/chat_id (skipping silently)"

    if limit is None:
        try:
            limit = int(os.environ.get(ENV_LIMIT, "10"))
        except ValueError:
            limit = 10
    if min_discount is None:
        try:
            min_discount = float(os.environ.get(ENV_MIN, "30"))
        except ValueError:
            min_discount = 30.0

    filtered = [
        h for h in hot
        if isinstance(h.get("_disc"), (int, float)) and h["_disc"] >= min_discount
    ]
    if not filtered:
        return False, "no items above min_discount"

    text = format_message(filtered, limit=limit)
    # Жёсткий cap на длину, чтобы не словить 400 от Telegram.
    if len(text) > _TG_TEXT_LIMIT:
        text = text[: _TG_TEXT_LIMIT - 1] + "…"
    # Token не URL-кодируем — у Telegram path-сегмент `bot<token>` принимает
    # только сырые символы; квот лишнее (Telegram сам декодирует и тогда
    # это работает, но мы ходим через urllib без encoding-bedrock прокси).
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": "true",
    }).encode()

    if os.environ.get(ENV_DRY) == "1":
        return True, f"[dry] {text}"

    try:
        req = urllib.request.Request(api, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body)
                ok = bool(parsed.get("ok"))
            except Exception:
                ok = False
            return ok, f"http {resp.status}: {body[:200]}"
    except urllib.error.HTTPError as e:  # type: ignore[name-defined]
        # 400 = «can't parse entities» (баг экранирования) — важно увидеть body.
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return False, f"send error http {e.code}: {body[:200]}"
    except Exception as e:
        return False, f"send error: {e}"


__all__ = ["format_message", "md_escape", "send"]
