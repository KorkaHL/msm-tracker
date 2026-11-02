"""Клиент MediaWiki API (Fandom). Только чтение, стандартная библиотека."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "msm-tracker/1.0 (+https://github.com; Telegram bot for My Singing Monsters events)"


def http_json(url: str, *, data: dict | None = None, headers: dict | None = None,
              timeout: int = 40, retries: int = 3) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    hdrs = {"User-Agent": UA, **({"Content-Type": "application/json"} if body else {}), **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 409):  # ретраи бессмысленны
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed: {url}: {last}")


class Wiki:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def api(self, **params) -> dict:
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        return http_json(f"{self.base}/api.php?{urllib.parse.urlencode(params)}")

    def page_url(self, title: str) -> str:
        return f"{self.base}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"

    def category_members(self, category: str, *, limit: int = 2000, recurse: int = 1) -> list[str]:
        """Страницы категории (и подкатегорий до глубины recurse)."""
        titles: list[str] = []
        seen_cats: set[str] = set()

        def walk(cat: str, depth: int) -> None:
            if cat in seen_cats or len(titles) >= limit:
                return
            seen_cats.add(cat)
            cont: dict = {}
            while True:
                r = self.api(action="query", list="categorymembers", cmtitle=f"Category:{cat}",
                             cmlimit="500", cmtype="page|subcat", **cont)
                for m in r.get("query", {}).get("categorymembers", []):
                    if m["title"].startswith("Category:"):
                        if depth < recurse:
                            walk(m["title"].split(":", 1)[1], depth + 1)
                    elif m["ns"] == 0:
                        titles.append(m["title"])
                if "continue" not in r:
                    break
                cont = r["continue"]

        walk(category, 0)
        return list(dict.fromkeys(titles))

    def wikitexts(self, titles: list[str]) -> dict[str, str]:
        """Вики-текст страниц пачками по 50."""
        out: dict[str, str] = {}
        for i in range(0, len(titles), 50):
            batch = titles[i : i + 50]
            r = self.api(action="query", prop="revisions", rvprop="content", rvslots="main",
                         redirects="1", titles="|".join(batch))
            for p in r.get("query", {}).get("pages", []):
                if p.get("missing"):
                    continue
                try:
                    out[p["title"]] = p["revisions"][0]["slots"]["main"]["content"]
                except (KeyError, IndexError):
                    continue
        return out

    def search(self, q: str, limit: int = 10) -> list[str]:
        r = self.api(action="query", list="search", srsearch=q, srlimit=str(limit))
        return [x["title"] for x in r.get("query", {}).get("search", [])]

    def categories_with_prefix(self, prefix: str, limit: int = 50) -> list[str]:
        r = self.api(action="query", list="allcategories", acprefix=prefix, aclimit=str(limit))
        return [c["category"] for c in r.get("query", {}).get("allcategories", [])]
