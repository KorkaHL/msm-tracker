"""Извлечение событий и окон доступности монстров из вики-страниц."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import significance
from .dates import DateSpan, day_end_exclusive, day_start, find_dates, iso
from .wikitext import Table, clean, lead_paragraph, links, parse_tables, parse_templates, strip_comments

DATE_KEYS = ("date", "dates", "start", "starts", "start date", "startdate", "begins", "available",
             "duration", "time", "when", "release", "release date", "released", "period", "event date")
END_KEYS = ("end", "ends", "end date", "enddate", "until")
MAX_EVENT_DAYS = 120
MIN_YEAR = 2012

_YEAR_HEADING = re.compile(r"^=+\s*(?:.*?\b)?((?:19|20)\d{2})\b.*?=+\s*$")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _span_ok(s: DateSpan, today: date) -> bool:
    return (MIN_YEAR <= s.start.year <= today.year + 2
            and (s.end - s.start).days <= MAX_EVENT_DAYS)


def _event_dict(title: str, s: DateSpan, meta: dict, source: str) -> dict:
    return {
        "id": f"{slug(title)}-{s.start.isoformat()}",
        "series": slug(title),
        "title": title,
        "start": iso(day_start(s.start)),
        "end": iso(day_end_exclusive(s.end)),
        "precision": "day",
        "status": "confirmed",
        "confidence": 1.0,
        "source": source,
        **meta,
    }


def _meta(title: str, wikitext: str, cleaned: str, monster_titles: frozenset[str], page_url: str) -> dict:
    kind, tags = significance.analyze(title, cleaned)
    monsters = [l for l in dict.fromkeys(links(wikitext)) if l in monster_titles][:25]
    return {
        "kind": kind,
        "tags": tags,
        "description": lead_paragraph(cleaned),
        "monsters": monsters,
        "url": page_url,
    }


def _infobox_spans(wikitext: str, today: date) -> list[tuple[DateSpan, str]]:
    out: list[tuple[DateSpan, str]] = []
    for tpl in parse_templates(strip_comments(wikitext)):
        p = tpl.params
        start_raw = next((p[k] for k in ("start", "starts", "start date", "startdate", "begins") if k in p), None)
        end_raw = next((p[k] for k in END_KEYS if k in p), None)
        if start_raw and end_raw:
            a = find_dates(clean(start_raw), today.year)
            b = find_dates(clean(end_raw), today.year)
            if a and b and b[0].end >= a[0].start:
                out.append((DateSpan(a[0].start, b[0].end, True, a[0].year_explicit, (0, 0)), f"infobox:{tpl.name}"))
                continue
        for k in DATE_KEYS:
            if k in p:
                for s in find_dates(clean(p[k]), today.year):
                    out.append((s, f"infobox:{tpl.name}.{k}"))
    return out


def _body_spans(cleaned: str, today: date) -> list[tuple[DateSpan, str]]:
    """Диапазоны дат из текста; год наследуется от заголовков вида «== 2024 ==»."""
    out: list[tuple[DateSpan, str]] = []
    year = today.year
    for line in cleaned.splitlines():
        m = _YEAR_HEADING.match(line.strip())
        if m:
            year = int(m.group(1))
            continue
        for s in find_dates(line, year, include_single=False):
            out.append((s, f"text:{line.strip()[:80]}"))
    return out


def extract_events(title: str, wikitext: str, *, today: date, page_url: str = "",
                   monster_titles: frozenset[str] = frozenset()) -> list[dict]:
    cleaned = clean(wikitext)
    meta = _meta(title, wikitext, cleaned, monster_titles, page_url)
    seen: set[tuple[date, date]] = set()
    events: list[dict] = []
    for s, src in _infobox_spans(wikitext, today) + _body_spans(cleaned, today):
        if (s.start, s.end) in seen or not _span_ok(s, today):
            continue
        # одиночные даты из инфобокса — событие на один день
        seen.add((s.start, s.end))
        events.append(_event_dict(title, s, meta, src))
    return events


def events_from_tables(page_title: str, wikitext: str, *, today: date, base_url: str,
                       monster_titles: frozenset[str] = frozenset()) -> list[dict]:
    """Страницы-списки событий: каждая строка таблицы с датой — событие.

    Название берётся из первой ячейки со ссылкой (или первой ячейки)."""
    events: list[dict] = []
    for table in parse_tables(wikitext):
        for row in table.rows:
            raw_row = " | ".join(row)
            spans = [s for c in row for s in find_dates(clean(c), today.year, include_single=False)]
            if not spans:
                continue
            name = None
            for c in row:
                ls = links(c)
                if ls:
                    name = ls[0]
                    break
            name = name or clean(row[0]).strip().split("\n")[0]
            if not name or len(name) > 80:
                continue
            cleaned = clean(raw_row)
            meta = _meta(name, raw_row, cleaned, monster_titles, f"{base_url}/wiki/{name.replace(' ', '_')}")
            meta["description"] = meta["description"] or cleaned[:300].strip()
            for s in spans:
                if _span_ok(s, today):
                    events.append(_event_dict(name, s, meta, f"table:{page_title}"))
    return events


_AVAIL_WORDS = re.compile(r"\b(availab\w*|sale|market|shop|store|breed\w*|returns?|return(?:ed)?|obtain\w*|offered)\b", re.I)


def monster_windows(title: str, wikitext: str, *, today: date) -> list[dict]:
    """Окна доступности монстра по строкам страницы со словами «available/market/…»."""
    cleaned = clean(wikitext)
    out: list[dict] = []
    year = today.year
    for line in cleaned.splitlines():
        m = _YEAR_HEADING.match(line.strip())
        if m:
            year = int(m.group(1))
            continue
        if not _AVAIL_WORDS.search(line):
            continue
        for s in find_dates(line, year, include_single=False):
            if _span_ok(s, today):
                out.append({"monster": title, "start": iso(day_start(s.start)),
                            "end": iso(day_end_exclusive(s.end)), "source": f"monster-page:{line.strip()[:80]}"})
    return out


# ---------------------------------------------------------------------------
# Страницы Availability*: таблицы «From | To | Monster(s) | Occasion» по годам.
# Реальная структура вики My Singing Monsters (проверена по логам Actions):
#   ==Раздел==  →  ===2026===  →  {| ... !From !To !Monster(s) !Occasion ... |}
# Даты записаны как «January 15th» (год берётся из заголовка).
# ---------------------------------------------------------------------------
_HEADING = re.compile(r"^(=+)\s*(.*?)\s*\1\s*$", re.M)
_TABLE = re.compile(r"\{\|.*?\n\|\}", re.S)


def availability_tables(wikitext: str):
    """→ [(раздел, год|None, Table)] в порядке следования на странице."""
    text = strip_comments(wikitext)
    items: list[tuple[int, str, object]] = []
    for m in _HEADING.finditer(text):
        items.append((m.start(), "h", (len(m.group(1)), m.group(2))))
    for m in _TABLE.finditer(text):
        items.append((m.start(), "t", m.group(0)))
    items.sort(key=lambda x: x[0])
    section, year = "", None
    out = []
    for _, kind, val in items:
        if kind == "h":
            level, name = val
            ym = re.fullmatch(r"(?:19|20)\d{2}", clean(name).strip())
            if ym:
                year = int(ym.group(0))
            elif level <= 2:
                section, year = clean(name).strip(), None
        else:
            for t in parse_tables(val):
                out.append((section, year, t))
    return out


def _cell_text(raw: str) -> str:
    raw = raw.replace("<nowiki>'</nowiki>", "\u2019")
    return re.sub(r"\s*\n\s*", " ", clean(raw)).strip()


def availability_rows(wikitext: str, *, today: date) -> list[dict]:
    rows: list[dict] = []
    for section, year, table in availability_tables(wikitext):
        h = [clean(x).strip().lower() for x in table.headers]
        if year is None or len(h) < 4 or h[0] != "from" or h[1] != "to":
            continue
        if year > today.year + 2:
            continue
        for row in table.rows:
            if len(row) < 4:
                continue
            a = find_dates(clean(row[0]), year)
            b = find_dates(clean(row[1]), year)
            if not a or not b:
                continue
            s, e = a[0].start, b[0].start
            if e < s:
                try:
                    e = e.replace(year=e.year + 1)
                except ValueError:
                    continue
            if (e - s).days > MAX_EVENT_DAYS:
                continue
            names = list(dict.fromkeys(links(row[2]))) or [_cell_text(row[2])]
            rows.append({
                "section": section,
                "start": s, "end": e,
                "monsters": [n for n in names if n],
                "note": _cell_text(row[2]),
                "occasion": _cell_text(row[3]),
                "occ_links": list(dict.fromkeys(links(row[3]))),
            })
    return rows


_PROMO = re.compile(r"['\u2018\u2019]+([^'\u2018\u2019]{3,}?)['\u2018\u2019]+\s*Promotion", re.I)


def _strip_rarity(name: str) -> str:
    return re.sub(r"^(Rare|Epic)\s+", "", name)


def events_from_availability(rows: list[dict], *, base_url: str, source: str) -> list[dict]:
    """Строки доступности → события (акции, сезонные события, out-of-season, прочее)."""
    merged: dict[tuple, dict] = {}
    for r in rows:
        occ = r["occasion"]
        low = occ.lower()
        parent = re.search(r"\(([^)]*)\)", occ)
        parent_txt = parent.group(1).strip() if parent else ""
        if "promotion" in low:
            m = _PROMO.search(occ)
            name = (m.group(1) if m else re.split(r"\bPromotion\b", occ, flags=re.I)[0]).strip(" '\u2019\u2018") or occ
            title, link = f"{name} (Promotion)", (r["occ_links"][0] if r["occ_links"] else r["monsters"][0])
        elif "out-of-season" in low or "out of season" in low:
            title, link = f"Out-of-Season: {_strip_rarity(r['monsters'][0])}", r["monsters"][0]
        elif r["occ_links"] and re.search(r"\b(?:19|20)\d{2}\b", occ):
            title, link = r["occ_links"][0], r["occ_links"][0]
        else:
            title, link = re.sub(r"\s+", " ", occ) or "Availability", (r["occ_links"] or r["monsters"])[0]
        text = f"{occ} {parent_txt} {r['section']}"
        kind, tags = significance.analyze(title, text)
        tags = list(dict.fromkeys(["monsters", *tags]))
        key = (slug(title), r["start"], r["end"])
        if key in merged:
            merged[key]["monsters"] = list(dict.fromkeys(merged[key]["monsters"] + r["monsters"]))
            continue
        merged[key] = {
            "id": f"{slug(title)}-{r['start'].isoformat()}",
            "series": slug(title),
            "title": title,
            "start": iso(day_start(r["start"])),
            "end": iso(day_end_exclusive(r["end"])),
            "precision": "day", "status": "confirmed", "confidence": 1.0,
            "source": f"{source}:{r['section']}",
            "kind": kind, "tags": tags,
            "description": (f"{parent_txt}. " if parent_txt else "") + (f"{r['section']}." if r["section"] else ""),
            "monsters": list(r["monsters"]),
            "occasion": occ,
            "url": f"{base_url}/wiki/{link.replace(' ', '_')}",
        }
    return list(merged.values())
