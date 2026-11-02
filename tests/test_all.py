"""Тесты на СИНТЕТИЧЕСКИХ фикстурах (в песочнице нет доступа к реальной вики).

Реальную разметку проверяет `python -m msm.cli probe` на машине с доступом в сеть.
"""
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from msm import extract, pipeline, predict, render, significance
from msm.bot import Bot
from msm.dates import find_dates, parse_iso
from msm.state import State
from msm.wikitext import clean, lead_paragraph, links, parse_tables, parse_templates

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)

PAGE = """{{Infobox event
|name = Yuletide
|start = December 15, 2025
|end = January 4, 2026
}}
'''Yuletide''' is a [[Seasonal event]] with new [[Dermit]] monsters and [[Costume]]s. Earn rewards and double diamonds!

== 2024 ==
The event ran from December 16 - January 5 with a [[Rare Wubbox|special]] [[File:x.png|thumb|[[Dermit]]]] offer.

== 2023 ==
It ran 17 - 31 December. Not a date: version 4.2.
"""


class Dates(unittest.TestCase):
    def test_ranges(self):
        r = find_dates("from October 25 - November 3, 2025", 2020)
        self.assertEqual((r[0].start, r[0].end), (date(2025, 10, 25), date(2025, 11, 3)))
        r = find_dates("December 16 - January 5", 2024)
        self.assertEqual((r[0].start, r[0].end), (date(2024, 12, 16), date(2025, 1, 5)))
        r = find_dates("17 - 31 December", 2023)
        self.assertEqual((r[0].start, r[0].end), (date(2023, 12, 17), date(2023, 12, 31)))
        r = find_dates("25 October to 3 November 2025", 2020)
        self.assertEqual((r[0].start, r[0].end), (date(2025, 10, 25), date(2025, 11, 3)))
        r = find_dates("2025-10-25 to 2025-11-03", 2020)
        self.assertEqual(r[0].end, date(2025, 11, 3))

    def test_single_and_invalid(self):
        self.assertEqual(find_dates("Released on March 4, 2013.", 2020)[0].start, date(2013, 3, 4))
        self.assertEqual(find_dates("February 31 - March 2", 2020, include_single=False), [])
        self.assertEqual(find_dates("no dates, just Marching on", 2020), [])


class Wikitext(unittest.TestCase):
    def test_templates_nested(self):
        t = parse_templates("{{Infobox|a=[[x|y]]|b={{inner|z}}|pos}}")
        self.assertEqual(t[0].params["b"], "{{inner|z}}")
        self.assertEqual(t[0].params["1"], "pos")

    def test_clean_and_links(self):
        c = clean("'''Hi''' [[A|b]] [[File:f.png|[[B]] cap]] {{tpl}}<ref>r</ref>")
        self.assertEqual(c.strip(), "Hi b")
        self.assertEqual(links("[[A|b]] [[File:f.png|x]] [[Category:C]] [[D#s]]"), ["A", "D"])

    def test_tables(self):
        t = parse_tables("{|\n! Name !! Dates\n|-\n| [[Xmas]] || Dec 1 - Dec 5, 2025\n|-\n| style=\"x\" | Y || z\n|}")[0]
        self.assertEqual(t.headers, ["Name", "Dates"])
        self.assertEqual(t.rows[0][0], "[[Xmas]]")
        self.assertEqual(t.rows[1][0], "Y")


class Extract(unittest.TestCase):
    def setUp(self):
        self.events = extract.extract_events("Yuletide", PAGE, today=NOW.date(), page_url="u",
                                             monster_titles=frozenset({"Dermit", "Rare Wubbox"}))

    def test_events_found(self):
        starts = sorted(e["start"][:10] for e in self.events)
        self.assertEqual(starts, ["2023-12-17", "2024-12-16", "2025-12-15"])
        e = next(e for e in self.events if e["start"].startswith("2025"))
        self.assertEqual(e["end"][:10], "2026-01-05")  # конец включительно → эксклюзивный +1 день
        self.assertEqual(e["kind"], "seasonal")
        self.assertIn("diamonds", e["tags"])
        self.assertIn("costumes", e["tags"])
        self.assertEqual(e["monsters"], ["Dermit", "Rare Wubbox"])
        self.assertTrue(e["description"].startswith("Yuletide is a"))

    def test_predict_next_year_only_when_missing(self):
        for e in self.events:
            e["status"] = "confirmed"
        pred = predict.predict(self.events, date(2026, 1, 10))
        # 2026 нет в подтверждённых → прогноз на конец 2026
        self.assertEqual([p["start"][:4] for p in pred], ["2026"])
        self.assertEqual(pred[0]["status"], "predicted")
        self.assertGreaterEqual(pred[0]["confidence"], 0.5)

    def test_tables(self):
        wt = "{|\n! Event !! Dates\n|-\n| [[Halloween Fest]] || October 20 - November 1, 2025 new monsters\n|}"
        ev = extract.events_from_tables("Events", wt, today=NOW.date(), base_url="b")
        self.assertEqual(ev[0]["title"], "Halloween Fest")
        self.assertEqual(ev[0]["end"][:10], "2025-11-02")

    def test_monster_windows(self):
        w = extract.monster_windows("Dermit", "== 2025 ==\nDermit is available in the market from December 15 - January 4.", today=NOW.date())
        self.assertEqual(w[0]["start"][:10], "2025-12-15")


class Significance(unittest.TestCase):
    def test_classify(self):
        k, tags = significance.analyze("Wublin Island", "Celestial and relics with a sale")
        self.assertEqual(k, "wublin")
        self.assertIn("relics", tags)
        self.assertTrue(significance.meaning(k, tags))


def ds_with(events, shop=()):
    return {"generated_at": "2026-11-02T10:00:00Z", "wiki": "w", "events": events, "shop": list(shop), "stats": {}}


def ev(id_, title, start, end, **kw):
    base = {"id": id_, "series": id_, "title": title, "start": start, "end": end, "status": "confirmed",
            "kind": "seasonal", "tags": [], "description": "d", "monsters": [], "first_seen": "1970-01-01T00:00:00Z"}
    base.update(kw)
    return base


class FakeTG:
    def __init__(self):
        self.sent, self.photos, self.edited, self.answered, self.markups = [], [], [], [], []
        self.photo_fails = False

    def send(self, chat, text, markup=None):
        self.sent.append((chat, text, markup))

    def send_photo(self, chat, photo, caption, markup=None):
        if self.photo_fails:
            raise RuntimeError("bad image")
        self.photos.append((chat, photo, caption, markup))

    def edit(self, chat, mid, text, markup=None):
        self.edited.append((chat, mid, text, markup))

    def edit_markup(self, chat, mid, markup):
        self.markups.append((chat, mid, markup))

    def answer(self, cid, text=""):
        self.answered.append((cid, text))

    def set_commands(self, c):
        pass


def cb(bot, data, chat=1, mid=9):
    bot.handle_update({"update_id": 5, "callback_query": {
        "id": "q", "data": data, "message": {"chat": {"id": chat}, "message_id": mid}}})


class BotTests(unittest.TestCase):
    def make(self, events, shop=()):
        d = tempfile.mkdtemp()
        self.tg = FakeTG()
        self.state = State(Path(d) / "s.bin", key="")
        return Bot(self.tg, self.state, ds_with(events, shop), clock=lambda: NOW)

    def msg(self, bot, text, chat=1):
        bot.handle_update({"update_id": 1, "message": {"chat": {"id": chat}, "text": text}})
        return self.tg.sent[-1]

    EVENTS = [ev("a", "Halloween", "2026-10-25T00:00:00Z", "2026-11-05T00:00:00Z", kind="seasonal"),
              ev("b", "Yuletide", "2026-11-20T00:00:00Z", "2026-12-01T00:00:00Z", monsters=["Yool"]),
              ev("p", "Flash (Promotion)", "2026-11-04T00:00:00Z", "2026-11-08T00:00:00Z", kind="promotion"),
              ev("c", "Old", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z"),
              ev("x", "Predicted Fest", "2026-12-10T00:00:00Z", "2026-12-20T00:00:00Z", status="predicted", confidence=0.8)]

    def test_start_has_keyboard_and_menu(self):
        bot = self.make(self.EVENTS)
        bot.handle_update({"update_id": 1, "message": {"chat": {"id": 1}, "text": "/start"}})
        self.assertIn("keyboard", self.tg.sent[0][2])
        self.assertIn("inline_keyboard", self.tg.sent[1][2])

    def test_day_precision_no_hours(self):
        bot = self.make(self.EVENTS)
        text = self.msg(bot, "/soon")[1]
        self.assertIn("Flash", text)
        self.assertIn("через 2 дня", text)   # 4 ноября, сегодня 2 ноября
        import re
        self.assertIsNone(re.search(r"\d+ ч\b", text))  # никаких «4 д 4 ч»
        self.assertNotIn("Predicted Fest", text)  # прогнозы отдельно

    def test_now_pred_and_filters(self):
        bot = self.make(self.EVENTS)
        self.assertIn("Halloween", self.msg(bot, "/now")[1])
        self.assertIn("Predicted Fest", self.msg(bot, "/pred")[1])
        cb(bot, "l:soon:promo:0")
        self.assertIn("Flash", self.tg.edited[-1][2])
        cb(bot, "l:soon:season:0")
        self.assertNotIn("Flash", self.tg.edited[-1][2])

    def test_card_photo_and_fallback(self):
        events = [dict(e_) for e_ in self.EVENTS]
        events[0]["image"] = "https://img/x.png"
        bot = self.make(events)
        bot.handle_update({"update_id": 1, "message": {"chat": {"id": 1}, "text": "/event halloween"}})
        self.assertEqual(len(self.tg.photos), 1)
        self.assertLessEqual(len(self.tg.photos[0][2]), 1024)
        self.tg.photo_fails = True
        self.msg(bot, "halloween")  # свободный текст + картинка недоступна → текстом
        self.assertIn("Halloween", self.tg.sent[-1][1])

    def test_open_card_by_button_and_reminder(self):
        bot = self.make(self.EVENTS)
        h = next(iter(h_ for h_, e_ in bot.by_hash.items() if e_["id"] == "b"))
        cb(bot, f"e:{h}")
        self.assertIn("Yuletide", self.tg.sent[-1][1])
        cb(bot, f"r:{h}")
        self.assertIn("b", self.state.user(1)["remind"])
        cb(bot, f"r:{h}")
        self.assertNotIn("b", self.state.user(1)["remind"])

    def test_settings_and_tz(self):
        bot = self.make([])
        cb(bot, "t:shop")
        self.assertFalse(self.state.user(1)["subs"]["shop"])
        cb(bot, "z:0")
        self.assertEqual(self.state.user(1)["tz"], "Europe/Kyiv")
        self.assertIn("Europe/Paris", self.msg(bot, "/tz Europe/Paris")[1])
        self.assertIn("Неизвестный", self.msg(bot, "/tz Mars/Base")[1])

    def test_reminder_notification_once(self):
        bot = self.make(self.EVENTS)
        bot.state.user(1)["since"] = NOW.timestamp()
        bot.state.user(1)["remind"] = ["b"]
        bot.state.user(1)["subs"] = {k: False for k in ("new", "start", "ending", "shop")}
        self.assertEqual(bot.send_notifications(), 0)  # до старта > 24 ч
        later = Bot(self.tg, self.state, ds_with(self.EVENTS), clock=lambda: datetime(2026, 11, 19, 12, tzinfo=timezone.utc))
        self.assertEqual(later.send_notifications(), 1)
        self.assertIn("Напоминание", self.tg.sent[-1][1])
        self.assertEqual(later.send_notifications(), 0)

    def test_notifications_dedupe_and_filters(self):
        events = [ev("a", "Halloween", "2026-10-01T00:00:00Z", "2026-11-30T00:00:00Z"),
                  ev("n", "Fresh", "2026-11-02T00:00:00Z", "2026-11-03T12:00:00Z"),
                  ev("p", "Predicted", "2026-11-02T00:00:00Z", "2026-11-03T12:00:00Z", status="predicted")]
        bot = self.make(events)
        bot.state.user(1)["since"] = NOW.timestamp() - 7200
        n1 = bot.send_notifications()
        texts = " ".join(t for _, t, _ in self.tg.sent)
        self.assertIn("Fresh", texts)
        self.assertNotIn("Predicted", texts)
        self.assertNotIn("Halloween", texts)  # давно идёт — не «началось»
        self.assertGreater(n1, 0)
        self.assertEqual(bot.send_notifications(), 0)

    def test_new_announce(self):
        e = ev("x", "Future", "2026-12-01T00:00:00Z", "2026-12-05T00:00:00Z", first_seen="2026-11-02T11:00:00Z")
        bot = self.make([e])
        bot.state.user(1)
        self.assertEqual(bot.send_notifications(), 1)
        self.assertIn("Новый анонс", self.tg.sent[-1][1])

    def test_first_run_baseline_has_no_new(self):
        events = [ev("x", "Future", "2026-12-01T00:00:00Z", "2026-12-05T00:00:00Z")]
        pipeline.merge_first_seen(events, None, "2026-11-02T12:00:00Z")
        self.assertEqual(events[0]["first_seen"], "1970-01-01T00:00:00Z")


class StateAndPipeline(unittest.TestCase):
    def test_encrypted_state_roundtrip(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        p = Path(tempfile.mkdtemp()) / "s.bin"
        s = State(p, key=key)
        s.user(42)["tz"] = "Asia/Almaty"
        s.save()
        self.assertNotIn(b"Almaty", p.read_bytes())
        self.assertEqual(State(p, key=key).user(42)["tz"], "Asia/Almaty")

    def test_sanity_guard(self):
        ok, _ = pipeline.sanity_ok([], None)
        self.assertFalse(ok)
        prev = {"events": [{"status": "confirmed"}] * 20}
        self.assertFalse(pipeline.sanity_ok([{"status": "confirmed"}] * 5, prev)[0])
        self.assertTrue(pipeline.sanity_ok([{"status": "confirmed"}] * 15, prev)[0])

    def test_build_shop_dedup(self):
        e = ev("a", "X", "2026-12-01T00:00:00Z", "2026-12-05T00:00:00Z", monsters=["M"])
        w = [{"monster": "M", "start": e["start"], "end": e["end"], "source": "s"}]
        self.assertEqual(len(pipeline.build_shop([e], w)), 1)


class Render(unittest.TestCase):
    def test_delta_and_range(self):
        from datetime import timedelta
        self.assertEqual(render.fmt_delta(timedelta(days=2, hours=3)), "2 д 3 ч")
        self.assertEqual(render.fmt_range({"start": "2026-12-01T00:00:00Z", "end": "2026-12-05T00:00:00Z"}), "1–4 декабря")
        self.assertEqual(render.fmt_range({"start": "2026-12-01T00:00:00Z", "end": "2026-12-02T00:00:00Z"}), "1 декабря")
        self.assertEqual(render.fmt_range({"start": "2026-10-28T00:00:00Z", "end": "2026-11-04T00:00:00Z"}), "28 октября – 3 ноября")

    def test_when_days_only(self):
        e = {"start": "2026-11-07T00:00:00Z", "end": "2026-11-12T00:00:00Z"}
        self.assertEqual(render.when(e, NOW, "UTC"), "через 5 дней")
        self.assertEqual(render.when(e, datetime(2026, 11, 6, 20, tzinfo=timezone.utc), "Europe/Moscow"), "стартует завтра")
        self.assertIn("идёт", render.when(e, datetime(2026, 11, 6, 22, tzinfo=timezone.utc), "Europe/Moscow"))  # в Москве уже 7-е
        self.assertEqual(render.when(e, datetime(2026, 11, 11, 5, tzinfo=timezone.utc), "UTC"), "последний день — сегодня")
        self.assertIn("ещё 3 дня", render.when(e, datetime(2026, 11, 8, 5, tzinfo=timezone.utc), "UTC"))

    def test_card_fits_caption_limit(self):
        e = {"id": "i", "series": "s", "title": "T" * 40, "kind": "seasonal", "tags": ["monsters", "costumes", "diamonds", "rewards"],
             "description": "D" * 600, "monsters": [f"Monster {i}" for i in range(40)], "parent": "Parent 2026",
             "start": "2026-11-01T00:00:00Z", "end": "2026-11-10T00:00:00Z", "status": "confirmed"}
        card = render.event_card(e, NOW, "UTC", limit=1000, history=["2024", "2025"])
        self.assertLessEqual(render.visible_len(card), 1000)
        self.assertIn("▰", card)  # идёт — есть прогресс-бар


if __name__ == "__main__":
    unittest.main()


# Фрагмент РЕАЛЬНОЙ разметки Availability/Seasonal (скопирован из лога Actions 2026-11-02)
REAL_AVAIL = """==Availability==
===2026===
<div class="mw-collapsible mw-collapsed custom-button">
{|class="article-table" style="white-space: nowrap"
!From
!To
!Monster(s)
!Occasion
|-
|February 4th
|February 23rd
|[[Schmoochle]]
|[[Season of Love]] 2026
|-
|February 11th
|February 16th
|[[Rare Schmoochle]]<br>[[Epic Schmoochle]]
|'4 Heads Are Better Than 2' Promotion<br>([[Season of Love]] 2026)
|-
|January 15th
|January 19th
|[[Hoola]]<br>[[Rare Hoola]]<br>[[Epic Hoola]]
|Out-of-Season
|-
|December 11th
|January 2nd
|[[Dermit]]
|New Rare Introduction<br>([[Rare Dermit]])
|}
</div>
===2025===
{|class="article-table"
!From
!To
!Monster(s)
!Occasion
|-
|February 5th
|February 24th
|[[Schmoochle]]
|[[Season of Love]] 2025
|}
==Vessels==
{|class="article-table mw-collapsible"
|+
!Month
!Available Vessels
!Occasion
|-
|January
|{{PortraitList|Phangler|Wynq}}
|Quintessence
|}
"""


class Availability(unittest.TestCase):
    def setUp(self):
        self.rows = extract.availability_rows(REAL_AVAIL, today=NOW.date())
        self.events = extract.events_from_availability(self.rows, base_url="https://w", source="Availability/Seasonal")

    def test_rows(self):
        self.assertEqual(len(self.rows), 5)  # таблица «Month» пропущена
        r = self.rows[0]
        self.assertEqual((r["start"], r["end"]), (date(2026, 2, 4), date(2026, 2, 23)))
        # переход через год: December 11th → January 2nd
        wrap = next(r for r in self.rows if r["monsters"] == ["Dermit"])
        self.assertEqual((wrap["start"], wrap["end"]), (date(2026, 12, 11), date(2027, 1, 2)))

    def test_events(self):
        by = {e["title"]: e for e in self.events}
        self.assertEqual(by["Season of Love"]["kind"], "seasonal")
        promo = by["4 Heads Are Better Than 2 (Promotion)"]
        self.assertEqual(promo["kind"], "promotion")
        self.assertEqual(promo["monsters"], ["Rare Schmoochle", "Epic Schmoochle"])
        self.assertEqual(by["Out-of-Season: Hoola"]["kind"], "outofseason")
        self.assertIn("monsters", promo["tags"])

    def test_prediction_for_recurring_only(self):
        pred = predict.predict(self.events, date(2026, 11, 2))
        titles = {p["title"] for p in pred}
        self.assertIn("Season of Love", titles)       # было в 2025 и 2026 → прогноз на 2027
        self.assertNotIn("Out-of-Season: Hoola", titles)  # единственный год → не прогнозируем
        sol = next(p for p in pred if p["title"] == "Season of Love")
        self.assertEqual(sol["start"][:4], "2027")
