"""Тесты для scripts/notifier.py — Telegram-нотификатор (гл. 5)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import notifier  # noqa: E402


class TestEscape(unittest.TestCase):
    def test_escapes_specials(self) -> None:
        self.assertEqual(notifier.md_escape("a.b"), "a\\.b")
        self.assertEqual(notifier.md_escape("(x)"), "\\(x\\)")
        self.assertEqual(notifier.md_escape("Скирк + Ху-Тао!"), "Скирк \\+ Ху\\-Тао\\!")

    def test_passthrough_clean(self) -> None:
        self.assertEqual(notifier.md_escape("AR60 hot"), "AR60 hot")


class TestFormat(unittest.TestCase):
    def test_renders_one_lot(self) -> None:
        hot = [{
            "ar": 60, "price_rub": 480, "_disc": 45, "_src": "FunPay",
            "_chars_pretty": "Скирк, Ху Тао", "url": "https://x.test/1",
        }]
        msg = notifier.format_message(hot, limit=10)
        self.assertIn("Genshib hot lots", msg)
        self.assertIn("\\-45%", msg)
        self.assertIn("AR60", msg)
        self.assertIn("Скирк, Ху Тао", msg)
        # url экранирован
        self.assertIn("https://x\\.test/1", msg)

    def test_limit_caps(self) -> None:
        hot = [
            {"ar": i, "price_rub": 100, "_disc": 50, "_src": "FunPay", "url": ""}
            for i in range(20)
        ]
        msg = notifier.format_message(hot, limit=3)
        # Заголовок + 3 строки лотов = 4 строки (без url, т.к. он пустой)
        self.assertEqual(len(msg.splitlines()), 4)


class TestSend(unittest.TestCase):
    def setUp(self) -> None:
        # Снимаем потенциальные env, чтоб тесты не были зависимы от системы.
        for k in (
            notifier.ENV_TOKEN, notifier.ENV_CHAT,
            notifier.ENV_LIMIT, notifier.ENV_MIN, notifier.ENV_DRY,
        ):
            os.environ.pop(k, None)

    def test_no_token_skips(self) -> None:
        ok, msg = notifier.send([{"ar": 55, "_disc": 50}])
        self.assertFalse(ok)
        self.assertIn("no token", msg)

    def test_below_threshold_skips(self) -> None:
        os.environ[notifier.ENV_TOKEN] = "x"
        os.environ[notifier.ENV_CHAT] = "y"
        os.environ[notifier.ENV_DRY] = "1"
        ok, msg = notifier.send([{"ar": 55, "_disc": 10}], min_discount=30)
        self.assertFalse(ok)
        self.assertIn("no items", msg)

    def test_dry_run_returns_text(self) -> None:
        os.environ[notifier.ENV_TOKEN] = "x"
        os.environ[notifier.ENV_CHAT] = "y"
        os.environ[notifier.ENV_DRY] = "1"
        hot = [{
            "ar": 60, "price_rub": 480, "_disc": 45, "_src": "FunPay",
            "url": "https://x/1",
        }]
        ok, msg = notifier.send(hot, min_discount=30)
        self.assertTrue(ok)
        self.assertIn("[dry]", msg)
        self.assertIn("AR60", msg)

    def test_real_send_uses_post(self) -> None:
        os.environ[notifier.ENV_TOKEN] = "X"
        os.environ[notifier.ENV_CHAT] = "Y"
        # Не задаём DRY → пойдёт реальный POST. Мокаем urlopen.
        with mock.patch("urllib.request.urlopen") as m:
            fake = mock.MagicMock()
            fake.status = 200
            fake.read.return_value = b'{"ok":true,"result":{}}'
            fake.__enter__ = lambda s: s
            fake.__exit__ = lambda *a: None
            m.return_value = fake
            ok, _ = notifier.send([{"ar": 60, "_disc": 50, "_src": "FunPay", "url": "u"}])
            self.assertTrue(ok)
            req = m.call_args.args[0]
            self.assertEqual(req.method, "POST")
            self.assertIn("api.telegram.org/botX/sendMessage", req.full_url)

    def test_429_retries_with_retry_after(self) -> None:
        """Если Telegram вернул 429, нужно подождать retry_after и ретраить."""
        os.environ[notifier.ENV_TOKEN] = "X"
        os.environ[notifier.ENV_CHAT] = "Y"

        from urllib.error import HTTPError
        from io import BytesIO

        # Первый раз 429, второй — 200.
        ok_resp = mock.MagicMock()
        ok_resp.status = 200
        ok_resp.read.return_value = b'{"ok":true}'
        ok_resp.__enter__ = lambda s: s
        ok_resp.__exit__ = lambda *a: None

        err = HTTPError(
            "http://x", 429,
            "Too Many Requests", {},
            BytesIO(b'{"ok":false,"parameters":{"retry_after":0}}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=[err, ok_resp]) as m, \
             mock.patch("time.sleep") as sleep_mock:
            ok, _ = notifier.send(
                [{"ar": 60, "_disc": 50, "_src": "FunPay", "url": "u"}],
            )
            self.assertTrue(ok)
            self.assertEqual(m.call_count, 2)
            sleep_mock.assert_called()  # хотя бы раз спали по retry_after


if __name__ == "__main__":
    unittest.main()
