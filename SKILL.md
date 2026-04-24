---
name: genshin-accounts-hub
version: 4.0
category: gaming
description: Мониторинг аккаунтов Genshin Impact на FunPay и PayGame с выводом в Markdown. JSON-LD парсинг PayGame, фильтрация мусора, детект падения цены.
tags: [genshin, funpay, paygame, accounts, parser, monitor, cron]
---

# Genshin Accounts Hub

Мониторинг аккаунтов Genshin Impact на FunPay и PayGame.

## Метод

Оба сайта **НЕ** работают через browser_navigate (FunPay — Cloudflare, PayGame — таймаут).
Единственный рабочий метод — **Python urllib** с gzip и `decode('utf-8', errors='replace')`.

- **FunPay** — HTML с `<a class="tc-item">` и `data-f-*` атрибутами. Регулярки.
- **PayGame** — Next.js SPA с SSR. Парсим **JSON-LD `<script type="application/ld+json">`** (Schema.org `CollectionPage` → `ItemList`). CSS-классы вида `sc-17v71la-*` игнорируем, они авто-генерируемые и меняются на каждом деплое.

## URL

| Сайт | URL аккаунтов |
|------|--------------|
| FunPay | `https://funpay.com/lots/696/` |
| PayGame | `https://paygame.ru/games/genshin-impact/offers?type=account` |

## Запуск

```bash
# Единый прогон обоих сайтов, вывод в scripts/report.md
python3 scripts/monitor_all.py

# Сбросить seen и начать заново
python3 scripts/monitor_all.py --reset

# Только один сайт
python3 scripts/monitor_all.py --only funpay
python3 scripts/monitor_all.py --only paygame

# Кастомный путь отчёта
python3 scripts/monitor_all.py --out ~/genshin_report.md
# или через env
GENSHIB_REPORT=~/genshin_report.md python3 scripts/monitor_all.py

# Отдельные скрипты (текстовый stdout, без Markdown)
python3 scripts/genshin_monitor.py      # FunPay
python3 scripts/paygame_monitor.py      # PayGame

# Разовый инспектор (без state, без Markdown)
python3 scripts/genshin_parser.py --site paygame --limit 10
python3 scripts/genshin_parser.py --site funpay --ar-min 55 --ar-max 60 --server европ
```

## Категории фильтрации (жёстко Европа)

1. 🔥 **cat1**: AR 50-60, цена **50-3000₽**, сервер Европа, без нероллов, без "договорная"/"под заказ"/"фарм"/"услуг"/"сборк" мусора.
2. 🌱 **cat2**: AR 0-20, цена **50-700₽**, сервер Европа, обязательно хотя бы **один ивентовый 5★** в описании (иначе скипаем), без нероллов, без мусора.

Все цены приводятся в рубли: FunPay отдаёт в $ (или €) — конвертируется по курсу из env (`GENSHIB_USD_RUB`, `GENSHIB_EUR_RUB`) или дефолту.

### Стандартные vs ивентовые 5★

**Критично для cat2.** Юзер не хочет аккаунты где ТОЛЬКО стандартные 5★ (Дилюк/Джинн/Кэцин/Мона/Цици/Тигнари/Дехья/Мидзуки) — такие скипаются.
Если найден хотя бы один ивентовый 5★ (Скирк/Мавуика/Фурина/Арлекино/Ху Тао/... — полный список см. `scripts/common.py:EVENT_5STAR`) — показываем.
Если 5★ в описании вообще не упомянуты — для cat2 тоже скипаем.

## Как работает seen и что такое "новое"

Формат `scripts/genshin_seen.json` и `scripts/paygame_seen.json`:
```json
{
  "cat1": {
    "67859153": {"price": 881.0, "first_seen": 1743000000, "last_seen": 1743100000}
  },
  "cat2": {}
}
```
Старый формат `{id: price}` читается как legacy (без временных меток).

- На **первом запуске** (seen пустой / удалён) скрипт записывает всё найденное в seen и печатает только `[init]`. Это чтобы не спамить 600+ записями.
- На последующих запусках отчёт содержит:
  - **🆕 Новые** — id, которых раньше не было в seen;
  - **📉 Цена упала** — id, видели раньше, но цена упала ≥ 5%;
  - краткие счётчики "всего в сегменте / новых / подешевевших".
- seen обновляется после каждого запуска. Старые записи не чистятся автоматически (оффер может снова всплыть через неделю — хочется корректно помнить его).

### Скинуть seen (показать всё как новое)
```bash
python3 scripts/monitor_all.py --reset   # сотрёт оба seen-файла и сделает init
# или вручную
rm -f scripts/*_seen.json
```

## Выход

По умолчанию пишется в `scripts/report.md` (Markdown, с кликабельными ссылками). Путь переопределяется флагом `--out` или env `GENSHIB_REPORT`. В консоль печатается только сводка по счётчикам и путь к отчёту.

`scripts/report.md` — рантайм-артефакт, из git исключён.

## FunPay: структура данных

HTML содержит `<a class="tc-item">` с data-атрибутами (на внешнем теге `<a>`):

```
<a href="https://funpay.com/lots/offer?id=XXXXX" class="tc-item ..."
   data-f-ar="55"
   data-f-type="Прокачанный"   — Стартовый / Нероленный / Прокачанный
   data-f-hero="Итэр"          — Итэр / Люмин
   data-f-mail="Привязана"
   data-f-platform="Любая">
  <div class="tc-desc-text">Описание</div>
  <div class="tc-server-inside">Европа</div>
  <div class="tc-price"><div>12.34</div><span class="unit">$</span></div>
  <div class="media-user-name">SellerName</div>
</a>
```

FunPay лента — ~3200 лотов, lazy-load не нужен (всё в первом HTML).

## PayGame: структура данных

JSON-LD (самый надёжный источник):
```json
{
  "@type": "ItemList",
  "itemListElement": [{
    "item": {
      "@type": "Product",
      "name": "...",
      "description": "... . Аккаунты Genshin Impact. Гарантия: ...; AR: 55; Неролл: Да; ...",
      "url": "https://paygame.ru/offers/XXXXX",
      "offers": {"price": 1099, "priceCurrency": "RUB", "seller": {"name": "..."}}
    }
  }]
}
```

Сервер (`Европа`/`Азия`/`США`/...) в JSON-LD отсутствует — вытаскиваем из HTML возле `href="/offers/<id>"` (span `Сервер<!-- -->:</span><span>Европа</span>`).

На первой странице ~25 лотов. Пагинация не задокументирована; больше не парсим.

## Крон

```python
cronjob(action='create',
        name='🎮 Genshin Monitor — FunPay + PayGame',
        prompt='Запусти python3 ~/repo/scripts/monitor_all.py и пришли содержимое scripts/report.md.',
        schedule='every 1h')
```

## Ограничения и известные фишки

1. **FunPay** — цены бывают в $ или €; читаем `<span class="unit">`, конвертим по курсу.
2. **FunPay "договорная"** — лот с ценой 1-2₽ означает "пиши в ЛС". Фильтруются по `price < 50₽` и по словам "договорн" в описании.
3. **PayGame пагинация** — только первая страница. Если нужно больше — смотреть XHR `/api/...` в DevTools.
4. **curl** — плохо работает с PayGame (кодировка). Использовать только Python urllib.
5. **seen** не чистится автоматом. Если файл распух сверх разумного — `--reset`.
