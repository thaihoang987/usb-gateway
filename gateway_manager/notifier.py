"""Telegram alerts when an enabled gateway loses or regains its USB port."""
from __future__ import annotations

import json
import os
import queue
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import ConfigError, _as_bool, _as_int


TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
CHAT_ID_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{5,64})$")
TELEGRAM_DEFAULTS = {
    "enabled": False,
    "bot_token": "",
    "chat_id": "",
    "offline_delay_s": 10,
    "notify_recovery": True,
    "title": "USB Gateway",
}


def normalize_telegram(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Merge a UI payload into the saved settings.

    An empty bot_token keeps the saved one so the UI never has to receive the
    secret back; clear_bot_token removes it explicitly.
    """
    token = str(payload.get("bot_token") or "").strip()
    if _as_bool(payload.get("clear_bot_token")):
        token = ""
    elif not token:
        token = current.get("bot_token", "")
    if token and not TOKEN_RE.fullmatch(token):
        raise ConfigError("Bot token must look like 123456789:AA... (from @BotFather)")

    chat_id = str(payload.get("chat_id", current.get("chat_id", ""))).strip()
    if chat_id and not CHAT_ID_RE.fullmatch(chat_id):
        raise ConfigError("Chat ID must be a number (e.g. 5388669599 or -100...) or @channelname")

    title = str(payload.get("title", current.get("title", "USB Gateway"))).strip() or "USB Gateway"
    if len(title) > 64:
        raise ConfigError("Title must be up to 64 characters")

    enabled = _as_bool(payload.get("enabled", current.get("enabled")), False)
    if enabled and (not token or not chat_id):
        raise ConfigError("Enter both bot token and chat ID before enabling Telegram alerts")

    return {
        "enabled": enabled,
        "bot_token": token,
        "chat_id": chat_id,
        "offline_delay_s": _as_int(payload.get("offline_delay_s", current.get("offline_delay_s", 10)),
                                   "offline_delay_s", 0, 3600),
        "notify_recovery": _as_bool(payload.get("notify_recovery", current.get("notify_recovery")), True),
        "title": title,
    }


def public_telegram(settings: dict[str, Any]) -> dict[str, Any]:
    token = settings.get("bot_token", "")
    result = {k: v for k, v in settings.items() if k != "bot_token"}
    result["bot_token_set"] = bool(token)
    result["bot_token_hint"] = f"…{token[-4:]}" if token else ""
    return result


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read {self.path}: {exc}") from exc
        telegram = raw.get("telegram") if isinstance(raw, dict) else None
        return {"telegram": {**TELEGRAM_DEFAULTS, **(telegram if isinstance(telegram, dict) else {})}}

    def save(self, settings: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, **settings}, indent=2, ensure_ascii=False)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)  # contains the bot token
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def update_telegram(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            settings = self.load()
            settings["telegram"] = normalize_telegram(payload, settings["telegram"])
            self.save(settings)
            return settings["telegram"]


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 10) -> str | None:
    """Send one message; returns None on success or a token-free error."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000]}).encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read() or b"{}")
        error = None if body.get("ok") else body.get("description", "Telegram rejected the message")
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get("description") or str(exc)
        except (ValueError, OSError):
            error = str(exc)
    except (OSError, ValueError) as exc:
        error = str(getattr(exc, "reason", exc))
    return str(error).replace(token, "<token>") if error else None


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class PortWatch:
    """Debounced online/offline tracking per gateway.

    A change must persist for offline_delay_s before it is reported, so a
    worker restarting through waiting/error for a second does not alert.
    Transitional statuses (stopped/disabled) keep the previous state.
    """

    def __init__(self):
        self.states: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _classify(status: str) -> str | None:
        if status == "running":
            return "online"
        if status in {"waiting", "error"}:
            return "offline"
        return None

    def observe(self, gateways: list[dict[str, Any]], delay: float, notify_recovery: bool,
                now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        messages: list[str] = []
        seen = set()
        for gw in gateways:
            seen.add(gw["id"])
            state = self.states.setdefault(gw["id"], {"confirmed": None, "pending": None, "since": now,
                                                      "offline_since": None})
            current = self._classify(gw["status"])
            if current is None or current == state["confirmed"]:
                state["pending"] = None
                continue
            if state["pending"] != current:
                state["pending"], state["since"] = current, now
            if now - state["since"] < delay:
                continue
            previous = state["confirmed"]
            state["confirmed"], state["pending"] = current, None
            where = f"Thiết bị: {gw['device']}\nTCP: {gw['tcp_port']}"
            if current == "offline":
                state["offline_since"] = state["since"]
                messages.append(f"🔴 {gw['name']}: MẤT KẾT NỐI"
                                f"{' (lúc bắt đầu theo dõi)' if previous is None else ''}\n{where}\n"
                                f"Lý do: {gw.get('message') or gw['status']}")
            elif previous == "offline" and notify_recovery:
                down = now - (state["offline_since"] or state["since"])
                messages.append(f"✅ {gw['name']}: ĐÃ KẾT NỐI LẠI sau {_duration(down)}\n{where}")
        for gone in set(self.states) - seen:
            del self.states[gone]
        return messages


class TelegramNotifier:
    def __init__(self, store: SettingsStore, event_sink: Callable[..., None]):
        self.store = store
        self.event_sink = event_sink
        self.watch = PortWatch()
        self.queue: queue.Queue[str | None] = queue.Queue(maxsize=100)
        self.thread: threading.Thread | None = None

    def settings(self) -> dict[str, Any]:
        try:
            return self.store.load()["telegram"]
        except ConfigError as exc:
            self.event_sink("error", f"Telegram settings unreadable: {exc}")
            return dict(TELEGRAM_DEFAULTS)

    def start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._sender, name="telegram", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        if self.thread and self.thread.is_alive():
            self.queue.put(None)
            self.thread.join(timeout=12)

    def observe(self, gateways: list[dict[str, Any]], online: int, total: int) -> None:
        settings = self.settings()
        messages = self.watch.observe(gateways, settings["offline_delay_s"], settings["notify_recovery"])
        if not messages:
            return
        for line in messages:
            self.event_sink("warning" if line.startswith("🔴") else "info", "Port alert: " + line.replace("\n", " | "))
        if settings["enabled"] and settings["bot_token"] and settings["chat_id"]:
            text = f"{settings['title']}\nTổng quan: {online}/{total} gateway online.\n\n" + "\n\n".join(messages)
            try:
                self.queue.put_nowait(text)
            except queue.Full:
                self.event_sink("warning", "Telegram queue full; alert dropped")

    def test(self, payload: dict[str, Any]) -> str | None:
        """Send a test with the saved settings overlaid by unsaved UI values."""
        current = self.settings()
        candidate = normalize_telegram({**payload, "enabled": False}, current)
        if not candidate["bot_token"] or not candidate["chat_id"]:
            raise ConfigError("Enter bot token and chat ID first")
        return send_telegram(candidate["bot_token"], candidate["chat_id"],
                             f"{candidate['title']}\n🔔 Tin nhắn thử - cấu hình Telegram hoạt động.")

    def _sender(self) -> None:
        while True:
            text = self.queue.get()
            if text is None:
                return
            error = None
            for attempt in range(3):
                settings = self.settings()
                if not settings["enabled"]:
                    break
                error = send_telegram(settings["bot_token"], settings["chat_id"], text)
                if error is None:
                    break
                time.sleep(5 * (attempt + 1))
            if error:
                self.event_sink("warning", f"Telegram send failed: {error}")
