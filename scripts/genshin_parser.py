#!/usr/bin/env python3
"""
Разовый парсер аккаунтов Genshin Impact с FunPay и PayGame.

Запуск:
    python3 genshin_parser.py
    python3 genshin_parser.py --site funpay --ar-min 55 --ar-max 60 --server европ
    python3 genshin_parser.py --site paygame --limit 10

В отличие от monitor_all.py — не ведёт state и не фильтрует мусор/нероллы.
Это инспектор текущей выдачи, без диффа по времени.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genshin_monitor import parse_funpay  # noqa: E402
from paygame_monitor import parse_paygame  # noqa: E402


def _filter(items, ar_min, ar_max, server, limit):
    out = []
    for o in items:
        ar = o.get("ar")
        if ar is not None and (ar < ar_min or ar > ar_max):
            continue
        if server:
            s = (o.get("server") or "").lower()
            if server.lower() not in s:
                continue
        out.append(o)
        if len(out) >= limit:
            break
    return out


def _print_funpay(items):
    print("=" * 60)
    print("FUNPAY — Аккаунты Genshin Impact")
    print("=" * 60)
    print()
    for i, o in enumerate(items, 1):
        ar_str = f"AR{o['ar']}" if o.get("ar") else "?"
        price_s = o.get("price_orig") or "?"
        print(f"{i}. [{ar_str}] {(o.get('desc') or '')[:100]}")
        print(
            f"   Сервер: {o.get('server','?')} | {o.get('hero','?')} | "
            f"{o.get('type','?')} | Почта: {o.get('mail','?')}"
        )
        print(f"   💰 {price_s} | 👤 {o.get('seller','?')}")
        print(f"   {o.get('url','')}")
        print()
    print(f"Всего: {len(items)}")


def _print_paygame(items):
    print()
    print("=" * 60)
    print("PAYGAME — Аккаунты Genshin Impact")
    print("=" * 60)
    print()
    for i, o in enumerate(items, 1):
        ar_str = f"AR{o['ar']}" if o.get("ar") else "?"
        price = o.get("price_rub")
        price_s = f"{price:.0f}₽" if isinstance(price, (int, float)) else (o.get("price_orig") or "?")
        print(f"{i}. [{ar_str}] {(o.get('desc') or '')[:100]}")
        print(f"   Сервер: {o.get('server','?')}")
        print(f"   💰 {price_s} | 👤 {o.get('seller','?')}")
        print(f"   {o.get('url','')}")
        print()
    print(f"Всего: {len(items)}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Genshin Impact account parser")
    p.add_argument("--site", default="all", choices=["funpay", "paygame", "all"])
    p.add_argument("--ar-min", type=int, default=0)
    p.add_argument("--ar-max", type=int, default=999)
    p.add_argument("--server", default=None)
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args(argv)

    if args.site in ("funpay", "all"):
        try:
            items = _filter(parse_funpay(), args.ar_min, args.ar_max, args.server, args.limit)
            _print_funpay(items)
        except Exception as e:
            print(f"Ошибка FunPay: {e}")

    if args.site in ("paygame", "all"):
        try:
            items = _filter(parse_paygame(), args.ar_min, args.ar_max, args.server, args.limit)
            _print_paygame(items)
        except Exception as e:
            print(f"Ошибка PayGame: {e}")


if __name__ == "__main__":
    main()
