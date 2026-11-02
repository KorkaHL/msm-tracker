"""Форматирование сообщений (Telegram HTML, русский)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from . import significance
from .dates import parse_iso

MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
             "сентября", "октября", "ноября", "декабря"]
MAX_MSG = 3800


def tzinfo(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def fmt_day(dt: datetime) -> str:
    return f"{dt.day} {MONTHS_RU[dt.month - 1]}"


def fmt_range(e: dict, tz: str = "UTC") -> str:
    s = parse_iso(e["start"])
    last = parse_iso(e["end"]) - timedelta(seconds=1)  # день-точность: конец включительно
    if s.date() == last.date():
        return fmt_day(s)
    if s.year != last.year:
        return f"{fmt_day(s)} {s.year} – {fmt_day(last)} {last.year}"
    return f"{fmt_day(s)} – {fmt_day(last)}"


def fmt_delta(td: timedelta) -> str:
    secs = max(0, int(td.total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} д {h} ч" if h else f"{d} д"
    if h:
        return f"{h} ч {m} мин" if m else f"{h} ч"
    return f"{m} мин"


def status_of(e: dict, now: datetime) -> str:
    s, en = parse_iso(e["start"]), parse_iso(e["end"])
    if now >= en:
        return "past"
    return "active" if now >= s else "upcoming"


def timing(e: dict, now: datetime) -> str:
    s, en = parse_iso(e["start"]), parse_iso(e["end"])
    st = status_of(e, now)
    if st == "active":
        return f"идёт, осталось {fmt_delta(en - now)}"
    if st == "upcoming":
        return f"начнётся через {fmt_delta(s - now)}"
    return "завершено"


def confidence_mark(e: dict) -> str:
    if e.get("status") != "predicted":
        return ""
    c = e.get("confidence", 0)
    level = "высокая" if c >= 0.7 else "средняя" if c >= 0.5 else "низкая"
    return f" 🔮 <i>прогноз по прошлым годам, уверенность {level}</i>"


def event_line(e: dict, now: datetime, tz: str = "UTC") -> str:
    label = significance.kind_label(e.get("kind", "other")).split(" ", 1)[0]
    return (f"{label} <b>{escape(e['title'])}</b> — {fmt_range(e, tz)}\n"
            f"   ⏳ {timing(e, now)}{confidence_mark(e)}")


def event_card(e: dict, now: datetime, tz: str = "UTC") -> str:
    out = [f"{significance.kind_label(e.get('kind', 'other'))}  <b>{escape(e['title'])}</b>",
           f"📅 {fmt_range(e, tz)}  (даты по UTC)", f"⏳ {timing(e, now)}{confidence_mark(e)}"]
    if e.get("description"):
        out += ["", escape(e["description"])]
    mean = significance.meaning(e.get("kind", "other"), e.get("tags", []))
    if mean:
        out += ["", "<b>Что это значит для тебя:</b>"] + [f"• {escape(m)}" for m in mean]
    labels = significance.tag_labels(e.get("tags", []))
    if labels:
        out += ["", " · ".join(labels)]
    if e.get("monsters"):
        out += ["", "🐾 Монстры: " + ", ".join(escape(m) for m in e["monsters"][:15])]
    if e.get("url"):
        out += ["", f'<a href="{escape(e["url"], quote=True)}">Страница в вики</a>']
    return "\n".join(out)


def paginate(lines: list[str], header: str = "") -> str:
    out, size = [header] if header else [], len(header)
    for ln in lines:
        if size + len(ln) > MAX_MSG:
            out.append("…")
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out)


def shop_line(r: dict, now: datetime) -> str:
    st = status_of(r, now)
    icon = {"active": "🟢", "upcoming": "🟡", "past": "⚪"}[st]
    pred = " 🔮" if r.get("status") == "predicted" else ""
    ev = f" · {escape(r['event'])}" if r.get("event") else ""
    return f"{icon} <b>{escape(r['monster'])}</b> — {fmt_range(r)}{pred}{ev}\n   ⏳ {timing(r, now)}"
