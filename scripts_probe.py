"""Диагностика на раннере Actions. PAGES='A|B' (вики-тексты), URLS='name=>url=>chars=>skip;;...' (любые URL)."""
import os, urllib.request
from msm.wiki import Wiki

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

chars = int(os.environ.get("CHARS", "3000"))
skip = int(os.environ.get("SKIP", "0"))
pages = [p for p in os.environ.get("PAGES", "").split("|") if p]
if pages:
    w = Wiki("https://mysingingmonsters.fandom.com")
    for t, txt in w.wikitexts(pages).items():
        print("=" * 30, "PAGE", t, "len", len(txt))
        print(txt[skip:skip + chars])

for item in [x for x in os.environ.get("URLS", "").split(";;") if x.strip()]:
    parts = (item.strip().split("=>") + ["1500", "0"])[:4]
    name, url, n, sk = parts[0], parts[1], int(parts[2]), int(parts[3])
    print("=" * 20, name, url)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            print("STATUS", r.status, "len", len(body))
            print(body[sk:sk + n])
    except Exception as e:
        print("FAIL", repr(e)[:200])
