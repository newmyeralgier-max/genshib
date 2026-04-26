# Genshib monitor — roadmap

Этот документ — рабочий план по доработкам, согласованный с владельцем
после PR #2. Каждая глава — отдельный пункт, с конкретными файлами,
сигнатурами, тестами и крайними случаями. Цель — чтобы любой запуск
на любую часть мог вестись без потери контекста.

Готовые задачи помечать `[x]`, в работе — `[~]`, не начатые — `[ ]`.

---

## 1. [ ] Кросс-сорс матчинг «PayGame ↔ FunPay»

**Идея.** На каждом PayGame-лоте показывать «на FunPay похожие лоты
торгуются за ~Y₽», и наоборот. Это превращает фид в источник
арбитражных сигналов.

### 1.1 Канонизация лота

Вытащить из `desc` нормализованный «отпечаток»:
- `ar_bucket` — округление AR до 5 (50, 55, 60).
- `event_5stars` — `frozenset(EVENT_5STAR ∩ desc.lower())`. Дальше
  работаем с этим набором, а не с сырым описанием.
- `count_event_5` — мощность множества (0, 1, 2, 3+).

```python
# scripts/match.py (новый файл)
def fingerprint(item: dict) -> tuple[int | None, frozenset[str], int]:
    ar = item.get("ar")
    bucket = (ar // 5) * 5 if ar is not None else None
    text = (item.get("desc_full") or item.get("desc") or "").lower()
    chars = frozenset(n for n in EVENT_5STAR if n in text)
    return bucket, chars, len(chars)
```

### 1.2 Похожесть

```python
def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b: return 0.0
    return len(a & b) / max(1, len(a | b))

def similar(left, right) -> bool:
    al, cl, _ = fingerprint(left)
    ar, cr, _ = fingerprint(right)
    if al != ar: return False                  # разные AR-бакеты
    if min(len(cl), len(cr)) == 0: return False
    return jaccard(cl, cr) >= 0.5              # хотя бы половина состава общая
```

### 1.3 Поиск в обе стороны

Для каждого PayGame-лота — `top_k=3` ближайших FunPay лотов того же
AR-бакета, отсортированных по jaccard DESC, цене ASC.
И наоборот для FunPay.

Не пихать соответствия в `categorize`. Делать на этапе сборки отчёта,
после того как cat1/cat2 обоих сайтов уже посчитаны:

```python
# scripts/monitor_all.py
matches_pg2fp = build_matches(pg["cat1"] + pg["cat2"], fp["cat1"] + fp["cat2"])
matches_fp2pg = build_matches(fp["cat1"] + fp["cat2"], pg["cat1"] + pg["cat2"])
```

`matches_*: dict[str, list[dict]]` — id → список похожих лотов с другого
источника, уже отсортированных.

### 1.4 Вывод в .md

Под каждым лотом, у которого есть match, добавить блок (свернуто):
```
- AR55 · 280₽ · PayGame · 👤Petya
  - Скирк + Сигна
  - https://paygame.ru/offers/12345
  - 🔁 На FunPay аналоги:
    - 720₽ — funpay.com/lots/offer?id=AAA (jaccard 0.66)
    - 750₽ — funpay.com/lots/offer?id=BBB (jaccard 0.50)
    - 810₽ — funpay.com/lots/offer?id=CCC (jaccard 0.50)
```

Если match-ов нет — блок просто не выводится (ничего не сломаем).

### 1.5 Тесты

- `test_fingerprint_buckets`: AR 51→50, AR 55→55, AR 59→55.
- `test_jaccard`: `{a,b} vs {a,c}` = 1/3.
- `test_match_full_pipeline`: 2 синтетических PayGame-лота, 3 FunPay
  лота — проверяем, что для каждого PG получаем правильный top-3.

### 1.6 Тонкости

- **Шум в desc**. Пробежать смоук-тест на реальных данных: считать,
  у какой доли лотов `event_5stars == ∅`. Если >50% — match-ов будет
  слишком мало; тогда дополнительно matchить по неявным маркерам
  (количество легендарок, текст «топ старт», итд) — но это вторая
  итерация.
- **Производительность**. n×m, где n,m ≤ ~500 — это 250k сравнений,
  не страшно. Если дойдёт до >5k×5k — тогда индекс по AR-бакету.

---

## 2. [ ] Медиана рынка + тег «📉 N% ниже рынка»

**Идея.** Для каждого лота — посчитать медиану FunPay по «классу
лота» и пометить лот, если он сильно дешевле этой медианы.

### 2.1 Класс лота

```python
def market_class(item) -> tuple[int | None, int]:
    ar_bucket = (item["ar"] // 5) * 5 if item.get("ar") is not None else None
    n_event = sum(1 for n in EVENT_5STAR if n in (item.get("desc") or "").lower())
    n_event_capped = min(n_event, 4)            # 0,1,2,3,4+
    return ar_bucket, n_event_capped
```

### 2.2 Медиана

База референса — **только FunPay**, потому что это та цена, по которой
ты будешь *продавать*. Считается один раз за прогон:

```python
def fp_median_by_class(fp_items: list[dict]) -> dict[tuple, float]:
    from statistics import median
    buckets: dict[tuple, list[float]] = {}
    for it in fp_items:
        if it.get("price_rub") is None: continue
        buckets.setdefault(market_class(it), []).append(it["price_rub"])
    return {k: median(v) for k, v in buckets.items() if len(v) >= 5}  # min sample
```

`min sample = 5` — если в классе меньше 5 лотов на FunPay, медиане не
доверяем, дисконт не считаем (NULL).

### 2.3 Тег

Для каждого лота на PayGame **и** на FunPay:
```python
def discount_pct(item, medians) -> float | None:
    cls = market_class(item)
    m = medians.get(cls)
    if m is None or item.get("price_rub") is None: return None
    return round((1 - item["price_rub"] / m) * 100, 1)
```

В .md: если `discount ≥ 30%` → префикс **«🔥 -45% от рынка»**.

### 2.4 Флаг `--hot-only`

`monitor_all.py --hot-only` — в .md остаются только лоты с
`discount ≥ 30%`. Удобно для быстрого скан-режима.

### 2.5 Тесты

- `test_market_class`: AR54+2★ → (50, 2).
- `test_median_skips_small_sample`: класс с 4 лотами → не в карте.
- `test_discount_pct`: цена 200, медиана 1000 → -80%.

### 2.6 Тонкости

- Медиана **по FunPay**, потому что это цена продажи. Считать по
  объединению — некорректно, мы и так знаем что PayGame дешевле.
- Если у лота нет ивентовых 5★ — класс `(ar, 0)`. Это валидный класс
  для cat1, но в нём шумно (много «стартовых»). Сэмпл должен помочь.
- Хранить медианы во внутреннем кэше прогона; не сохранять между
  запусками (источник правды — текущая FunPay-витрина).

---

## 5. [ ] Telegram-нотификации

**Контекст.** Cron уже есть (Гермес/OpenClav, раз в 6 часов). Нам
нужна только Telegram-часть: бот, который при каждом прогоне шлёт
тебе короткое сообщение с самыми «горячими» лотами.

### 5.1 Конфиг

Через переменные окружения, никаких хардкодов:
- `GENSHIB_TG_BOT_TOKEN` — токен бота, полученный у @BotFather.
- `GENSHIB_TG_CHAT_ID` — твой chat id (узнаётся у @userinfobot).
- (опц.) `GENSHIB_TG_MIN_DISCOUNT=30` — слать только при discount ≥X%.
- (опц.) `GENSHIB_TG_LIMIT=10` — максимум лотов в одном сообщении.

Если `GENSHIB_TG_BOT_TOKEN` не задан — модуль молчит, ничего не шлём.
Это позволяет крутить скрипт и без Telegram.

### 5.2 Модуль

```python
# scripts/notifier.py (новый)
def notify_telegram(hot: list[dict], summary: str) -> None:
    token = os.environ.get("GENSHIB_TG_BOT_TOKEN")
    chat = os.environ.get("GENSHIB_TG_CHAT_ID")
    if not (token and chat):
        return
    if not hot:
        return                      # тихий прогон, не шумим
    text = _format(hot, summary)    # MarkdownV2, см. 5.3
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    fetch_json(url, post={
        "chat_id": chat,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    })
```

`fetch` в `common.py` сейчас только GET — расширить до POST или
добавить `fetch_json(url, post=...)`. Минимальная правка: 5 строк.

### 5.3 Что шлём

Из `monitor_all.py` после построения отчёта:
- собираем «горячие лоты»: `discount ≥ 30%` + только `new` (не падения,
  чтоб не дублировать прошлые сообщения), отсортировано по дисконту;
- режем до `limit` штук;
- собираем сообщение:

```
🔥 *Genshib · 24.04 22:30*
PayGame: 142 в выдаче, 5 новых, 3 горячих
FunPay: 435 в выдаче, 5 новых, 1 горячий

🔥 *PayGame*
\\-58% · AR55 280₽ — Скирк + Сигна — paygame.ru/offers/12345
\\-44% · AR60 320₽ — Хутао + Фурина — paygame.ru/offers/67890

🔥 *FunPay*
\\-31% · AR55 489₽ — Йоимия + ЛИННЕЯ — funpay.com/lots/offer?id=AAAAA
```

Все спецсимволы экранировать под MarkdownV2 (`_`, `*`, `[`, ...).

### 5.4 Тесты

- `test_format_no_tokens_skips`: без env-переменных — модуль не падает,
  ничего не шлёт.
- `test_format_escapes_markdown_v2`: «AR60_(тест)» → `AR60\\_\\(тест\\)`.
- `test_filter_only_new_hot`: на синтетике — отбирается только то, что
  одновременно `new=True` и `discount≥thr`.

Сетевой вызов в Telegram **в тестах не делаем** — мокаем `fetch_json`.

### 5.5 Тонкости

- Лимит сообщения Telegram = 4096 символов. При >limit лотов — режем.
- `disable_web_page_preview=True` — иначе превью FunPay засрёт ленту.
- Если 429 (rate limit) — ретрай через `retry_after` из ответа.
- В env-конфиге **не хранить токен в коде**. Если понадобится в .bat —
  ставить переменную в `setx GENSHIB_TG_BOT_TOKEN ...`, а не в .bat.

---

## 6. [ ] Топ-N «горячих» в шапке отчёта

**Идея.** В самом верху .md — отдельный блок с самыми ценными лотами,
не разбитый по источникам. 90% обычных просмотров отчёта решаются
одним взглядом на эту секцию.

### 6.1 Алгоритм

```python
def hot_pick(fp_data, pg_data, medians, n=10) -> list[dict]:
    pool = []
    for d, source in ((fp_data, "FunPay"), (pg_data, "PayGame")):
        for cat in ("cat1", "cat2"):
            for lot in d[f"new_{cat}"]:           # только NEW
                disc = discount_pct(lot, medians)
                if disc is None or disc < 20:     # порог
                    continue
                pool.append({**lot, "_disc": disc, "_cat": cat})
    pool.sort(key=lambda x: -x["_disc"])
    return pool[:n]
```

### 6.2 Вывод

```markdown
## 🔥 Top-10 hot lots

| disc | source | AR | price | event 5★ | url |
|---|---|---|---|---|---|
| -58% | PayGame | 55 | 280₽ | Скирк, Сигна | paygame.ru/offers/12345 |
| -44% | PayGame | 60 | 320₽ | Хутао, Фурина | paygame.ru/offers/67890 |
| ...  |
```

Markdown-таблица + ссылки. Пустая таблица не выводится — пишем
«сейчас горячих лотов нет (порог −20%)».

### 6.3 Тесты

- `test_hot_pick_sort_desc`: -10%, -50%, -30% → -50, -30, -10.
- `test_hot_pick_threshold`: -10% при пороге 20% → отброшен.
- `test_hot_pick_only_new`: лот, который не в `new_*` — не в пуле.
- `test_hot_pick_limit_n`.

### 6.4 Тонкости

- Только `new_*` — иначе шапка будет одна и та же изо дня в день.
- Если хочется иногда смотреть «всё, что висит дёшево» — отдельный
  флаг `--hot-include-existing`.

---

## 7. [ ] История цен

**Идея.** Сейчас seen хранит только текущую цену. Расширяем до истории
наблюдений по каждому id, чтоб видеть тренды и считать «сколько лот
висит без покупателя».

### 7.1 Формат seen v3

Текущий v2:
```json
{"cat1": {"123": {"price": 500, "first_seen": 1700, "last_seen": 1800}}}
```

Новый v3 — обратно совместим:
```json
{
  "_version": 3,
  "cat1": {
    "123": {
      "price": 500,
      "first_seen": 1700,
      "last_seen": 1800,
      "history": [
        {"ts": 1700, "price": 600},
        {"ts": 1750, "price": 550},
        {"ts": 1800, "price": 500}
      ]
    }
  }
}
```

Чтение v2 → автоматически проставляем `history=[{ts: last_seen,
price: price}]`.

### 7.2 Запись

В `update_seen`: при изменении цены добавляем запись в history.
Не дублируем подряд одинаковые. Кап на длину (`max_history=50`),
старые точки усредняем по неделям, чтобы файл не распух.

### 7.3 Использование

- В шапке лота: `📉 за 7д цена 600→500 (-17%)`.
- Метрика «висит N дней» = `(now - first_seen) / 86400`.
- Нагретая медиана v2 (см. главу 2) может опционально брать
  не «текущая цена FunPay», а «её 7-дневная средняя».

### 7.4 Тесты

- `test_seen_v2_to_v3_migration`: старый файл читается, history
  добивается из last_seen.
- `test_history_no_duplicates`: подряд одинаковые цены не пишутся.
- `test_history_capped`: добавили 100 точек — в файле <=50.

### 7.5 Тонкости

- Размер seen.json. На 500 лотах × 50 точек × 30 байт ≈ 750 KB.
  Терпимо. Если станет проблемой — отдельный файл `history.json`.
- Сейчас seen чистится по TTL `last_seen > 14d`. Перенести на
  `last_seen > 30d` или хотя бы дать env-настройку.

---

## 8. [ ] «Исчезли = продали» (sold/disappeared)

**Идея.** Лот вчера был в seen, сегодня в выдаче его нет → скорее
всего, продан. Это сигнал того, какие лоты быстро уходят.

### 8.1 Алгоритм

После сбора `cat1`/`cat2` текущего прогона:
```python
def diff_disappeared(seen_cat: dict, fresh_ids: set[str]) -> list[dict]:
    out = []
    for iid, rec in list(seen_cat.items()):
        if iid in fresh_ids:
            continue
        # был, но больше нет
        out.append({"id": iid, **rec})
    return out
```

Удалять из seen **не сразу**. Дать grace 1 прогон («может, временно
выпал из выдачи из-за пагинации/глюка API»). Помечать в seen полем
`missed_runs`. После 2-х промахов считать «продано», логировать в
отчёт, удалять из seen.

### 8.2 Вывод

В шапке секции:
```
> 📉 ушло из выдачи за этот прогон: 12 лотов (вероятно, проданы)
```

Опц. отдельная секция «🛒 Похоже на свежие продажи» с топ-N по
скорости (короче всех висели).

### 8.3 Тесты

- `test_disappeared_grace`: 1 промах → не считаем продан.
- `test_disappeared_after_two_misses`: 2 промаха → попадает в список
  и удаляется из seen.
- `test_disappeared_resets_on_return`: лот вернулся через прогон —
  `missed_runs` сбрасывается.

### 8.4 Тонкости

- Особенно осторожно с PayGame-пагинацией: если падёт middle-страница,
  пропадут лоты с этой страницы → не считать их «проданы». Поэтому
  если `ok=False` (см. `fetch_all_offers`), пропускать diff в этом
  прогоне целиком.
- На FunPay лента бывает урезанной (если их сервер упал) — то же.

---

## 10. [ ] Дедуп между источниками

**Идея.** Один и тот же продавец иногда вешает одинаковый аккаунт и
на FunPay, и на PayGame. Если описания совпадают на ~90%, помечать
«дубль с другой площадки», чтоб не считать его дважды.

### 10.1 Алгоритм

```python
def is_dup(a, b) -> bool:
    if a["source"] == b["source"]: return False
    al, cl, _ = fingerprint(a)
    ar_, cr, _ = fingerprint(b)
    if al != ar_: return False
    if abs(a["price_rub"] - b["price_rub"]) / max(a["price_rub"], 1) > 0.15:
        return False                # цены различаются больше чем на 15%
    if jaccard(cl, cr) < 0.7: return False
    # доп. сильный сигнал: совпадение продавца
    if a["seller"].lower() == b["seller"].lower():
        return True
    return jaccard(cl, cr) >= 0.85
```

### 10.2 Вывод

В .md рядом с лотом:
```
- AR55 · 280₽ · PayGame · 👤Petya
  - 🔗 дубль с другой площадки: funpay.com/lots/offer?id=AAA
```

В summary:
```
> 🔗 кросс-сорс дублей: 7 пар
```

### 10.3 Тесты

- `test_dup_same_seller_partial_overlap`: один селлер, jaccard 0.6 →
  считаем дублем.
- `test_dup_diff_seller_full_overlap`: разные селлеры, jaccard 0.9 →
  считаем дублем.
- `test_dup_diff_price`: цена 200 vs 800 → не дубль.
- `test_dup_same_source`: оба FunPay → не дубль (никогда).

### 10.4 Тонкости

- Селлер = очень сильный признак. Часто один и тот же продавец юзает
  ник «Petya» на FunPay и «Petya» на PayGame — стоит сразу матчить
  селлеров между сайтами как индекс.
- На PayGame иногда добавляют слово «АВТОВЫДАЧА» в начало title — на
  FunPay нет. Канонизация title (стрипать эмодзи, верхний регистр,
  спец-маркеры типа `автовыдача`) сильно помогает.

---

## Порядок реализации (предложение)

1. **Сначала фундамент** (для всех остальных пунктов он нужен):
   главы **1.1–1.2 (fingerprint + jaccard)** в `scripts/match.py`.
2. **Глава 2** (медианы) — на этом фундаменте, недорого.
3. **Глава 6** (Top-N hot) — тривиально поверх (1) + (2).
4. **Глава 10** (дедуп) — fingerprint уже готов.
5. **Глава 7** (история) — отдельный пласт seen-файла, аккуратная
   миграция.
6. **Глава 8** (sold/disappeared) — поверх (7).
7. **Глава 5** (Telegram) — последним, т.к. удобно слать сразу
   «Top-N hot» из (6).

Каждая глава — отдельный коммит в один и тот же PR (или отдельные
маленькие PR-ы), все с unit-тестами.

---

## Что осознанно НЕ делаем

- **Не расширяем фильтры.** Никаких новых регионов, диапазонов AR,
  цен. Это явное требование владельца.
- **Не добавляем планировщик** (cron уже есть в Hermes/OpenClav).
- **Не лезем в frontend/UI.** Отчёт остаётся .md, открывается в
  Antigravity / любом MD-вьювере.

---

_Документ — рабочий, обновляем по ходу. Сделано — `[x]`, в работе — `[~]`._
