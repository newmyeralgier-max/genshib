"""Юнит-тесты для scripts/match.py (fingerprint, jaccard, similar)."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import match  # noqa: E402


class TestFingerprint(unittest.TestCase):
    def test_ar_bucket_floor_to_5(self) -> None:
        self.assertEqual(match.fingerprint({"ar": 50, "desc": ""})[0], 50)
        self.assertEqual(match.fingerprint({"ar": 51, "desc": ""})[0], 50)
        self.assertEqual(match.fingerprint({"ar": 54, "desc": ""})[0], 50)
        self.assertEqual(match.fingerprint({"ar": 55, "desc": ""})[0], 55)
        self.assertEqual(match.fingerprint({"ar": 59, "desc": ""})[0], 55)
        self.assertEqual(match.fingerprint({"ar": 60, "desc": ""})[0], 60)

    def test_ar_unknown(self) -> None:
        self.assertEqual(match.fingerprint({"desc": "Скирк"})[0], None)
        self.assertEqual(match.fingerprint({"ar": None, "desc": "Скирк"})[0], None)

    def test_chars_extracted(self) -> None:
        b, chars, n = match.fingerprint({"ar": 55, "desc": "ХУ ТАО + Скирк + Ке Цин"})
        # ке цин — стандарт, в EVENT_5STAR его нет. ху тао и скирк — есть.
        self.assertIn("ху тао", chars)
        self.assertIn("скирк", chars)
        self.assertEqual(n, len(chars))
        self.assertGreaterEqual(n, 2)

    def test_uses_desc_full_first(self) -> None:
        b, chars, _ = match.fingerprint({
            "ar": 55,
            "desc": "пусто",
            "desc_full": "Фурина и Ху Тао",
        })
        self.assertIn("фурина", chars)
        self.assertIn("ху тао", chars)


class TestJaccard(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertAlmostEqual(
            match.jaccard(frozenset({"a", "b"}), frozenset({"a", "c"})),
            1 / 3,
        )

    def test_full_overlap(self) -> None:
        self.assertEqual(
            match.jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})),
            1.0,
        )

    def test_no_overlap(self) -> None:
        self.assertEqual(
            match.jaccard(frozenset({"a"}), frozenset({"b"})),
            0.0,
        )

    def test_empty_inputs_give_zero(self) -> None:
        self.assertEqual(match.jaccard(frozenset(), frozenset()), 0.0)
        self.assertEqual(match.jaccard(frozenset({"a"}), frozenset()), 0.0)


class TestSimilar(unittest.TestCase):
    def _lot(self, ar: int, desc: str) -> dict:
        return {"ar": ar, "desc": desc}

    def test_same_bucket_strong_overlap(self) -> None:
        a = self._lot(55, "Скирк + Ху Тао + Фурина")
        b = self._lot(57, "Скирк + Ху Тао + Аяка")
        # бакет 55, jaccard 2/4 = 0.5 → >= 0.5 порог
        self.assertTrue(match.similar(a, b))

    def test_diff_bucket(self) -> None:
        a = self._lot(50, "Скирк + Ху Тао")
        b = self._lot(55, "Скирк + Ху Тао")
        self.assertFalse(match.similar(a, b))

    def test_one_without_event_5(self) -> None:
        a = self._lot(55, "Дилюк + Цици")  # стандарт-only
        b = self._lot(55, "Скирк + Ху Тао")
        self.assertFalse(match.similar(a, b))

    def test_threshold_override(self) -> None:
        a = self._lot(55, "Скирк + Ху Тао + Фурина + Аяка")
        b = self._lot(55, "Скирк + Линнея")  # jaccard 1/5 = 0.2
        self.assertFalse(match.similar(a, b))                       # default 0.5
        self.assertTrue(match.similar(a, b, min_jaccard=0.15))      # ослабленный


class TestMarketMedians(unittest.TestCase):
    def _lot(self, ar: int, desc: str, price: float) -> dict:
        return {"ar": ar, "desc": desc, "price_rub": price}

    def test_market_class(self) -> None:
        # AR54 + 2 ивентовых → (50, 2)
        self.assertEqual(
            match.market_class({"ar": 54, "desc": "Скирк + Ху Тао"}),
            (50, 2),
        )
        # AR60 + 0 ивентовых → (60, 0)
        self.assertEqual(
            match.market_class({"ar": 60, "desc": "Дилюк + Цици"}),
            (60, 0),
        )

    def test_market_class_caps_at_4(self) -> None:
        cls = match.market_class({
            "ar": 55,
            "desc": "Скирк + Ху Тао + Фурина + Аяка + Линнея + Йоимия",
        })
        self.assertEqual(cls[0], 55)
        self.assertEqual(cls[1], 4)  # capped

    def test_median_skips_small_sample(self) -> None:
        items = [
            self._lot(55, "Скирк + Ху Тао", 1000),
            self._lot(55, "Скирк + Ху Тао", 1100),
            self._lot(55, "Скирк + Ху Тао", 900),
            self._lot(55, "Скирк + Ху Тао", 1050),
        ]  # 4 lots — under default min_sample=5
        medians = match.fp_median_by_class(items)
        self.assertEqual(medians, {})

    def test_median_simple(self) -> None:
        # 5 лотов одного класса → есть медиана
        items = [
            self._lot(55, "Скирк + Ху Тао", p)
            for p in (900, 1000, 1100, 1200, 1300)
        ]
        medians = match.fp_median_by_class(items)
        self.assertIn((55, 2), medians)
        self.assertEqual(medians[(55, 2)], 1100.0)

    def test_median_drops_none_prices(self) -> None:
        items = [
            self._lot(55, "Скирк + Ху Тао", p)
            for p in (900, 1000, 1100, 1200, 1300)
        ]
        items.append({"ar": 55, "desc": "Скирк + Ху Тао", "price_rub": None})
        medians = match.fp_median_by_class(items)
        self.assertEqual(medians[(55, 2)], 1100.0)

    def test_discount_pct_below_market(self) -> None:
        medians = {(55, 2): 1000.0}
        item = {"ar": 55, "desc": "Скирк + Ху Тао", "price_rub": 200.0}
        self.assertEqual(match.discount_pct(item, medians), 80.0)

    def test_discount_pct_above_market(self) -> None:
        medians = {(55, 2): 1000.0}
        item = {"ar": 55, "desc": "Скирк + Ху Тао", "price_rub": 1500.0}
        # -50% «от рынка» = на 50% ДОРОЖЕ медианы → discount = -50.0
        self.assertEqual(match.discount_pct(item, medians), -50.0)

    def test_discount_pct_no_class(self) -> None:
        medians: dict = {}
        item = {"ar": 55, "desc": "Скирк + Ху Тао", "price_rub": 200.0}
        self.assertIsNone(match.discount_pct(item, medians))

    def test_discount_pct_no_price(self) -> None:
        medians = {(55, 2): 1000.0}
        item = {"ar": 55, "desc": "Скирк + Ху Тао", "price_rub": None}
        self.assertIsNone(match.discount_pct(item, medians))


class TestBuildMatches(unittest.TestCase):
    def _lot(self, sid: str, ar: int, desc: str, price: float = 500) -> dict:
        return {"id": sid, "ar": ar, "desc": desc, "price_rub": price}

    def test_finds_top_k(self) -> None:
        pg = [self._lot("p1", 55, "Скирк + Ху Тао", 200)]
        fp = [
            self._lot("f1", 55, "Скирк + Ху Тао", 700),     # j=1.0
            self._lot("f2", 55, "Скирк + Фурина", 600),     # j=1/3
            self._lot("f3", 55, "Скирк + Ху Тао + Аяка", 800),  # j=2/3
            self._lot("f4", 50, "Скирк + Ху Тао", 500),     # diff bucket
        ]
        m = match.build_matches(pg, fp, top_k=3)
        self.assertIn("p1", m)
        ids = [x["id"] for x in m["p1"]]
        # f1 (1.0), f3 (0.66) — f2 не пройдёт min_jaccard=0.5
        self.assertEqual(ids[:2], ["f1", "f3"])

    def test_sorts_by_jaccard_then_price(self) -> None:
        pg = [self._lot("p1", 55, "Скирк + Ху Тао")]
        fp = [
            self._lot("f1", 55, "Скирк + Ху Тао", 1500),    # j=1.0
            self._lot("f2", 55, "Скирк + Ху Тао", 700),     # j=1.0, дешевле
        ]
        m = match.build_matches(pg, fp, top_k=2)
        # одинаковый jaccard → сначала дешёвый
        self.assertEqual([x["id"] for x in m["p1"]], ["f2", "f1"])

    def test_empty_inputs(self) -> None:
        self.assertEqual(match.build_matches([], []), {})
        self.assertEqual(
            match.build_matches([self._lot("p1", 55, "Скирк")], []),
            {},
        )

    def test_skips_lots_without_event_5(self) -> None:
        pg = [self._lot("p1", 55, "Дилюк + Цици")]   # стандарт-only
        fp = [self._lot("f1", 55, "Дилюк + Цици")]
        m = match.build_matches(pg, fp)
        self.assertEqual(m, {})

    def test_records_jaccard_score(self) -> None:
        pg = [self._lot("p1", 55, "Скирк + Ху Тао")]
        fp = [self._lot("f1", 55, "Скирк + Ху Тао")]
        m = match.build_matches(pg, fp)
        self.assertEqual(m["p1"][0]["_jaccard"], 1.0)


class TestDedup(unittest.TestCase):
    def _lot(
        self, sid: str, ar: int, desc: str, price: float, source: str, seller: str = "x"
    ) -> dict:
        return {
            "id": sid, "ar": ar, "desc": desc, "price_rub": price,
            "source": source, "seller": seller,
        }

    def test_same_seller_partial_overlap(self) -> None:
        a = self._lot("a", 55, "Скирк + Ху Тао + Фурина", 700, "FunPay", "petya")
        b = self._lot("b", 55, "Скирк + Ху Тао", 720, "PayGame", "petya")
        # jaccard 2/3 ≈ 0.667 → дубль через селлера (порог 0.7? нет)
        # 0.667 < 0.7 → НЕ дубль. Проверим обратное: добавим Фурину обоим.
        c = self._lot("c", 55, "Скирк + Ху Тао + Фурина", 720, "PayGame", "petya")
        self.assertTrue(match.is_dup(a, c))           # j=1.0
        self.assertFalse(match.is_dup(a, b))          # j=0.667 < 0.7

    def test_diff_seller_full_overlap(self) -> None:
        a = self._lot("a", 55, "Скирк + Ху Тао + Фурина", 700, "FunPay", "alpha")
        b = self._lot("b", 55, "Скирк + Ху Тао + Фурина", 720, "PayGame", "beta")
        self.assertTrue(match.is_dup(a, b))           # j=1.0 ≥ 0.85

    def test_diff_seller_partial_overlap_not_dup(self) -> None:
        a = self._lot("a", 55, "Скирк + Ху Тао + Фурина", 700, "FunPay", "alpha")
        b = self._lot("b", 55, "Скирк + Ху Тао", 720, "PayGame", "beta")
        # j=0.667 < 0.85 → не дубль (без совпадения селлера порог жёстче)
        self.assertFalse(match.is_dup(a, b))

    def test_diff_price_too_far(self) -> None:
        a = self._lot("a", 55, "Скирк + Ху Тао", 200, "FunPay", "alpha")
        b = self._lot("b", 55, "Скирк + Ху Тао", 800, "PayGame", "alpha")
        self.assertFalse(match.is_dup(a, b))

    def test_same_source_never_dup(self) -> None:
        a = self._lot("a", 55, "Скирк + Ху Тао", 700, "FunPay", "alpha")
        b = self._lot("b", 55, "Скирк + Ху Тао", 720, "FunPay", "alpha")
        self.assertFalse(match.is_dup(a, b))

    def test_diff_ar_bucket_not_dup(self) -> None:
        a = self._lot("a", 50, "Скирк + Ху Тао + Фурина", 700, "FunPay", "alpha")
        b = self._lot("b", 55, "Скирк + Ху Тао + Фурина", 720, "PayGame", "alpha")
        self.assertFalse(match.is_dup(a, b))

    def test_find_cross_source_dups(self) -> None:
        fp = [
            self._lot("f1", 55, "Скирк + Ху Тао + Фурина", 700, "FunPay", "alpha"),
            self._lot("f2", 50, "Скирк + Линнея + Аяка", 500, "FunPay", "x"),
        ]
        pg = [
            self._lot("p1", 55, "Скирк + Ху Тао + Фурина", 720, "PayGame", "beta"),
            self._lot("p2", 60, "Скирк + Ху Тао", 200, "PayGame", "x"),
        ]
        pairs = match.find_cross_source_dups(fp, pg)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0]["id"], "f1")
        self.assertEqual(pairs[0][1]["id"], "p1")


if __name__ == "__main__":
    unittest.main()
