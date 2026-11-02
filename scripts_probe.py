"""Диагностика структуры вики (запускается в Actions: workflow probe.yml)."""
import json, sys
from msm.wiki import Wiki
w = Wiki("https://mysingingmonsters.fandom.com")
for p in ("Event", "Season", "Limited", "Holiday", "Calendar", "Wublin", "Celestial", "Amber", "Time", "Rare", "Epic"):
    print("CAT", p, w.categories_with_prefix(p, 30))
queries = ["events", "seasonal event", "event calendar", "limited time", "Yuletide", "Halloween"]
hits = []
for q in queries:
    r = w.search(q, 8)
    print("SEARCH", q, r)
    hits += r[:2]
show = list(dict.fromkeys(hits))[:8]
for t, txt in w.wikitexts(show).items():
    print("=" * 30, "PAGE", t, len(txt))
    print(txt[:1200])
for c in ("Monsters", "Seasonal Monsters", "Events"):
    try:
        print("MEMBERS", c, w.category_members(c, recurse=0)[:25])
    except Exception as e:
        print("MEMBERS", c, "ERR", e)
