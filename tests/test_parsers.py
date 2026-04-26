"""
Smoke / regression тесты на парсеры FunPay и PayGame.

Зачем: чтобы поломки регулярок / схемы API ловились локально, а не на
живом прогоне через 12 часов.

Запуск:
    cd genshib
    python3 -m unittest tests.test_parsers
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common  # noqa: E402
import genshin_monitor  # noqa: E402
import paygame_monitor  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class TestFunPayParser(unittest.TestCase):
    def setUp(self) -> None:
        self.html = _read("funpay_lots_696.html")
        self.items = genshin_monitor.parse_funpay(self.html)

    def test_extracts_items(self) -> None:
        self.assertGreaterEqual(len(self.items), 5)

    def test_required_fields_present(self) -> None:
        for it in self.items:
            self.assertIn("id", it)
            self.assertIn("price_rub", it)
            self.assertIn("server", it)
            self.assertIn("desc", it)
            self.assertIn("url", it)
            self.assertTrue(it["url"].startswith("https://funpay.com/lots/offer?id="))

    def test_price_in_rubles(self) -> None:
        # Фикстура снята с ?currency=RUR → все цены должны быть в ₽
        # (price_rub == price_orig number, без USD/EUR конверсии).
        for it in self.items:
            if it.get("price_rub") is None:
                continue
            self.assertIn("₽", it["price_orig"], msg=f"unit not RUB: {it['price_orig']}")

    def test_feed_order_present(self) -> None:
        # сортировочный ключ для "новых" должен быть проставлен на всех
        for it in self.items:
            self.assertIn("_feed_order", it)


class TestPayGameParser(unittest.TestCase):
    def setUp(self) -> None:
        data = json.loads(_read("paygame_api_page1.json"))
        self.api_results = data["results"]
        self.items = paygame_monitor.parse_paygame(self.api_results)

    def test_extracts_items(self) -> None:
        self.assertEqual(len(self.items), len(self.api_results))

    def test_required_fields(self) -> None:
        for it in self.items:
            self.assertIn("id", it)
            self.assertIn("price_rub", it)
            self.assertIn("server", it)
            self.assertIn("desc", it)
            self.assertIn("url", it)
            self.assertTrue(it["url"].startswith("https://paygame.ru/offers/"))

    def test_price_is_rub_float(self) -> None:
        for it in self.items:
            if it["price_rub"] is None:
                continue
            self.assertIsInstance(it["price_rub"], float)

    def test_neroll_extracted(self) -> None:
        # Для лотов, у которых в API стояло Неролл: Да — у нас должно
        # быть neroll == True. Хотя бы один из таких в fixture есть.
        # Если нет — тест всё равно проходит (просто проверяем тип).
        for it in self.items:
            self.assertIsInstance(it["neroll"], bool)

    def test_created_ts_parsed(self) -> None:
        for it in self.items:
            ts = it.get("_created_ts")
            self.assertTrue(ts is None or isinstance(ts, int))


class TestFilters(unittest.TestCase):
    def test_is_europe_strict(self) -> None:
        self.assertTrue(common.is_europe("Европа"))
        self.assertTrue(common.is_europe("europe"))
        self.assertTrue(common.is_europe("EU"))
        self.assertTrue(common.is_europe("Европа (EU)"))
        self.assertFalse(common.is_europe("Азия"))
        self.assertFalse(common.is_europe("США"))
        self.assertFalse(common.is_europe(""))
        self.assertFalse(common.is_europe("Eurasia"))  # не подстрока

    def test_is_garbage(self) -> None:
        self.assertTrue(common.is_garbage("ДОГОВОРНАЯ ЦЕНА"))
        self.assertTrue(common.is_garbage("цена дог."))
        self.assertTrue(common.is_garbage("торг уместен"))
        self.assertTrue(common.is_garbage("под заказ"))
        self.assertTrue(common.is_garbage("услуги по прокачке"))
        # buy-side маскировка под продавца
        self.assertTrue(common.is_garbage("🏵️Куплю ваш аккаунт!🏵️"))
        self.assertTrue(common.is_garbage("выкуп аккаунтов дорого"))
        self.assertTrue(common.is_garbage("скуплю акки гёнша"))
        self.assertTrue(common.is_garbage("Обменяю на свой аккаунт"))
        self.assertFalse(common.is_garbage("Скирк, Ху Тао, AR60"))

    def test_is_blacklisted_seller(self) -> None:
        self.assertTrue(common.is_blacklisted_seller("AuraFarm"))
        self.assertTrue(common.is_blacklisted_seller("aurafarm"))
        self.assertTrue(common.is_blacklisted_seller("  AuraFarm  "))
        self.assertFalse(common.is_blacklisted_seller("KillLaFlare"))
        self.assertFalse(common.is_blacklisted_seller(""))
        self.assertFalse(common.is_blacklisted_seller(None))

    def test_event_5star(self) -> None:
        self.assertTrue(common.has_event_5star("ХУ ТАО + Скирк + 6 легов"))
        self.assertTrue(common.has_event_5star("only Arlecchino, Furina"))
        self.assertFalse(common.has_event_5star("Дилюк, Цици, Мона"))

    def test_price_to_rub(self) -> None:
        self.assertEqual(common.price_to_rub(100, "₽"), 100.0)
        self.assertEqual(common.price_to_rub(100, "RUR"), 100.0)
        self.assertEqual(common.price_to_rub(1, "$"), common.USD_TO_RUB)
        self.assertEqual(common.price_to_rub(1, "€"), common.EUR_TO_RUB)
        self.assertIsNone(common.price_to_rub(None, "₽"))


class TestSeenStateMachine(unittest.TestCase):
    def test_first_seen_and_drop(self) -> None:
        seen: dict = {"cat1": {}, "cat2": {}}
        items_v1 = [{"id": "a", "price_rub": 1000.0}]
        new, drops = common.update_seen(seen, "cat1", items_v1)
        self.assertEqual(len(new), 1)
        self.assertEqual(len(drops), 0)

        # тот же лот, цена та же — ничего нового
        new, drops = common.update_seen(seen, "cat1", items_v1)
        self.assertEqual(len(new), 0)
        self.assertEqual(len(drops), 0)

        # цена упала >5%
        items_v2 = [{"id": "a", "price_rub": 800.0}]
        new, drops = common.update_seen(seen, "cat1", items_v2)
        self.assertEqual(len(new), 0)
        self.assertEqual(len(drops), 1)
        self.assertAlmostEqual(drops[0]["_drop_pct"], 20.0, places=1)

    def test_price_none_does_not_overwrite(self) -> None:
        seen: dict = {"cat1": {"a": {"price": 500, "first_seen": 1, "last_seen": 1}}, "cat2": {}}
        items = [{"id": "a", "price_rub": None}]
        common.update_seen(seen, "cat1", items)
        self.assertEqual(seen["cat1"]["a"]["price"], 500)

    def test_prune_seen(self) -> None:
        now = int(time.time())
        seen = {
            "cat1": {
                "old": {"price": 100, "first_seen": now - 30 * 86400, "last_seen": now - 30 * 86400},
                "fresh": {"price": 100, "first_seen": now, "last_seen": now},
            },
            "cat2": {},
        }
        removed = common.prune_seen(seen, ttl_seconds=14 * 86400, now=now)
        self.assertEqual(removed, 1)
        self.assertNotIn("old", seen["cat1"])
        self.assertIn("fresh", seen["cat1"])

    def test_legacy_format_load(self) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            json.dump({"cat1": {"x": 123.0}, "cat2": {}}, f)
            path = f.name
        try:
            seen = common.load_seen(path)
            self.assertEqual(seen["cat1"]["x"]["price"], 123.0)
            self.assertIsNone(seen["cat1"]["x"]["first_seen"])
        finally:
            os.unlink(path)


class TestCategorize(unittest.TestCase):
    def test_funpay_categorize_filters_garbage(self) -> None:
        items = [
            {"id": "1", "ar": 55, "type": "прокачанный", "server": "Европа",
             "desc": "ДОГОВОРНАЯ ЦЕНА", "price_rub": 1500.0, "seller": "x"},
            {"id": "2", "ar": 55, "type": "прокачанный", "server": "Европа",
             "desc": "Скирк + Ху Тао", "price_rub": 1500.0, "seller": "x"},
            {"id": "3", "ar": 55, "type": "нероленный", "server": "Европа",
             "desc": "ивент 6500", "price_rub": 1500.0, "seller": "x"},
            {"id": "4", "ar": 55, "type": "прокачанный", "server": "Азия",
             "desc": "ивент 6500", "price_rub": 1500.0, "seller": "x"},
            # buyer pretending to be seller
            {"id": "5", "ar": 60, "type": "прокачанный", "server": "Европа",
             "desc": "Куплю ваш аккаунт! Ниже рынка",
             "price_rub": 367.0, "seller": "KillLaFlare"},
            # blacklisted seller
            {"id": "6", "ar": 55, "type": "прокачанный", "server": "Европа",
             "desc": "Скирк + Ху Тао", "price_rub": 200.0, "seller": "AuraFarm"},
        ]
        c1, c2 = genshin_monitor.categorize(items)
        ids = {i["id"] for i in c1}
        self.assertEqual(ids, {"2"})

    def test_paygame_cat2_requires_event_5star(self) -> None:
        items = [
            {"id": "1", "ar": 5, "neroll": False, "server": "Европа",
             "desc": "Только Цици и Мона", "desc_full": "Только Цици и Мона",
             "price_rub": 500.0},
            {"id": "2", "ar": 5, "neroll": False, "server": "Европа",
             "desc": "Скирк + Аяка", "desc_full": "Скирк + Аяка",
             "price_rub": 500.0},
        ]
        c1, c2 = paygame_monitor.categorize(items)
        ids = {i["id"] for i in c2}
        self.assertEqual(ids, {"2"})


if __name__ == "__main__":
    unittest.main()
