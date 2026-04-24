---
name: genshin-accounts-hub
version: 3.0
category: gaming
description: Поиск и мониторинг аккаунтов Genshin Impact на FunPay и PayGame. Парсинг через Python urllib, фильтрация по AR/серверу/персонажам, крон-мониторинг.
tags: [genshin, funpay, paygame, accounts, parser, monitor, cron]
---

# Genshin Accounts Hub

Поиск, фильтрация и мониторинг аккаунтов Genshin Impact на FunPay и PayGame.

## Метод

Оба сайта **НЕ** работают через browser_navigate (FunPay — Cloudflare, PayGame — таймаут).
Единственный рабочий метод — **Python urllib** с gzip (не curl — у curl проблемы с кодировкой PayGame).

## URL

| Сайт | URL аккаунтов |
|------|--------------|
| FunPay | `https://funpay.com/lots/696/` |
| PayGame | `https://paygame.ru/games/genshin-impact/offers?type=account` |

## Стандартные vs Ивентовые 5★ персонажи

**Критично для фильтрации AR≤20 аккаунтов!** Пользователь не хочет аккаунты где ТОЛЬКО стандартные 5★.

### Стандартные 5★ (постоянный баннер, актуально 5.4+)
Дилюк, Джинн, Кэ Цин, Мона, Цици, Тигнари, Дехья, **Мидзуки** (добавлена в 5.4!)

### Ивентовые 5★ (ограниченные)
Венди, Эола, Кадзуха, Чжун Ли, Гань Юй, Сяо, Ху Тао, Йоимия, Шэнь Хэ, Аяка, Райдэн, Аято, Итто, Кокоми, Яэ Мико, Нахида, Сайно, Вандерер/Скиталец, Альхаисам, Фурин, Нёвиллет, Навия, Клавирин, Сигвин, Лизли, Муалани, Кинич, Часка, Мавуика, Ситлали, Шилонен, Арлекино, Тарталья/Чайлд, Альбедо, Нилу, Эмилия, Коломбина, Инеффа, Эскофье, Флинс, Е Лань, Тиори, Скирк и др.

### Логика фильтра
- Если в описании есть хоть ОДНА ивентовая 5★ → показываем (даже если есть стандартные)
- Если есть ТОЛЬКО стандартные 5★ → пропускаем
- Если 5★ не упомянуты → показываем (может быть неролл/стартовый)

## FunPay: структура данных

HTML содержит `<a class="tc-item">` с data-атрибутами:

```
<a href="https://funpay.com/lots/offer?id=XXXXX" class="tc-item lazyload-hidden"
   data-f-ar="55"          — Adventure Rank
   data-f-type="Прокачанный" — Стартовый/Нероленный/Прокачанный
   data-f-hero="Итэр"      — Итэр/Люмин
   data-f-mail="Привязана" — Почта привязана/не привязана
   data-f-platform="Любая"
>
  <div class="tc-desc-text">Описание</div>
  <div class="tc-server-inside">Европа</div>
  <div class="tc-price"><div>123.45</div><span class="unit">€</span></div>
  <div class="media-user-name">SellerName</div>
</a>
```

## PayGame: структура данных

Next.js SPA, но **серверный рендеринг** отдаёт HTML с текстовыми фрагментами.
Парсинг через последовательное сканирование текстовых нод:
- Описание → Цена (NNN ₽) → Мета (Сервер, Гарантия, Смена данных) → Продавец

## Запуск парсера (разовый поиск)

```bash
python3 ~/.hermes_5/skills/gaming/genshin-accounts-hub/scripts/genshin_parser.py
python3 ~/.hermes_5/skills/gaming/genshin-accounts-hub/scripts/genshin_parser.py --site funpay --ar-min 55 --ar-max 60 --server европ
python3 ~/.hermes_5/skills/gaming/genshin-accounts-hub/scripts/genshin_parser.py --site paygame --limit 10
```

## Мониторинг (крон)

Скрипт `~/.hermes/scripts/genshin_monitor.py` — мониторинг с отслеживанием новых предложений.

### Категории фильтрации
1. 🔥 **AR 50-60, до 3000₽** — все аккаунты этого ранга и цены
2. 🌱 **AR≤20, до 500₽** — только с ивентовыми 5★ (без "только стандартные"), мин. цена 50₽ (отсев мусора "под заказ")

### Как работает
- Парсит оба сайта, фильтрует по категориям
- Хранит seen IDs в `/tmp/genshin_seen.json`
- Показывает только **новые** (не отправленные ранее) предложения
- Сортировка по цене (дешёвые первыми)

### Настройка крона
```python
# Создание крон-джоба (каждый час)
cronjob(action='create',
        name='🎮 Genshin Monitor — аккаунты FP+PG',
        prompt='Запусти python3 ~/.hermes/scripts/genshin_monitor.py и отправь вывод пользователю.',
        schedule='every 1h',
        script='genshin_monitor.py')
```

### Скинуть seen-файл (показать все как новые)
```bash
rm -f /tmp/genshin_seen.json
```

## Параметры CLI (genshin_parser.py)

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `--site` | all | funpay / paygame / all |
| `--ar-min` | 0 | Минимальный AR |
| `--ar-max` | 999 | Максимальный AR |
| `--server` | None | Фильтр по серверу (подстрока: "европ", "asia", "america") |
| `--limit` | 30 | Максимум результатов |

## Ограничения

1. **FunPay**: lazy-load — начальная страница содержит ~3240 аккаунтов. Пагинация не требуется.
2. **PayGame**: только первая страница (~20 аккаунтов). Для большего нужно API пагинации.
3. **Browser tool**: НЕ использовать — оба сайта блокируют/таймаутят.
4. **curl**: PayGame возвращает кодировку windows-1251 → нужен Python urllib с `decode('utf-8', errors='replace')`.
5. **FunPay цены в EUR**: конвертация по курсу 105₽/€ для отображения пользователю.

## Результаты (16.04.2026)

- **FunPay**: 3240 аккаунтов, цены от 1.26€ до 4194€
- **PayGame**: 20+ аккаунтов, цены в рублях
- **AR 50-60 ≤3000₽**: ~796 аккаунтов (FunPay)
- **AR≤20 ≤500₽ с ивентовыми**: ~110 аккаунтов (FunPay, после отсева "только стандартные")
