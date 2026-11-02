"""Проверка внешних источников анонсов с раннера Actions."""
import json, re, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}

def get(name, url, n=600, headers=None):
    print("=" * 20, name, url)
    try:
        req = urllib.request.Request(url, headers={**UA, **(headers or {})})
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
            print("STATUS", r.status, r.headers.get("content-type"), "len", len(body))
            print(body[:n].replace("\n", " "))
            return body
    except Exception as e:
        print("FAIL", repr(e)[:200])

get("reddit.json", "https://www.reddit.com/r/MySingingMonsters/new.json?limit=5")
get("reddit.rss", "https://www.reddit.com/r/MySingingMonsters/new/.rss?limit=5")
get("reddit.api", "https://api.reddit.com/r/MySingingMonsters/new?limit=3")
b = get("itunes.search", "https://itunes.apple.com/search?term=my+singing+monsters&entity=software&limit=3&country=us", 1500)
get("bsky.search", "https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors?q=my%20singing%20monsters&limit=5", 1200)
for h in ("@MySingingMonsters", "@MySingingMonstersOfficial", "@mysingingmonsters"):
    body = get("youtube " + h, f"https://www.youtube.com/{h}", 200)
    if body:
        print("CHANNEL IDS", set(re.findall(r'"channelId":"(UC[\w-]{22})"', body))[:5] if False else list(set(re.findall(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', body)))[:5])
get("fandom.recentchanges", "https://mysingingmonsters.fandom.com/api.php?action=query&list=recentchanges&rcnamespace=0&rclimit=25&rcprop=title|timestamp|comment&format=json", 2500)
get("fandom.search.upcoming", "https://mysingingmonsters.fandom.com/api.php?action=query&list=search&srsearch=upcoming+OR+%22sneak+peek%22+OR+roadmap&srlimit=10&format=json", 1500)
get("fandom.blogs", "https://mysingingmonsters.fandom.com/api.php?action=query&list=allpages&apnamespace=500&aplimit=15&format=json", 1500)
get("fandom.news", "https://mysingingmonsters.fandom.com/api.php?action=query&list=search&srsearch=news+update+announcement&srlimit=10&format=json", 1500)
get("official.site", "https://www.mysingingmonsters.com/", 800)
get("bbb.news", "https://www.bigbluebubble.com/news", 800)
get("bbb.rss", "https://www.bigbluebubble.com/feed", 500)
get("gplay", "https://play.google.com/store/apps/details?id=com.bigbluebubble.singingmonsters&hl=en&gl=us", 300)
get("steam.search", "https://store.steampowered.com/api/storesearch/?term=singing+monsters&cc=us&l=en", 800)
get("twitter.syndication", "https://syndication.twitter.com/srv/timeline-profile/screen-name/SingingMonsters", 400)
