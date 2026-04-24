#!/usr/bin/env python3
"""
Specialized PayGame Monitor for Genshin Impact
"""
import urllib.request, gzip, re, json, os, hashlib, sys, io

EUR_TO_RUB = 105
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paygame_seen.json")

# Стандартные 5★
STANDARD_5STAR = {
    "дилюк", "diluc", "джинн", "jean", "кэ цин", "кэцин", "ке цин", "кецин", "keqing",
    "мона", "mona", "цици", "ци ци", "qiqi", "тигнари", "tighnari",
    "дехья", "dehya", "амбер", "amber", "мидзуки", "midzuki", "мидуки",
}

# Ивентовые 5★
EVENT_5STAR = {
    "венди", "венти", "venti", "эола", "еола", "eula", "кадзуха", "kazuha",
    "чжун ли", "чжунли", "zhongli", "гань юй", "ганьюй", "ganyu", "сяо", "xiao",
    "ху тао", "хутао", "hu tao", "йоимия", "ёимия", "yoimiya",
    "шэнь хэ", "шэньхэ", "шень хэ", "шеньхэ", "shenhe", "янь фэй", "яньфэй", "yanfei",
    "аяка", "ayaka", "камисато", "райдэн", "райден", "raiden",
    "аято", "ayato", "итто", "itto", "кокоми", "kokomi",
    "яэ мико", "яэмико", "ямико", "yae miko",
    "нахида", "nahida", "нихида", "сайно", "cyno",
    "вандерер", "wanderer", "скиталец", "странник", "альхаисам", "alhaitham",
    "фурина", "furina", "нихида", "нёвиллет", "neuvillette", "невиллет",
    "навия", "navia", "клоринда", "clorinde", "сигвин", "sigewinne",
    "рисли", "wriothesley", "рёли", "линей", "lyney", "фремине", "freminet",
    "муалани", "mualani", "кинич", "kinich", "часка", "chasca",
    "мавуика", "mavuika", "ситлали", "citlali", "шилонен", "xilonen",
    "арлекино", "arlecchino",
    "тарталья", "tartaglia", "чайлд", "childe", "альбедо", "albedo",
    "нилу", "nilou", "фарузан", "faruzan",
    "эмилия", "emilie", "коломбина", "инеффа", "иннефа",
    "эскоф", "эскофье", "лаум", "дурин",
    "варка", "тиори", "chiori", "скирк",
    "е лань", "елань", "ю лань", "yelan",
    "шарлотта", "charlotte",
}

def should_skip_starter_only(desc):
    t = desc.lower()
    found_std = any(name in t for name in STANDARD_5STAR)
    found_evt = any(name in t for name in EVENT_5STAR)
    if found_evt: return False
    if found_std and not found_evt: return True
    return False

def fetch(url, timeout=30):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
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
        print(f"Ошибка PayGame: {e}")
        return ""

def load_seen():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {}

def save_seen(seen):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
    except: pass

def parse_paygame():
    html = fetch('https://paygame.ru/games/genshin-impact/offers?type=account')
    if not html: return []
    
    # Карта и ссылка
    items = re.findall(r'<a\s+[^>]*href="([^"]+)"[^>]*class="[^"]*sc-17v71la-2[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)
    
    results = []
    for href, body in items:
        # Описание
        desc_m = re.search(r'class="sc-17v71la-14[^"]*">([^<]+)</span>', body)
        description = desc_m.group(1).strip() if desc_m else ""
        
        # Цена
        price_m = re.search(r'class="sc-17v71la-17[^"]*">([^<]+)</span>', body)
        price_str = price_m.group(1).strip() if price_m else ""
        
        # Ранг (AR)
        ar_m = re.search(r'AR<!-- -->:</span><span[^>]*>(\d+)</span>', body)
        ar = int(ar_m.group(1)) if ar_m else None
        
        # Продавец
        seller_m = re.search(r'class="jvm2kw-22[^"]*"><span>([^<]+)</span>', body)
        seller = seller_m.group(1).strip() if seller_m else "?"
        
        price_rub = None
        try:
            price_rub = float(price_str.replace('₽','').replace(' ','').replace(',','.'))
        except: pass
        
        url = "https://paygame.ru" + href if href.startswith('/') else href

        results.append({
            'source': 'PayGame', 'id': hashlib.md5(f"{description}{price_str}".encode()).hexdigest()[:10],
            'ar': ar, 'server': '?', 'desc': description,
            'price_rub': price_rub, 'price_orig': price_str,
            'seller': seller, 'url': url
        })
    return results

def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding))

def main():
    all_accounts = parse_paygame()
    
    cat1 = []
    cat2 = []
    for a in all_accounts:
        ar = a.get('ar')
        price = a.get('price_rub')
        desc = a.get('desc', '')

        if ar and 50 <= ar <= 60 and price and price <= 3000:
            cat1.append(a)

        if ar and ar <= 20 and price and price <= 750:
            if price < 50 or should_skip_starter_only(desc): continue
            cat2.append(a)

    seen = load_seen()
    new_cat1 = [a for a in cat1 if a['id'] not in seen.get('cat1', {})]
    new_cat2 = [a for a in cat2 if a['id'] not in seen.get('cat2', {})]

    seen.setdefault('cat1', {}).update({a['id']: a['price_rub'] for a in cat1})
    seen.setdefault('cat2', {}).update({a['id']: a['price_rub'] for a in cat2})
    save_seen(seen)

    output = ["--- PAYGAME MONITOR ---"]
    if new_cat1:
        output.append(f"🔥 AR 50-60, до 3000₽ — {len(new_cat1)} новых:")
        for i, a in enumerate(new_cat1[:15]):
            output.append(f"{i+1}. AR{a['ar']} | {a['price_orig']} | 👤 {a['seller']}\n   {a['desc'][:100]}\n   🔗 {a['url']}")
    else:
        output.append("🔥 AR 50-60 — новых нет")

    output.append("")
    if new_cat2:
        output.append(f"🌱 AR≤20, до 750₽ — {len(new_cat2)} новых:")
        for i, a in enumerate(new_cat2[:15]):
            output.append(f"{i+1}. AR{a['ar']} | {a['price_orig']}\n   {a['desc'][:100]}\n   🔗 {a['url']}")
    else:
        output.append("🌱 AR≤20 — новых нет")

    output.append(f"\n📊 Найдено на PayGame: {len(all_accounts)} лотов. Всего в фильтрах: {len(cat1)}+{len(cat2)}")
    safe_print("\n".join(output))

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        except: pass
    main()
