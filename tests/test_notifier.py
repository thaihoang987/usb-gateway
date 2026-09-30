import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway_manager.config import ConfigError
from gateway_manager.notifier import PortWatch, SettingsStore, TelegramNotifier, public_telegram

TOKEN = "123456789:AAtesttokentesttokentesttoken12"


def gw(status, message=""):
    return dict(id="a", name="Arduino", device="/dev/serial/by-path/x", tcp_port=5020,
                status=status, message=message)


class PortWatchTests(unittest.TestCase):
    def test_debounced_offline_and_reconnect(self):
        watch = PortWatch()
        self.assertEqual(watch.observe([gw("running")], 10, True, now=0), [])
        self.assertEqual(watch.observe([gw("running")], 10, True, now=10), [])  # baseline is silent
        self.assertEqual(watch.observe([gw("waiting", "Waiting for USB")], 10, True, now=20), [])
        offline = watch.observe([gw("waiting", "Waiting for USB")], 10, True, now=30)
        self.assertEqual(len(offline), 1)
        self.assertIn("MẤT KẾT NỐI", offline[0])
        self.assertIn("Waiting for USB", offline[0])
        self.assertEqual(watch.observe([gw("waiting")], 10, True, now=31), [])  # no repeat
        watch.observe([gw("running")], 10, True, now=40)
        back = watch.observe([gw("running")], 10, True, now=50)
        self.assertEqual(len(back), 1)
        self.assertIn("ĐÃ KẾT NỐI LẠI sau 30s", back[0])

    def test_short_blip_and_transitional_status_are_silent(self):
        watch = PortWatch()
        watch.observe([gw("running")], 5, True, now=0)
        watch.observe([gw("running")], 5, True, now=5)
        self.assertEqual(watch.observe([gw("error")], 5, True, now=6), [])
        self.assertEqual(watch.observe([gw("stopped")], 5, True, now=8), [])
        self.assertEqual(watch.observe([gw("running")], 5, True, now=9), [])
        self.assertEqual(watch.observe([gw("running")], 5, True, now=30), [])

    def test_offline_at_start_is_reported_and_recovery_can_be_muted(self):
        watch = PortWatch()
        watch.observe([gw("waiting")], 0, False, now=0)
        self.assertEqual(watch.observe([gw("running")], 0, False, now=1), [])
        watch.observe([], 0, False, now=2)
        self.assertEqual(watch.states, {})


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SettingsStore(Path(self.temp.name) / "settings.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_token_kept_masked_and_cleared(self):
        self.store.update_telegram({"enabled": True, "bot_token": TOKEN, "chat_id": "5388669599"})
        saved = self.store.update_telegram({"enabled": True, "bot_token": "", "chat_id": "-100123"})
        self.assertEqual(saved["bot_token"], TOKEN)
        public = public_telegram(saved)
        self.assertNotIn("bot_token", public)
        self.assertEqual(public["bot_token_hint"], "…en12")
        self.assertNotIn(TOKEN, json.dumps(public))
        cleared = self.store.update_telegram({"enabled": False, "clear_bot_token": True})
        self.assertEqual(cleared["bot_token"], "")

    def test_validation(self):
        with self.assertRaises(ConfigError):
            self.store.update_telegram({"enabled": True, "chat_id": "123"})
        with self.assertRaises(ConfigError):
            self.store.update_telegram({"bot_token": "nope"})
        with self.assertRaises(ConfigError):
            self.store.update_telegram({"chat_id": "abc def"})

    def test_notifier_queues_only_when_enabled(self):
        events = []
        self.store.update_telegram({"offline_delay_s": 0})
        notifier = TelegramNotifier(self.store, lambda *a: events.append(a))
        notifier.watch.observe([gw("running")], 0, True, now=0)
        with patch("gateway_manager.notifier.time.monotonic", return_value=100):
            notifier.observe([gw("error", "boom")], 0, 1)
        self.assertTrue(notifier.queue.empty())
        self.assertIn("MẤT KẾT NỐI", events[0][1])
        self.store.update_telegram({"enabled": True, "bot_token": TOKEN, "chat_id": "1"})
        with patch("gateway_manager.notifier.time.monotonic", return_value=200):
            notifier.observe([gw("running")], 1, 1)
        self.assertIn("Tổng quan: 1/1", notifier.queue.get_nowait())


if __name__ == "__main__":
    unittest.main()
