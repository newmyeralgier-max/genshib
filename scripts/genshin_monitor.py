import urllib.request, gzip, re, json, os, hashlib

EUR_TO_RUB = 105
# Используем путь относительно скрипта для Windows/Linux совместимости
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genshin_seen.json")

# Стандартные 5★ (постоянный баннер, актуально на 5.4+)
STANDARD_5STAR = {
    "дилюк", "diluc", "джинн", "jean", "кэ цин", "кэцин", "ке цин", "кецин", "keqing",
    "мона", "mona", "цици", "ци ци", "qiqi", "тигнари", "tighnari",
    "дехья", "dehya", "амбер", "amber", "мидзуки", "midzuki", "мидуки",
}

# Ивентовые (ограниченные) 5★ персонажи
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
    """True = пропускать: только стандартные 5★, без ивентовых"""
    t = desc.lower()
    found_std = any(name in t for name in STANDARD_5STAR)
    found_evt = any(name in t for name in EVENT_5STAR)
    if found_evt:
        return False
    if found_std and not found_evt:
        return True
    return False

def fetch(url, timeout=20):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'max-age=0',
        'Upgrade-Insecure-Requests': '1',
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            data = gzip.decompress(data)
        elif resp.headers.get('Content-Encoding') == 'br':
            try:
                import brotli
                data = brotli.decompress(data)
            except ImportError: pass
        return data.decode('utf-8', errors='replace')
    except Exception as e:
        print(f"Ошибка при запросе {url}: {e}")
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
    except Exception as e:
        print(f"Ошибка сохранения состояния: {e}")

def parse_funpay():
    html = fetch('https://funpay.com/lots/696/')
    if not html: return []
    
    # Более гибкий поиск элементов tc-item
    items = re.findall(
        r'<a\s+[^>]*href="(https://funpay\.com/lots/offer\?id=(\d+))"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )
    results = []
    for url, item_id, body in items:
        # Проверяем, что это именно лот (есть класс tc-item)
        if 'tc-item' not in html[html.find(url)-100 : html.find(url)+500]: 
            # Это может быть не совсем надежно, но обычно tc-item рядом
            pass

        # Ранг приключений (AR)
        ar_m = re.search(r'data-f-ar="(\d+)"', body)
        ar = int(ar_m.group(1)) if ar_m else None
        if ar is None:
            # Ищем в тексте: AR 50, 50 ранг, 50 rank
            ar_m2 = re.search(r'(?:AR|ранг|rank|Ранг)\s*[:=]?\s*(\d+)', body, re.I)
            ar = int(ar_m2.group(1)) if ar_m2 else None
        
        # Сервер
        srv = re.findall(r'tc-server-inside[^>]*>([^<]+)', body)
        server = srv[0].strip() if srv else ""

        # Описание
        desc = re.findall(r'tc-desc-text[^>]*>([^<]+)', body)
        description = desc[0].replace('&nbsp;', ' ').strip() if desc else ""

        # Цена и валюта
        price_m = re.search(r'tc-price[^>]*>\s*<div>([^<]+)<', body)
        unit_m = re.search(r'<span class="unit">([^<]+)<', body)
        price_str = price_m.group(1).strip() if price_m else ""
        currency = unit_m.group(1).strip() if unit_m else ""

        price_rub = None
        try:
            # Убираем пробелы в цене (бывает "1 234")
            p_val = price_str.replace(' ', '').replace(',', '.')
            price_num = float(p_val)
            if currency == '€': price_rub = price_num * EUR_TO_RUB
            elif currency == '₽' or 'руб' in currency.lower(): price_rub = price_num
            elif currency == '$': price_rub = price_num * 95
        except: pass

        # Почта и тип
        mail_m = re.search(r'data-f-mail="([^"]*)"', body)
        mail = mail_m.group(1) if mail_m else "?"
        type_m = re.search(r'data-f-type="([^"]*)"', body)
        acc_type = type_m.group(1) if type_m else ""

        results.append({
            'source': 'FunPay', 'id': item_id,
            'ar': ar, 'server': server, 'desc': description,
            'price_rub': price_rub, 'price_orig': f"{price_str} {currency}",
            'mail': mail, 'type': acc_type, 'url': url
        })
    return results


def filter_accounts(all_accounts):
    cat1 = []
    cat2 = []
    for a in all_accounts:
        ar = a.get('ar')
        price = a.get('price_rub')
        desc = a.get('desc', '')

        if ar and 50 <= ar <= 60 and price and price <= 3000:
            cat1.append(a)

        # Категория 2: стартёры до 750₽ (увеличили лимит, чтобы видеть аккаунты по $7-8)
        if ar and ar <= 20 and price and price <= 750:
            if price < 50:  # пропускаем мусор "под заказ" за 1₽
                continue
            if should_skip_starter_only(desc):
                continue
            cat2.append(a)

    cat1.sort(key=lambda x: x['price_rub'] or 999999)
    cat2.sort(key=lambda x: x['price_rub'] or 999999)
    return cat1, cat2

import sys

def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        # Убираем символы, которые не пролазят в текущую кодировку консоли
        print(text.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding))

def main():
    all_accounts = []
    try:
        all_accounts.extend(parse_funpay())
    except Exception as e:
        safe_print(f"⚠ FunPay ошибка: {e}")

    cat1, cat2 = filter_accounts(all_accounts)
    seen = load_seen()

    new_cat1 = [a for a in cat1 if a['id'] not in seen.get('cat1', {})]
    new_cat2 = [a for a in cat2 if a['id'] not in seen.get('cat2', {})]

    # Обновляем историю
    seen.setdefault('cat1', {}).update({a['id']: a['price_rub'] for a in cat1})
    seen.setdefault('cat2', {}).update({a['id']: a['price_rub'] for a in cat2})
    save_seen(seen)

    output = []
    if new_cat1:
        output.append(f"🔥 FunPay: AR 50-60, до 3000₽ — {len(new_cat1)} новых:")
        for i, a in enumerate(new_cat1[:20]):
            rub = f"{a['price_rub']:.0f}₽" if a['price_rub'] else "?"
            output.append(f"{i+1}. AR{a['ar']} | {rub} ({a['price_orig']}) | {a.get('server','')} | 📧{a.get('mail','')}")
            output.append(f"   {a['desc'][:100]}")
            output.append(f"   🔗 {a['url']}")
    else:
        output.append("🔥 FunPay: новых нет")

    output.append("")
    if new_cat2:
        output.append(f"🌱 FunPay: AR≤20, до 750₽, с ивентовыми — {len(new_cat2)} новых:")
        for i, a in enumerate(new_cat2[:20]):
            rub = f"{a['price_rub']:.0f}₽" if a['price_rub'] else "?"
            output.append(f"{i+1}. AR{a['ar']} | {rub} ({a['price_orig']})")
            output.append(f"   {a['desc'][:100]}")
            output.append(f"   🔗 {a['url']}")
    else:
        output.append("🌱 FunPay: новых нет")

    output.append(f"\n📊 Всего на FunPay: {len(all_accounts)} лотов. В фильтрах: {len(cat1)}+{len(cat2)}")
    safe_print("\n".join(output))

if __name__ == "__main__":
    # Для Windows принудительно ставим UTF-8 если это возможно
    if sys.platform == "win32":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        except: pass
    main()
