"""Состояние бота (подписчики, offset, журнал отправленных уведомлений).

Репозиторий публичный, поэтому файл состояния шифруется (Fernet, ключ в секрете STATE_KEY),
иначе chat_id подписчиков лежали бы в открытом виде. Без ключа — обычный JSON (для разработки).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_USER = {"tz": "Europe/Moscow", "subs": {"new": True, "start": True, "ending": True, "shop": True}}
SENT_TTL = 45 * 86400


def _fernet(key: str | None):
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


class State:
    def __init__(self, path: Path, key: str | None = None):
        self.path = Path(path)
        self.f = _fernet(key if key is not None else os.environ.get("STATE_KEY"))
        self.d: dict = {"offset": 0, "users": {}, "sent": {}}
        self.load()

    def load(self) -> None:
        try:
            raw = self.path.read_bytes()
            txt = self.f.decrypt(raw).decode() if self.f else raw.decode()
            self.d = {**self.d, **json.loads(txt)}
        except FileNotFoundError:
            pass

    def save(self) -> None:
        cutoff = time.time() - SENT_TTL
        self.d["sent"] = {k: v for k, v in self.d["sent"].items() if v >= cutoff}
        txt = json.dumps(self.d, ensure_ascii=False, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self.f.encrypt(txt.encode()) if self.f else txt.encode())
        tmp.replace(self.path)

    # --- пользователи ---
    def user(self, chat_id: int, create: bool = True) -> dict | None:
        k = str(chat_id)
        if k not in self.d["users"] and create:
            self.d["users"][k] = {**json.loads(json.dumps(DEFAULT_USER)), "since": time.time()}
        return self.d["users"].get(k)

    def remove_user(self, chat_id: int) -> None:
        self.d["users"].pop(str(chat_id), None)

    # --- журнал уведомлений ---
    def was_sent(self, key: str) -> bool:
        return key in self.d["sent"]

    def mark_sent(self, key: str) -> None:
        self.d["sent"][key] = time.time()
