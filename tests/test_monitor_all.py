"""Тесты для scripts/monitor_all.py — hot_pick, build_markdown."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import monitor_all  # noqa: E402


def _lot(sid: str, ar: int, desc: str, price: float, source: str = "FunPay") -> dict:
    return {
        "id": sid, "ar": ar, "desc": desc, "price_rub": price,
        "source": source, "seller": "tester", "server": "Европа",
        "url": f"https://example.com/{sid}",
    }


class TestHotPick(unittest.TestCase):
    def test_sorts_by_discount_desc(self) -> None:
        # Медиана для (55, 2) = 1000.
        medians = {(55, 2): 1000.0}
        fp_data = {
            "new_cat1": [
                _lot("a", 55, "Скирк + Ху Тао", 800),  # -20%
                _lot("b", 55, "Скирк + Ху Тао", 200),  # -80%
                _lot("c", 55, "Скирк + Ху Тао", 500),  # -50%
            ],
            "new_cat2": [],
        }
        pg_data = {"new_cat1": [], "new_cat2": []}
        out = monitor_all.hot_pick(fp_data, pg_data, medians, threshold=20.0, n=10)
        ids = [x["id"] for x in out]
        self.assertEqual(ids, ["b", "c", "a"])

    def test_threshold_drops_low_discount(self) -> None:
        medians = {(55, 2): 1000.0}
        fp_data = {
            "new_cat1": [_lot("a", 55, "Скирк + Ху Тао", 900)],  # -10%
            "new_cat2": [],
        }
        pg_data = {"new_cat1": [], "new_cat2": []}
        out = monitor_all.hot_pick(fp_data, pg_data, medians, threshold=20.0, n=10)
        self.assertEqual(out, [])

    def test_only_new(self) -> None:
        # Лот в cat1 (но не в new_cat1) — не должен попасть в hot-pool.
        medians = {(55, 2): 1000.0}
        fp_data = {
            "new_cat1": [],
            "new_cat2": [],
            "cat1": [_lot("a", 55, "Скирк + Ху Тао", 100)],
        }
        pg_data = {"new_cat1": [], "new_cat2": []}
        out = monitor_all.hot_pick(fp_data, pg_data, medians, threshold=20.0, n=10)
        self.assertEqual(out, [])

    def test_limit_n(self) -> None:
        medians = {(55, 2): 1000.0}
        fp_data = {
            "new_cat1": [_lot(f"a{i}", 55, "Скирк + Ху Тао", 100) for i in range(20)],
            "new_cat2": [],
        }
        pg_data = {"new_cat1": [], "new_cat2": []}
        out = monitor_all.hot_pick(fp_data, pg_data, medians, threshold=20.0, n=5)
        self.assertEqual(len(out), 5)

    def test_combines_both_sources(self) -> None:
        medians = {(55, 2): 1000.0}
        fp_data = {
            "new_cat1": [_lot("fp1", 55, "Скирк + Ху Тао", 700)],  # -30%
            "new_cat2": [],
        }
        pg_data = {
            "new_cat1": [_lot("pg1", 55, "Скирк + Ху Тао", 200, source="PayGame")],  # -80%
            "new_cat2": [],
        }
        out = monitor_all.hot_pick(fp_data, pg_data, medians, threshold=20.0, n=10)
        ids = [x["id"] for x in out]
        # PayGame первым (больший дисконт)
        self.assertEqual(ids, ["pg1", "fp1"])


class TestBuildMarkdown(unittest.TestCase):
    def test_renders_top_block_when_present(self) -> None:
        # Подсунем 5 одинаковых лотов FunPay (медиана 1000) + один хот-лот.
        base = [_lot(f"f{i}", 55, "Скирк + Ху Тао", 1000) for i in range(5)]
        hot = _lot("hot", 55, "Скирк + Ху Тао", 200)  # -80%
        fp_data = {
            "source": "FunPay", "total_raw": 100,
            "cat1": base + [hot], "cat2": [],
            "new_cat1": [hot], "new_cat2": [],
            "drop_cat1": [], "drop_cat2": [],
            "first_run": False, "ok": True,
        }
        pg_data = {
            "source": "PayGame", "total_raw": 0,
            "cat1": [], "cat2": [],
            "new_cat1": [], "new_cat2": [],
            "drop_cat1": [], "drop_cat2": [],
            "first_run": False, "ok": True,
        }
        md = monitor_all.build_markdown(fp_data, pg_data, "2025-01-01 00:00")
        self.assertIn("Top hot lots", md)
        self.assertIn("-80%", md)
        self.assertIn("hot", md)  # url или id попадает в строку

    def test_renders_empty_top_block(self) -> None:
        fp_data = {
            "source": "FunPay", "total_raw": 0,
            "cat1": [], "cat2": [],
            "new_cat1": [], "new_cat2": [],
            "drop_cat1": [], "drop_cat2": [],
            "first_run": False, "ok": True,
        }
        pg_data = dict(fp_data, source="PayGame")
        md = monitor_all.build_markdown(fp_data, pg_data, "2025-01-01 00:00")
        self.assertIn("горячих лотов нет", md)


if __name__ == "__main__":
    unittest.main()
