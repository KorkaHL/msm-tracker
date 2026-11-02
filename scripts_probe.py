"""Диагностика структуры вики: PAGES='A|B|C' CHARS=4000 python scripts_probe.py"""
import os
from msm.wiki import Wiki
w = Wiki("https://mysingingmonsters.fandom.com")
chars = int(os.environ.get("CHARS", "3000"))
skip = int(os.environ.get("SKIP", "0"))
pages = [p for p in os.environ.get("PAGES", "").split("|") if p]
for t, txt in w.wikitexts(pages).items():
    print("=" * 30, "PAGE", t, "len", len(txt))
    print(txt[skip:skip + chars])
for c in [x for x in os.environ.get("CATS", "").split("|") if x]:
    print("MEMBERS", c, w.category_members(c, recurse=0)[:60])
