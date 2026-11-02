"""Извлечение дат и диапазонов дат из английского текста вики."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"
_SEP = r"(?:-|–|—|to|until|through|till)"


def _date_pat(n: int) -> str:
    return (
        rf"(?:(?P<m{n}>{_MON})\b\.?\s+(?P<d{n}>\d{{1,2}}){_ORD}\b"
        rf"|(?P<e{n}>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<f{n}>{_MON})\b\.?)"
        rf"(?:,?\s+(?P<y{n}>\d{{4}})\b)?"
    )


_LB = r"(?<![A-Za-z0-9])"
_RE_ISO_RANGE = re.compile(
    rf"{_LB}(\d{{4}})-(\d{{2}})-(\d{{2}})\s*(?:–|—|to|until|\s-\s)\s*(\d{{4}})-(\d{{2}})-(\d{{2}})"
)
_RE_DD_RANGE = re.compile(
    rf"{_LB}(?P<d1>\d{{1,2}}){_ORD}\s*{_SEP}\s*(?P<d2>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<mo>{_MON})\b\.?"
    rf"(?:,?\s+(?P<y>\d{{4}})\b)?",
    re.I,
)
_RE_RANGE = re.compile(
    rf"{_LB}{_date_pat(1)}\s*{_SEP}\s*(?:{_date_pat(2)}|(?P<d3>\d{{1,2}}){_ORD}\b(?:,?\s+(?P<y3>\d{{4}})\b)?)",
    re.I,
)
_RE_SINGLE = re.compile(rf"{_LB}{_date_pat(1)}", re.I)
_RE_ISO = re.compile(rf"{_LB}(\d{{4}})-(\d{{2}})-(\d{{2}})(?!\d)")


@dataclass(frozen=True)
class DateSpan:
    start: date
    end: date  # включительно
    ranged: bool
    year_explicit: bool
    pos: tuple[int, int]


def _mk(y: int, m: int, d: int) -> date:
    return date(y, m, d)


def _month(g: str | None) -> int | None:
    return MONTHS[g.lower()] if g else None


def find_dates(text: str, default_year: int, *, include_single: bool = True) -> list[DateSpan]:
    """Находит диапазоны (и опционально одиночные даты) в тексте."""
    found: list[DateSpan] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    def add(span: DateSpan) -> None:
        taken.append(span.pos)
        found.append(span)

    for m in _RE_ISO_RANGE.finditer(text):
        try:
            s = _mk(*map(int, m.group(1, 2, 3)))
            e = _mk(*map(int, m.group(4, 5, 6)))
        except ValueError:
            continue
        if e >= s and free(m.span()):
            add(DateSpan(s, e, True, True, m.span()))

    for m in _RE_DD_RANGE.finditer(text):
        if not free(m.span()):
            continue
        mo = MONTHS[m["mo"].lower()]
        y = int(m["y"]) if m["y"] else None
        try:
            ye = y or default_year
            s, e = _mk(ye, mo, int(m["d1"])), _mk(ye, mo, int(m["d2"]))
        except ValueError:
            continue
        if e >= s:
            add(DateSpan(s, e, True, y is not None, m.span()))

    for m in _RE_RANGE.finditer(text):
        if not free(m.span()):
            continue
        mo1 = _month(m["m1"] or m["f1"])
        d1 = int(m["d1"] or m["e1"])
        y1 = int(m["y1"]) if m["y1"] else None
        if m["d3"]:
            mo2, d2, y2 = mo1, int(m["d3"]), (int(m["y3"]) if m["y3"] else None)
        else:
            mo2 = _month(m["m2"] or m["f2"])
            d2 = int(m["d2"] or m["e2"])
            y2 = int(m["y2"]) if m["y2"] else None
        try:
            if y1 is None and y2 is None:
                ys = ye = default_year
                if (mo2, d2) < (mo1, d1):
                    ye += 1
            elif y1 is None:
                ye = y2
                ys = ye - 1 if (mo1, d1) > (mo2, d2) else ye
            elif y2 is None:
                ys = y1
                ye = ys + 1 if (mo2, d2) < (mo1, d1) else ys
            else:
                ys, ye = y1, y2
            s, e = _mk(ys, mo1, d1), _mk(ye, mo2, d2)
        except ValueError:
            continue
        if e >= s:
            add(DateSpan(s, e, True, (y1 or y2) is not None, m.span()))

    if include_single:
        for m in _RE_ISO.finditer(text):
            if free(m.span()):
                try:
                    d = _mk(*map(int, m.group(1, 2, 3)))
                except ValueError:
                    continue
                add(DateSpan(d, d, False, True, m.span()))
        for m in _RE_SINGLE.finditer(text):
            if not free(m.span()):
                continue
            mo = _month(m["m1"] or m["f1"])
            d = int(m["d1"] or m["e1"])
            y = int(m["y1"]) if m["y1"] else None
            try:
                dd = _mk(y or default_year, mo, d)
            except ValueError:
                continue
            add(DateSpan(dd, dd, False, y is not None, m.span()))

    found.sort(key=lambda s: s.pos)
    return found


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def day_end_exclusive(d: date) -> datetime:
    return day_start(d + timedelta(days=1))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
