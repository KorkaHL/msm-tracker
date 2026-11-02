"""Сборка датасета: вики → события → прогноз → расписание магазина."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import extract
from .dates import iso
from .predict import predict
from .wiki import Wiki

log = logging.getLogger("msm.pipeline")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "events.json"


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or ROOT / "config" / "sources.json").read_text())


def build_shop(events: list[dict], windows: list[dict]) -> list[dict]:
    """Расписание «когда монстр доступен»: из событий и из страниц самих монстров."""
    rows: dict[tuple, dict] = {}
    for e in events:
        for m in e.get("monsters", []):
            key = (m, e["start"], e["end"])
            rows[key] = {"monster": m, "start": e["start"], "end": e["end"], "event_id": e["id"],
                         "event": e["title"], "status": e["status"], "confidence": e.get("confidence", 1.0)}
    for w in windows:
        key = (w["monster"], w["start"], w["end"])
        rows.setdefault(key, {**w, "status": "confirmed", "confidence": 0.8, "event_id": None, "event": None})
    return sorted(rows.values(), key=lambda r: (r["start"], r["monster"]))


def merge_first_seen(events: list[dict], previous: dict | None, now_iso: str) -> None:
    """first_seen нужен, чтобы уведомлять о НОВЫХ анонсах; при первом запуске — базовая линия."""
    prev = {e["id"]: e.get("first_seen") for e in (previous or {}).get("events", [])}
    baseline = previous is None
    for e in events:
        e["first_seen"] = prev.get(e["id"]) or ("1970-01-01T00:00:00Z" if baseline else now_iso)


def sanity_ok(new_events: list[dict], previous: dict | None) -> tuple[bool, str]:
    confirmed = [e for e in new_events if e["status"] == "confirmed"]
    if not confirmed:
        return False, "парсер не нашёл ни одного события (изменилась разметка вики или категория?)"
    if previous:
        old = len([e for e in previous.get("events", []) if e["status"] == "confirmed"])
        if old >= 10 and len(confirmed) < old * 0.5:
            return False, f"событий стало заметно меньше: {old} → {len(confirmed)}"
    return True, "ok"


def build_dataset(cfg: dict, wiki: Wiki, today: datetime, previous: dict | None = None) -> dict:
    d = today.date()
    monster_titles: set[str] = set()
    for cat in cfg.get("monster_categories", []):
        try:
            monster_titles |= set(wiki.category_members(cat, recurse=1))
        except Exception as e:  # одна битая категория не должна ронять всё
            log.warning("monster category %s: %s", cat, e)
    mt = frozenset(monster_titles)
    log.info("monsters known: %d", len(mt))

    event_titles: list[str] = []
    for cat in cfg.get("event_categories", []):
        try:
            event_titles += wiki.category_members(cat, recurse=cfg.get("category_depth", 1))
        except Exception as e:
            log.warning("event category %s: %s", cat, e)
    event_titles += cfg.get("event_pages", [])
    event_titles = list(dict.fromkeys(event_titles))
    texts = wiki.wikitexts(event_titles)
    log.info("event pages fetched: %d/%d", len(texts), len(event_titles))

    events: list[dict] = []
    list_pages = set(cfg.get("list_pages", []))
    for title, text in texts.items():
        if title in list_pages:
            events += extract.events_from_tables(title, text, today=d, base_url=wiki.base, monster_titles=mt)
        else:
            events += extract.extract_events(title, text, today=d, page_url=wiki.page_url(title), monster_titles=mt)
    for title in list_pages - set(texts):
        extra = wiki.wikitexts([title])
        for t, text in extra.items():
            events += extract.events_from_tables(t, text, today=d, base_url=wiki.base, monster_titles=mt)

    by_id = {e["id"]: e for e in events}
    events = list(by_id.values())

    windows: list[dict] = []
    mp = sorted(mt)[: cfg.get("max_monster_pages", 600)]
    if cfg.get("scan_monster_pages", True) and mp:
        for title, text in wiki.wikitexts(mp).items():
            windows += extract.monster_windows(title, text, today=d)

    events += predict(events, d)
    events.sort(key=lambda e: (e["start"], e["title"]))
    now_iso = iso(today)
    merge_first_seen(events, previous, now_iso)
    return {
        "generated_at": now_iso,
        "wiki": wiki.base,
        "events": events,
        "shop": build_shop(events, windows),
        "stats": {"events": len(events), "monsters": len(mt), "event_pages": len(texts)},
    }


def load_dataset(path: Path = DATA) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_dataset(ds: dict, path: Path = DATA) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ds, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
