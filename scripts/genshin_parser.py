#!/usr/bin/env python3
"""
Парсер аккаунтов Genshin Impact с FunPay и PayGame
Запуск: python3 genshin_parser.py [--site funpay|paygame|all] [--ar-min 50] [--ar-max 60] [--server europe|asia|america] [--limit 30]
"""
import urllib.request
import gzip
import re
import json
import argparse

def fetch(url, accept='text/html', timeout=20):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            data = gzip.decompress(data)
        return data.decode('utf-8', errors='replace')
    except Exception as e:
        print(f"Ошибка при запросе {url}: {e}")
        return ""

def parse_funpay(ar_min=0, ar_max=999, server=None, limit=30):
    html = fetch('https://funpay.com/lots/696/')
    if not html: return []
    
    items = re.findall(
        r'<a\s+[^>]*href="(https://funpay\.com/lots/offer\?id=(\d+))"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    offers = []
    for href, item_id, body in items:
        o = {"url": href, "source": "FunPay", "id": item_id}
        
        # Описание
        desc = re.findall(r'tc-desc-text[^>]*>([^<]+)', body)
        o["desc"] = desc[0].replace('&nbsp;', ' ').strip() if desc else "?"
        
        # Ранг (AR)
        ar_m = re.search(r'data-f-ar="(\d+)"', body)
        o["ar"] = int(ar_m.group(1)) if ar_m else None
        if not o["ar"]:
            ar_m2 = re.search(r'(?:AR|ранг|rank|Ранг)\s*[:=]?\s*(\d+)', body, re.I)
            o["ar"] = int(ar_m2.group(1)) if ar_m2 else None
        
        type_m = re.search(r'data-f-type="([^"]*)"', body)
        o["type"] = type_m.group(1) if type_m else "?"
        
        hero_m = re.search(r'data-f-hero="([^"]*)"', body)
        o["hero"] = hero_m.group(1) if hero_m else "?"
        
        mail_m = re.search(r'data-f-mail="([^"]*)"', body)
        o["mail"] = mail_m.group(1) if mail_m else "?"
        
        seller = re.findall(r'media-user-name[^>]*>\s*\n?\s*([^<]+)', body)
        o["seller"] = seller[0].strip() if seller else "?"
        
        price_m = re.search(r'tc-price[^>]*>\s*<div>([^<]+)<', body)
        unit_m = re.search(r'<span class="unit">([^<]+)<', body)
        o["price"] = price_m.group(1).strip() if price_m else "?"
        o["currency"] = unit_m.group(1).strip() if unit_m else ""
        
        srv = re.findall(r'tc-server-inside[^>]*>([^<]+)', body)
        o["server"] = srv[0].strip().lower() if srv else ""
        
        # Filter
        if o["ar"] and (o["ar"] < ar_min or o["ar"] > ar_max):
            continue
        if server and server not in o["server"]:
            continue
        
        offers.append(o)
        if len(offers) >= limit:
            break
    
    return offers

def parse_paygame(ar_min=0, ar_max=999, server=None, limit=30):
    html = fetch('https://paygame.ru/games/genshin-impact/offers?type=account')
    texts = re.findall(r'>\s*([^<]{5,300})\s*<', html)
    texts = [t.strip() for t in texts if t.strip()]
    
    offers = []
    current = None
    skip_set = {"Сервер", "Гарантия продавца", "Смена данных", "В аренду",
                "Написать продавцу", "автовыдача", "Нет оценок", "нет оценок",
                "Пожизненная", "Полная", "Неролл", "Европа", "Азия", "Америка"}
    
    i = 0
    while i < len(texts):
        t = texts[i].replace('\xa0', ' ')
        
        # Price
        if re.match(r'^[\d\s,.]+₽$', t.strip()):
            if current:
                current["price"] = t.strip()
            i += 1
            continue
        
        if t in skip_set or re.match(r'^(Пожизненная|Полная|Неролл|\d+ дней?|Европа|Азия|Америка)$', t):
            i += 1
            continue
        
        if current and not current.get("seller") and re.match(r'^[a-zA-Z0-9_-]{2,30}$', t):
            current["seller"] = t
            i += 1
            continue
        
        if re.search(r'акк|AR|ранг|gen|примо|крут|личн|прод|срочн|сигн|легендарн|персонаж|5★|₽|Ху |Флинс|Райд|Нахид|Фурин|Аяк|Колом|Скирк|Мавуик|Варес|Инефф|Эскоф|Дурин|Нефер|Лаум|Варк|Тиор', t, re.I) or \
           len(t) > 15 and not re.match(r'^[A-ZА-Я]{1,5}$', t):
            if current and current.get("desc"):
                offers.append(current)
            current = {"source": "PayGame", "desc": t, "seller": None, "price": None, "server": None, "ar": None}
            ar_m = re.search(r'AR\s*(\d+)', t, re.I)
            if ar_m:
                current["ar"] = int(ar_m.group(1))
        
        i += 1
    
    if current and current.get("desc"):
        offers.append(current)
    
    # Filter
    result = []
    for o in offers:
        if o["ar"] and (o["ar"] < ar_min or o["ar"] > ar_max):
            continue
        if server:
            # PayGame doesn't have server in parsed text, skip filter
            pass
        result.append(o)
        if len(result) >= limit:
            break
    
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Genshin Impact account parser')
    parser.add_argument('--site', default='all', choices=['funpay', 'paygame', 'all'])
    parser.add_argument('--ar-min', type=int, default=0)
    parser.add_argument('--ar-max', type=int, default=999)
    parser.add_argument('--server', default=None)
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    
    if args.site in ('funpay', 'all'):
        print(f"{'='*60}")
        print(f"FUNPAY — Аккаунты Genshin Impact")
        print(f"{'='*60}\n")
        try:
            fp = parse_funpay(ar_min=args.ar_min, ar_max=args.ar_max, server=args.server, limit=args.limit)
            for i, o in enumerate(fp):
                ar_str = f"AR{o['ar']}" if o.get('ar') else "?"
                price_str = f"{o['price']} {o['currency']}" if o.get('price') != "?" else "цена?"
                print(f"{i+1}. [{ar_str}] {o['desc'][:100]}")
                print(f"   Сервер: {o['server']} | {o.get('hero','?')} | {o.get('type','?')} | Почта: {o.get('mail','?')}")
                print(f"   💰 {price_str} | 👤 {o['seller']}")
                print(f"   {o['url']}")
                print()
            print(f"Всего: {len(fp)}")
        except Exception as e:
            print(f"Ошибка FunPay: {e}")
    
    if args.site in ('paygame', 'all'):
        print(f"\n{'='*60}")
        print(f"PAYGAME — Аккаунты Genshin Impact")
        print(f"{'='*60}\n")
        try:
            pg = parse_paygame(ar_min=args.ar_min, ar_max=args.ar_max, limit=args.limit)
            for i, o in enumerate(pg):
                ar_str = f"AR{o['ar']}" if o.get('ar') else "?"
                print(f"{i+1}. [{ar_str}] {o['desc'][:100]}")
                print(f"   💰 {o.get('price', '?')} | 👤 {o.get('seller', '?')}")
                print()
            print(f"Всего: {len(pg)}")
        except Exception as e:
            print(f"Ошибка PayGame: {e}")
