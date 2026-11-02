"""Оформление сообщений (Telegram HTML, русский).

Важно: вики даёт только ДАТЫ без времени старта, поэтому везде считаем и показываем дни,
а не часы/минуты (иначе получалась бы ложная точность).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

from . import significance
from .dates import parse_iso
from .emoji import DEFAULT, e

MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
             "сентября", "октября", "ноября", "декабря"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
LINE = "━━━━━━━━━━━━━━"


def tzinfo(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def today_in(now: datetime, tz: str) -> date:
    return now.astimezone(tzinfo(tz)).date()


def span_dates(ev: dict) -> tuple[date, date]:
    """Календарные даты события [start, end] включительно (хранятся как полночь UTC)."""
    return parse_iso(ev["start"]).date(), (parse_iso(ev["end"]) - timedelta(seconds=1)).date()


def status_of(ev: dict, now: datetime, tz: str = "UTC") -> str:
    td = today_in(now, tz)
    s, en = span_dates(ev)
    if td > en:
        return "past"
    return "active" if td >= s else "upcoming"


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def days_phrase(n: int) -> str:
    return f"{n} {plural(n, ('день', 'дня', 'дней'))}"


def fmt_day(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month - 1]}"


def fmt_range(ev: dict, tz: str = "UTC") -> str:
    s, en = span_dates(ev)
    if ev.get("open_ended"):  # известна только дата начала
        return f"с {fmt_day(s)}"
    if s == en:
        return fmt_day(s)
    if s.year == en.year and s.month == en.month:
        return f"{s.day}–{en.day} {MONTHS_RU[s.month - 1]}"
    if s.year != en.year:
        return f"{fmt_day(s)} {s.year} – {fmt_day(en)} {en.year}"
    return f"{fmt_day(s)} – {fmt_day(en)}"


def short_range(ev: dict) -> str:
    s, en = span_dates(ev)
    if s == en:
        return f"{s.day} {MONTHS_SHORT[s.month - 1]}"
    if s.month == en.month:
        return f"{s.day}–{en.day} {MONTHS_SHORT[s.month - 1]}"
    return f"{s.day} {MONTHS_SHORT[s.month - 1]}–{en.day} {MONTHS_SHORT[en.month - 1]}"


def fmt_delta(td: timedelta) -> str:
    """Для «свежести данных» (/about): здесь точное время уместно."""
    secs = max(0, int(td.total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} д {h} ч" if h else f"{d} д"
    if h:
        return f"{h} ч {m} мин" if m else f"{h} ч"
    return f"{m} мин"


def when(ev: dict, now: datetime, tz: str = "UTC") -> str:
    td = today_in(now, tz)
    s, en = span_dates(ev)
    st = status_of(ev, now, tz)
    if st == "upcoming":
        n = (s - td).days
        return "стартует сегодня" if n == 0 else "стартует завтра" if n == 1 else f"через {days_phrase(n)}"
    if st == "active":
        left = (en - td).days
        if left == 0:
            return "последний день — сегодня"
        return f"идёт · ещё {days_phrase(left)}"
    return "завершено"


timing = when  # обратная совместимость


def bar(frac: float, width: int = 10) -> str:
    n = max(0, min(width, round(frac * width)))
    return "▰" * n + "▱" * (width - n)


def progress(ev: dict, now: datetime, tz: str = "UTC") -> str:
    s, en = span_dates(ev)
    td = today_in(now, tz)
    total = (en - s).days + 1
    done = max(0, min(total, (td - s).days + 1))
    return f"{bar(done / total)} {round(100 * done / total)}%"


def kind_name(kind: str) -> str:
    lab = significance.kind_label(kind)
    return lab.split(" ", 1)[1] if " " in lab else lab


def kind_icon(kind: str) -> str:
    return e(f"kind_{kind}") if f"kind_{kind}" in DEFAULT else e("kind_other")


SOURCE_LABELS = {"bluesky": "Bluesky BBB", "steam": "Steam", "bbb": "Big Blue Bubble", "history": "вики (редакторы)"}
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
              "ноябрь", "декабрь"]


def source_label(ev: dict) -> str:
    return SOURCE_LABELS.get(ev.get("source", "").split(":")[0], "")


def plan_when(key: str) -> str:
    """'2026-12' → 'декабрь 2026'; '' → 'без даты'."""
    try:
        y, m = key.split("-")
        return f"{MONTHS_NOM[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return "без даты"


def pred_mark(ev: dict) -> str:
    if ev.get("status") == "announced":
        src = source_label(ev)
        return f"📣 официальный анонс" + (f" · {src}" if src else "")
    if ev.get("status") != "predicted":
        return ""
    c = ev.get("confidence", 0)
    level = "высокая" if c >= 0.7 else "средняя" if c >= 0.5 else "низкая"
    return f"{e('pred')} прогноз · уверенность {level}"


def visible_len(html: str) -> int:
    return len(re.sub(r"<[^>]+>", "", html))


def event_list_item(ev: dict, now: datetime, tz: str, num: int | None = None) -> str:
    n = f"<b>{num}.</b> " if num else ""
    extra = f"\n      <i>{pred_mark(ev)}</i>" if ev.get("status") in ("predicted", "announced") else ""
    return (f"{n}{kind_icon(ev.get('kind', 'other'))} <b>{escape(ev['title'])}</b>\n"
            f"      {e('cal')} {fmt_range(ev, tz)} · {when(ev, now, tz)}{extra}")


def event_line(ev: dict, now: datetime, tz: str = "UTC") -> str:
    return event_list_item(ev, now, tz)


def event_card(ev: dict, now: datetime, tz: str = "UTC", *, limit: int = 3900, history: list[str] | None = None) -> str:
    """Карточка события. Если не влезает в limit (подпись к фото ≤ 1024) — урезаем по приоритету."""
    kind = ev.get("kind", "other")
    head = f"{kind_icon(kind)} <b>{escape(ev['title'])}</b>\n<i>{escape(kind_name(kind))}</i>"
    if ev.get("status") in ("predicted", "announced"):
        head += f"\n{pred_mark(ev)}"
    st = status_of(ev, now, tz)
    when_block = [f"{e('cal')} <b>{fmt_range(ev, tz)}</b>", f"{e('clock')} {when(ev, now, tz)}"]
    if st == "active":
        when_block.append(progress(ev, now, tz))
    parent = f"{e('parent')} Часть события: <b>{escape(ev['parent'])}</b>" if ev.get("parent") else ""
    desc = ev.get("description", "").strip()
    mean = significance.meaning(kind, ev.get("tags", []))
    monsters = ev.get("monsters", [])
    hist = f"{e('hist')} Раньше: " + " · ".join(history) if history and len(history) > 0 else ""
    note = "<i>Дата без времени старта — в игре событие стартует в течение дня.</i>"

    def build(level: int) -> str:
        parts = [head, LINE, "\n".join(when_block)]
        if parent:
            parts.append(parent)
        if desc and level < 2:
            parts.append(f"<blockquote>{escape(desc[: 300 if level else 500])}</blockquote>")
        if mean:
            m = mean[: 2 if level >= 1 else 5]
            parts.append(f"<b>{e('idea')} Что это значит</b>\n" + "\n".join(f"• {escape(x)}" for x in m))
        if monsters:
            k = 5 if level >= 2 else 10 if level == 1 else 20
            more = f" и ещё {len(monsters) - k}" if len(monsters) > k else ""
            parts.append(f"{e('shop')} <b>Монстры:</b> " + ", ".join(escape(x) for x in monsters[:k]) + more)
        if hist and level < 3:
            parts.append(hist)
        if level < 1:
            parts.append(note)
        return "\n\n".join(p for p in parts if p)

    for level in range(4):
        text = build(level)
        if visible_len(text) <= limit:
            return text
    return text[:limit]


def paginate_lines(lines: list[str], header: str) -> str:
    out, size = [header], len(header)
    for ln in lines:
        if size + len(ln) > 3800:
            out.append("…")
            break
        out.append(ln)
        size += len(ln) + 2
    return "\n\n".join(out)


def shop_item(r: dict, now: datetime, tz: str, num: int | None = None) -> str:
    st = status_of(r, now, tz)
    icon = {"active": "🟢", "upcoming": "🟡", "past": "⚪"}[st]
    pred = f" {e('pred')}" if r.get("status") == "predicted" else ""
    ev = f"\n      {e('parent')} {escape(r['event'])}" if r.get("event") else ""
    return (f"{icon} <b>{escape(r['monster'])}</b>{pred}\n"
            f"      {e('cal')} {fmt_range(r, tz)} · {when(r, now, tz)}{ev}")


def shop_line(r: dict, now: datetime, tz: str = "UTC") -> str:
    return shop_item(r, now, tz)


def wiki_url(base: str, title: str) -> str:
    return f"{base}/wiki/{quote(title.replace(' ', '_'))}"
