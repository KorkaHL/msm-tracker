"""Внешние источники анонсов (все публичные и без ключей, проверены с раннера Actions):

  • Bluesky официального аккаунта My Singing Monsters   — посты с датами → «официальные анонсы»
  • Steam News (My Singing Monsters, appid 1419170)     — посты об обновлениях
  • RSS Big Blue Bubble                                  — новости/обновления издателя
  • Вики: History/<год>                                  — даты вперёд, которые вносят редакторы
  • Вики: Upcoming Features                              — «дорожная карта» без дат (раздел «В разработке»)

Каждый источник изолирован: падение одного не ломает остальные.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import significance
from .dates import MONTHS, day_end_exclusive, day_start, find_dates, iso
from .extract import slug
from .wiki import UA, http_json
from .wikitext import clean

log = logging.getLogger("msm.sources")

MAX_ANNOUNCE_DAYS = 60
_KEYWORDS = re.compile(
    r"event|availab|start|begin|return|live|update|promotion|sale|coming|celebrat|monster|island|season|anniversary|"
    r"giveaway|contest|challenge|costume|relic|diamond|bundle|showcase|premiere|release|open|tonight|tomorrow", re.I)
_URL = re.compile(r"https?://\S+|\b\w+\.(?:com|gg|be|ly)/\S*")
_HASHTAG = re.compile(r"#\w+")


@dataclass
class Post:
    source: str        # bluesky | steam | bbb
    uid: str
    ts: datetime
    title: str
    text: str
    url: str
    image: str | None = None


# ---------------------------------------------------------------- загрузка
def fetch_bluesky(actor: str = "mysingingmonsters.com", limit: int = 50) -> list[Post]:
    q = urllib.parse.urlencode({"actor": actor, "limit": limit, "filter": "posts_no_replies"})
    data = http_json(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?{q}")
    out: list[Post] = []
    for item in data.get("feed", []):
        if item.get("reason"):  # репосты пропускаем
            continue
        p = item["post"]
        rec = p.get("record", {})
        text = rec.get("text", "")
        if not text:
            continue
        rkey = p["uri"].rsplit("/", 1)[-1]
        emb = p.get("embed") or {}
        img = None
        for k in ("images", "items"):
            if emb.get(k):
                img = emb[k][0].get("fullsize") or emb[k][0].get("thumbnail")
                break
        if not img and emb.get("external", {}).get("thumb"):
            img = emb["external"]["thumb"]
        out.append(Post("bluesky", rkey, _parse_ts(rec.get("createdAt")), text.split("\n")[0][:90], text,
                        f"https://bsky.app/profile/{actor}/post/{rkey}", img))
    return out


def fetch_steam(appid: int = 1419170, count: int = 20) -> list[Post]:
    data = http_json(f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid={appid}&count={count}&maxlength=4000")
    out: list[Post] = []
    for n in data.get("appnews", {}).get("newsitems", []):
        text = re.sub(r"\[/?[a-z0-9*=_ ]+\]", " ", n.get("contents", ""), flags=re.I)
        out.append(Post("steam", str(n["gid"]), datetime.fromtimestamp(n["date"], tz=timezone.utc), n["title"],
                        html.unescape(text), n.get("url", "")))
    return out


def fetch_bbb(url: str = "https://www.bigbluebubble.com/feed", limit: int = 15) -> list[Post]:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return parse_rss(r.read(), limit)


def parse_rss(xml_bytes: bytes, limit: int = 15) -> list[Post]:
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    root = ET.fromstring(xml_bytes)
    out: list[Post] = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        body = it.findtext("content:encoded", namespaces=ns) or it.findtext("description") or ""
        img = re.search(r'<img[^>]+src="([^"]+)"', body)
        text = html.unescape(re.sub(r"<[^>]+>", " ", body))
        text = re.sub(r"\s+", " ", text).strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
        link = (it.findtext("link") or "").strip()
        out.append(Post("bbb", hashlib.sha1(link.encode()).hexdigest()[:10], ts, title, text, link,
                        img.group(1) if img else None))
        if len(out) >= limit:
            break
    return out


def _parse_ts(s: str | None) -> datetime:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------- посты → события
def _title_from(post: Post) -> str:
    t = _HASHTAG.sub("", _URL.sub("", post.title or post.text)).strip(" -–—:!•\n")
    t = re.sub(r"\s+", " ", t)
    return (t[:80].rsplit(" ", 1)[0] + "…") if len(t) > 80 else (t or "Анонс")


def _shift_year(d: date, post_day: date) -> date:
    """Даты без года в посте: если получилась дата сильно «до» поста — это следующий год."""
    if (post_day - d).days > 60:
        try:
            return d.replace(year=d.year + 1)
        except ValueError:
            return d
    return d


def posts_to_events(posts: list[Post], today: date, known: list[dict] | None = None) -> list[dict]:
    """Посты, где названы будущие/текущие даты → события «announced»."""
    known = known or []
    out: dict[str, dict] = {}
    for p in posts:
        text = p.text
        if not _KEYWORDS.search(text):
            continue
        post_day = p.ts.date()
        for sp in find_dates(text, post_day.year):
            s, e = sp.start, sp.end
            if not sp.year_explicit:
                s2 = _shift_year(s, post_day)
                e = e.replace(year=e.year + (s2.year - s.year)) if s2 != s else e
                s = s2
            if e < today - timedelta(days=1) or (e - s).days > MAX_ANNOUNCE_DAYS or s < post_day - timedelta(days=1):
                continue  # прошлое / слишком длинное / дата раньше самого поста — не анонс
            title = _title_from(p)
            low = text.lower()
            if any(abs((parse_day(k["start"]) - s).days) <= 1 and k["title"].lower() in low for k in known):
                continue  # то же самое уже есть из вики
            kind, tags = significance.analyze(title, text)
            eid = f"ann-{p.source}-{p.uid[:10]}-{s.isoformat()}"
            out[eid] = {
                "id": eid, "series": slug(title), "title": title, "kind": kind, "tags": tags,
                "start": iso(day_start(s)), "end": iso(day_end_exclusive(e)), "precision": "day",
                "status": "announced", "confidence": 1.0, "source": f"{p.source}:{p.uid}",
                "description": re.sub(r"\s+", " ", _URL.sub("", text)).strip()[:400],
                "monsters": [], "url": p.url, "open_ended": sp.start == sp.end,
                "announced_at": iso(p.ts), **({"image": p.image} if p.image else {}),
            }
            break  # одна основная дата на пост
    return list(out.values())


def parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").date()


# ---------------------------------------------------------------- вики: History/<год>
# Структура страницы (проверена на живой вики):
#   ==November 2026==
#   *'''November 18th:'''
#   **The eighth [[Feast-Ember]] will be celebrated.<ref>…</ref>
#   ==December 2026==
#   *'''Unknown dates:'''
#   **The fifteenth [[Festival of Yay]] will be celebrated.
#   ==Unknown Months==
#   *The [[Bone Island]] [[Titansouls|Titansoul]] will be released.
_DAY = re.compile(r"^\*+\s*'''?\s*([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*:?\s*'''?\s*:?(.*)$")
_H2 = re.compile(r"^==\s*([^=].*?)\s*==\s*$")
_LINK1 = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]")


def _hist_title(raw: str, txt: str) -> str:
    if re.search(r"celebrated", txt, re.I):
        m = _LINK1.search(raw)
        if m:
            return m.group(1).strip()
    t = re.sub(r"^(?:A|An|The)\s+", "", txt.strip().rstrip("."))
    return (t[0].upper() + t[1:])[:90] if t else txt[:90]


def history_events(wikitext: str, year: int, today: date, base_url: str) -> tuple[list[dict], list[dict]]:
    """→ (анонсы с датой СЕГОДНЯ или ПОЗЖЕ, планы без точной даты [{when:'2026-12'|'', text}])."""
    dated: list[tuple[date, str, str]] = []
    plans: list[dict] = []
    cur: date | None = None
    month_key = ""
    unknown_sub = False
    unknown_section = False
    for raw in wikitext.splitlines():
        line = raw.strip()
        h = _H2.match(line)
        if h:
            name = clean(h.group(1)).strip()
            cur, unknown_sub = None, False
            unknown_section = name.lower().startswith("unknown")
            mm = re.match(r"([A-Za-z]+)\s+(\d{4})", name)
            month_key = f"{int(mm.group(2))}-{MONTHS[mm.group(1).lower()]:02d}" if mm and mm.group(1).lower() in MONTHS else ""
            continue
        if line.startswith("**"):
            body = line.lstrip("*")
            txt = clean(body).strip()
            if not txt:
                continue
            if cur:
                dated.append((cur, txt, body))
            elif unknown_sub:
                plans.append({"when": month_key, "text": txt[:200]})
        elif line.startswith("*"):
            m = _DAY.match(line)
            unknown_sub = False
            if m and m.group(1).lower() in MONTHS:
                try:
                    cur = date(year, MONTHS[m.group(1).lower()], int(m.group(2)))
                except ValueError:
                    cur = None
                rest = clean(m.group(3)).strip()
                if cur and rest:
                    dated.append((cur, rest, m.group(3)))
            else:
                cur = None
                txt = clean(line.lstrip("*")).strip()
                if re.match(r"unknown dates?", txt, re.I):
                    unknown_sub = True
                elif unknown_section and txt:
                    plans.append({"when": "", "text": txt[:200]})
    out: list[dict] = []
    for d, txt, raw in dated:
        if d < today:
            continue
        title = _hist_title(raw, txt)
        kind, tags = significance.analyze(title, txt)
        eid = f"hist-{d.isoformat()}-{hashlib.sha1(txt.encode()).hexdigest()[:6]}"
        out.append({
            "id": eid, "series": slug(title)[:60], "title": title, "kind": kind, "tags": tags,
            "start": iso(day_start(d)), "end": iso(day_end_exclusive(d)), "precision": "day",
            "status": "announced", "confidence": 1.0, "source": f"history:{year}", "description": txt[:300],
            "monsters": [], "url": f"{base_url}/wiki/History/{year}", "open_ended": True,
        })
    return out, plans


# ---------------------------------------------------------------- вики: Upcoming Features
_PLINK = re.compile(r"\{\{PortraitLink\|([^|}]+)\|Upcoming\|([^|}]+)", re.I)
_HEAD_CELL = re.compile(r"^!\s*(?:colspan=\"?\d+\"?\s*\|)?\s*(Upcoming[^|\n]+?)\s*$", re.I)
_HEADING = re.compile(r"^={2,}\s*(.+?)\s*={2,}\s*$")


def roadmap(wikitext: str) -> list[dict]:
    """Ещё не вышедшие монстры из «Upcoming Features» (без дат): [{group, monsters}]."""
    groups: dict[str, list[str]] = {}
    group = "Upcoming"
    for line in wikitext.splitlines():
        line = line.strip()
        h = _HEAD_CELL.match(line)
        if h:
            group = clean(h.group(1)).strip()
            continue
        hh = _HEADING.match(line)
        if hh:
            group = clean(hh.group(1)).strip()
        for m in _PLINK.finditer(line):
            groups.setdefault(group, [])
            name = m.group(1).strip()
            if name not in groups[group]:
                groups[group].append(name)
    return [{"group": g, "monsters": ms} for g, ms in groups.items() if ms]
