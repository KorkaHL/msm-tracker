"""Прогноз повторяющихся событий по датам прошлых лет."""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .dates import iso, parse_iso

HORIZON_DAYS = 400
MIN_CONFIDENCE = 0.3


def _norm_ordinal(d: date) -> int:
    """День года, нормализованный к невисокосному году (чтобы сравнивать даты)."""
    return date(2001, d.month, min(d.day, 28) if d.month == 2 else d.day).toordinal()


def predict(events: list[dict], today: date) -> list[dict]:
    series: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("status") == "confirmed":
            series[e["series"]].append(e)

    out: list[dict] = []
    now = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    for key, occ in series.items():
        starts = [parse_iso(e["start"]) for e in occ]
        ends = [parse_iso(e["end"]) for e in occ]
        past_years = sorted({s.year for s in starts})
        if not past_years:
            continue
        durations = [(b - a).days for a, b in zip(starts, ends)]
        dur = max(1, round(statistics.median(durations)))
        norm = [_norm_ordinal(s.date()) for s in starts]
        mid = round(statistics.median(norm))
        spread = max(norm) - min(norm)
        base = date.fromordinal(mid)
        n_years = len(past_years)
        if n_years < 2:  # одноразовые события (напр. «New Rare Introduction») не прогнозируем
            continue
        conf = 0.4 + 0.15 * min(n_years - 1, 4)
        if spread > 14:
            conf *= 0.6
        if conf < MIN_CONFIDENCE:
            continue
        template = occ[-1]
        for year in (today.year, today.year + 1):
            if year in past_years:
                continue
            try:
                start = datetime(year, base.month, base.day, tzinfo=timezone.utc)
            except ValueError:
                continue
            end = start + timedelta(days=dur)
            if end <= now or start > now + timedelta(days=HORIZON_DAYS):
                continue
            ev = {k: template[k] for k in ("series", "title", "kind", "tags", "description", "monsters", "url", "parent", "section")
                  if k in template}
            ev.update({
                "id": f"{key}-{start.date().isoformat()}-pred",
                "start": iso(start), "end": iso(end), "precision": "day",
                "status": "predicted", "confidence": round(conf, 2),
                "source": f"prediction:{n_years}y,spread={spread}d",
            })
            out.append(ev)
    return out
