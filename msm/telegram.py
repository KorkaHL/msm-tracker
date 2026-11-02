"""Тонкий клиент Telegram Bot API (long polling)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

API = "https://api.telegram.org"


class TelegramError(Exception):
    def __init__(self, code: int, desc: str):
        super().__init__(f"{code}: {desc}")
        self.code = code
        self.desc = desc


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, _timeout: int = 40, **params):
        req = urllib.request.Request(
            f"{API}/bot{self.token}/{method}", data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                payload = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
            except Exception:
                payload = {"ok": False, "error_code": e.code, "description": str(e)}
        if not payload.get("ok"):
            raise TelegramError(payload.get("error_code", 0), payload.get("description", ""))
        return payload["result"]

    def get_updates(self, offset: int, poll: int = 25):
        # ключ "timeout" у API — это long-poll, а наш сетевой timeout передаём отдельно
        return self.call("getUpdates", poll + 15, **{"offset": offset, "timeout": poll,
                                                     "allowed_updates": ["message", "callback_query"]})

    def send(self, chat_id: int, text: str, markup: dict | None = None):
        p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            p["reply_markup"] = markup
        return self.call("sendMessage", **p)

    def edit(self, chat_id: int, message_id: int, text: str, markup: dict | None = None):
        p = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
             "disable_web_page_preview": True}
        if markup:
            p["reply_markup"] = markup
        try:
            return self.call("editMessageText", **p)
        except TelegramError as e:
            if "not modified" in e.desc:
                return None
            raise

    def answer(self, callback_id: str, text: str = ""):
        return self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def set_commands(self, commands: list[tuple[str, str]]):
        return self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])

    def send_photo(self, chat_id: int, photo: str, caption: str, markup: dict | None = None):
        p = {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML"}
        if markup:
            p["reply_markup"] = markup
        return self.call("sendPhoto", **p)

    def edit_markup(self, chat_id: int, message_id: int, markup: dict):
        try:
            return self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=markup)
        except TelegramError as e:
            if "not modified" in e.desc:
                return None
            raise
