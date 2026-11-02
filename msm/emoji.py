"""Эмодзи интерфейса. Если в config/emoji.json задан custom_emoji_id — отправляется премиум-эмодзи
(<tg-emoji>) с обычным эмодзи как запасным вариантом, иначе просто обычный эмодзи."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT = {
    "now": "🟢", "soon": "🗓", "shop": "🐾", "pred": "🔮", "bell": "🔔", "globe": "🌍", "info": "ℹ️",
    "menu": "🏠", "prev": "◀️", "next": "▶️", "clock": "⏳", "cal": "📅", "idea": "💡", "link": "🔗",
    "remind": "⏰", "remind_off": "🔕", "star": "✨", "hist": "📜", "parent": "🧩", "ok": "✅", "off": "⬜",
    "kind_promotion": "🏷️", "kind_outofseason": "🔁", "kind_wublin": "🥁", "kind_celestial": "🌌",
    "kind_amber": "🔶", "kind_seasonal": "🎄", "kind_anniversary": "🎂", "kind_update": "🆕",
    "kind_sale": "💸", "kind_collab": "🤝", "kind_intro": "🌟", "kind_other": "📌",
}

try:
    IDS: dict[str, str] = json.loads((Path(__file__).resolve().parent.parent / "config" / "emoji.json").read_text()).get("ids", {})
except (OSError, json.JSONDecodeError):
    IDS = {}


def e(name: str) -> str:
    fb = DEFAULT.get(name, "•")
    cid = IDS.get(name)
    return f'<tg-emoji emoji-id="{cid}">{fb}</tg-emoji>' if cid else fb


def plain(name: str) -> str:
    """Обычный эмодзи (для текста кнопок — там <tg-emoji> не работает)."""
    return DEFAULT.get(name, "•")
