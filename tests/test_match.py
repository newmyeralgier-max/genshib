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


if __name__ == "__main__":
    unittest.main()
