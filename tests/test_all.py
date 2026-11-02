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
        self.sent, self.edited, self.answered = [], [], []

    def send(self, chat, text, markup=None):
        self.sent.append((chat, text, markup))

    def edit(self, chat, mid, text, markup=None):
        self.edited.append((chat, mid, text, markup))

    def answer(self, cid, text=""):
        self.answered.append(cid)

    def set_commands(self, c):
        pass


class BotTests(unittest.TestCase):
    def make(self, events, shop=()):
        d = tempfile.mkdtemp()
        self.tg = FakeTG()
        self.state = State(Path(d) / "s.bin", key="")
        return Bot(self.tg, self.state, ds_with(events, shop), clock=lambda: NOW)

    def msg(self, bot, text, chat=1):
        bot.handle_update({"update_id": 1, "message": {"chat": {"id": chat}, "text": text}})
        return self.tg.sent[-1][1]

    def test_commands(self):
        bot = self.make([ev("a", "Halloween", "2026-10-25T00:00:00Z", "2026-11-05T00:00:00Z"),
                         ev("b", "Yuletide", "2026-11-20T00:00:00Z", "2026-12-01T00:00:00Z"),
                         ev("c", "Old", "2025-01-01T00:00:00Z", "2025-01-05T00:00:00Z")])
        self.assertIn("Halloween", self.msg(bot, "/now"))
        self.assertNotIn("Yuletide", self.msg(bot, "/now"))
        self.assertIn("Yuletide", self.msg(bot, "/soon"))
        self.assertIn("Что это значит", self.msg(bot, "/event yule"))
        self.assertIn("Halloween", self.msg(bot, "halloween"))  # свободный текст
        self.assertIn("Europe/Kyiv", self.msg(bot, "/tz Europe/Kyiv"))
        self.assertIn("Неизвестный", self.msg(bot, "/tz Mars/Base"))

    def test_settings_toggle(self):
        bot = self.make([])
        self.msg(bot, "/settings")
        bot.handle_update({"update_id": 2, "callback_query": {
            "id": "q", "data": "set:shop", "message": {"chat": {"id": 1}, "message_id": 9}}})
        self.assertFalse(self.state.user(1)["subs"]["shop"])
        self.assertTrue(self.tg.edited)

    def test_notifications_dedupe_and_filters(self):
        events = [ev("a", "Halloween", "2026-11-02T00:00:00Z", "2026-11-02T20:00:00Z"),   # начался 12 ч назад → поздно для «start»
                  ev("n", "Fresh", "2026-11-02T10:30:00Z", "2026-11-03T10:30:00Z"),       # только что начался, конец в пределах 24 ч
                  ev("p", "Predicted", "2026-11-02T10:30:00Z", "2026-11-03T10:30:00Z", status="predicted")]
        shop = [{"monster": "Dermit", "start": "2026-11-02T10:30:00Z", "end": "2026-11-09T00:00:00Z",
                 "status": "confirmed", "event": "Fresh"}]
        bot = self.make(events, shop)
        bot.state.user(1)["since"] = NOW.timestamp() - 7200
        n1 = bot.send_notifications()
        texts = " ".join(t for _, t, _ in self.tg.sent)
        self.assertIn("Fresh", texts)
        self.assertNotIn("Predicted", texts)
        self.assertNotIn("Событие началось</b>\n🎄 <b>Halloween", texts)
        self.assertIn("Dermit", texts)
        self.assertEqual(bot.send_notifications(), 0)  # повторно не шлём
        self.assertGreater(n1, 0)

    def test_new_announce_and_late_subscriber(self):
        e = ev("x", "Future", "2026-12-01T00:00:00Z", "2026-12-05T00:00:00Z", first_seen="2026-11-02T11:00:00Z")
        bot = self.make([e])
        bot.state.user(1)
        self.assertEqual(bot.send_notifications(), 1)
        self.assertIn("Новый анонс", self.tg.sent[-1][1])

    def test_first_run_baseline_has_no_new(self):
        events = [ev("x", "Future", "2026-12-01T00:00:00Z", "2026-12-05T00:00:00Z")]
        pipeline.merge_first_seen(events, None, "2026-11-02T12:00:00Z")
        self.assertEqual(events[0]["first_seen"], "1970-01-01T00:00:00Z")
        pipeline.merge_first_seen(events, {"events": []}, "2026-11-02T12:00:00Z")


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
        self.assertEqual(render.fmt_range({"start": "2026-12-01T00:00:00Z", "end": "2026-12-05T00:00:00Z"}),
                         "1 декабря – 4 декабря")
        self.assertEqual(render.fmt_range({"start": "2026-12-01T00:00:00Z", "end": "2026-12-02T00:00:00Z"}), "1 декабря")


if __name__ == "__main__":
    unittest.main()
