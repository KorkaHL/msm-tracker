"""Логика бота: экраны, inline-навигация, карточки с картинками, уведомления, напоминания."""
from __future__ import annotations

import difflib
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from . import render
from .dates import parse_iso
from .state import State
from .telegram import Telegram, TelegramError

log = logging.getLogger("msm.bot")

COMMANDS = [
    ("menu", "Главное меню"),
    ("now", "Что идёт сейчас"),
    ("soon", "Ближайшие события"),
    ("pred", "Прогноз по прошлым годам"),
    ("shop", "Монстры: когда доступны"),
    ("event", "Найти событие: /event название"),
    ("settings", "Уведомления и часовой пояс"),
    ("about", "О боте и данных"),
]

# кнопки постоянной клавиатуры → экраны
KEYBOARD_LABELS = {"🟢 Сейчас": "now", "🗓 Скоро": "soon", "🐾 Монстры": "shop", "🔮 Прогноз": "pred",
                   "🔔 Настройки": "settings", "🏠 Меню": "menu"}
REPLY_KEYBOARD = {"keyboard": [["🟢 Сейчас", "🗓 Скоро"], ["🐾 Монстры", "🔮 Прогноз"], ["🔔 Настройки", "🏠 Меню"]],
                  "resize_keyboard": True, "is_persistent": True}

SUB_LABELS = {"new": "🆕 Новые анонсы", "start": "▶️ Старт событий", "ending": "⏰ Конец через 24 ч",
              "shop": "🐾 Акции и монстры"}
FILTERS = [("all", "Все"), ("season", "🎄 Сезон"), ("promo", "🏷️ Акции"), ("off", "🔁 Вне сезона"), ("other", "🌟 Прочее")]
FILTER_KINDS = {"season": {"seasonal", "anniversary"}, "promo": {"promotion"}, "off": {"outofseason"}}
TIMEZONES = [("Киев", "Europe/Kyiv"), ("Москва", "Europe/Moscow"), ("Минск", "Europe/Minsk"), ("Алматы", "Asia/Almaty"),
             ("Ташкент", "Asia/Tashkent"), ("Екатеринбург", "Asia/Yekaterinburg"), ("Новосибирск", "Asia/Novosibirsk"),
             ("Владивосток", "Asia/Vladivostok"), ("Варшава", "Europe/Warsaw"), ("Берлин", "Europe/Berlin"),
             ("Лондон", "Europe/London"), ("Нью-Йорк", "America/New_York"), ("UTC", "UTC")]
PAGE = 5
START_WINDOW = timedelta(hours=18)  # «началось» шлём, пока событие свежее
ENDING_WINDOW = timedelta(hours=24)
NEW_WINDOW = timedelta(hours=72)
REMIND_BEFORE = timedelta(hours=24)

Screen = tuple[str, dict | None]


def _h(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


class Bot:
    def __init__(self, tg: Telegram, state: State, dataset: dict, *, clock=None):
        self.tg, self.state, self.ds = tg, state, dataset
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.by_hash = {_h(ev["id"]): ev for ev in dataset.get("events", [])}

    # ---------- данные ----------
    @property
    def events(self) -> list[dict]:
        return self.ds.get("events", [])

    def _tz(self, chat_id: int) -> str:
        return (self.state.user(chat_id) or {}).get("tz", "UTC")

    def history(self, ev: dict) -> list[str]:
        years = sorted({x["start"][:4] for x in self.events if x["series"] == ev["series"] and x["status"] == "confirmed"})
        return years[-6:] if len(years) > 1 else []

    def find_event(self, query: str, now: datetime, tz: str = "UTC") -> dict | None:
        q = query.lower().strip()
        pool: dict[str, list[dict]] = {}
        for ev in self.events:
            pool.setdefault(ev["title"], []).append(ev)
        names = list(pool)
        hit = [n for n in names if q in n.lower()] or difflib.get_close_matches(query, names, 1, 0.5)
        if not hit:
            return None
        occ = pool[hit[0]]
        future = [x for x in occ if render.status_of(x, now, tz) != "past"]
        return min(future, key=lambda x: x["start"]) if future else max(occ, key=lambda x: x["start"])

    # ---------- списки ----------
    def _items(self, screen: str, now: datetime, tz: str) -> list[dict]:
        td = render.today_in(now, tz)
        out = []
        for ev in self.events:
            st = render.status_of(ev, now, tz)
            s, _ = render.span_dates(ev)
            predicted = ev.get("status") == "predicted"
            if screen == "now" and st == "active" and not predicted:
                out.append(ev)
            elif screen == "soon" and st == "upcoming" and not predicted and (s - td).days <= 45:
                out.append(ev)
            elif screen == "pred" and predicted and st != "past" and (s - td).days <= 180:
                out.append(ev)
        key = (lambda x: x["end"]) if screen == "now" else (lambda x: x["start"])
        return sorted(out, key=key)

    @staticmethod
    def _passes(ev: dict, flt: str) -> bool:
        if flt == "all":
            return True
        kind = ev.get("kind", "other")
        if flt == "other":
            return not any(kind in ks for ks in FILTER_KINDS.values())
        return kind in FILTER_KINDS.get(flt, set())

    TITLES = {"now": "🟢 Идёт сейчас", "soon": "🗓 Ближайшие события", "pred": "🔮 Прогноз по прошлым годам"}
    EMPTY = {"now": "Сейчас ничего не идёт. Загляни в «Скоро» 🗓", "soon": "В вики пока нет анонсов на ближайшие 45 дней.\nЗагляни в 🔮 Прогноз — там даты по прошлым годам.",
             "pred": "Прогнозов на ближайшие полгода нет."}

    def screen_list(self, chat_id: int, screen: str, flt: str = "all", page: int = 0) -> Screen:
        now, tz = self.clock(), self._tz(chat_id)
        items = [x for x in self._items(screen, now, tz) if self._passes(x, flt)]
        pages = max(1, -(-len(items) // PAGE))
        page = max(0, min(page, pages - 1))
        chunk = items[page * PAGE:(page + 1) * PAGE]
        head = f"<b>{self.TITLES[screen]}</b>  ·  {len(items)}"
        if screen == "pred":
            head += "\n<i>Это статистика прошлых лет, а не официальный анонс.</i>"
        if chunk:
            text = render.paginate_lines([render.event_list_item(x, now, tz, i + 1) for i, x in enumerate(chunk)], head)
        else:
            text = f"{head}\n\n{self.EMPTY[screen]}"
        rows = [[_btn(("• " if k == flt else "") + label, f"l:{screen}:{k}:0") for k, label in FILTERS[:3]],
                [_btn(("• " if k == flt else "") + label, f"l:{screen}:{k}:0") for k, label in FILTERS[3:]]]
        if chunk:
            rows.append([_btn(f"{i + 1}", f"e:{_h(x['id'])}") for i, x in enumerate(chunk)])
        if pages > 1:
            rows.append([_btn("◀️", f"l:{screen}:{flt}:{page - 1}"), _btn(f"{page + 1}/{pages}", "noop"),
                         _btn("▶️", f"l:{screen}:{flt}:{page + 1}")])
        rows.append([_btn("🏠 Меню", "m")])
        return text, {"inline_keyboard": rows}

    def screen_shop(self, chat_id: int, page: int = 0) -> Screen:
        now, tz = self.clock(), self._tz(chat_id)
        td = render.today_in(now, tz)
        best: dict[str, dict] = {}
        for r in self.ds.get("shop", []):
            st = render.status_of(r, now, tz)
            s, _ = render.span_dates(r)
            if st == "past" or (s - td).days > 45 or r["monster"].lower().startswith(("all ", "rares", "epics")):
                continue
            cur = best.get(r["monster"])
            rank = (st != "active", r.get("status") == "predicted", r["start"])
            if cur is None or rank < cur[0]:
                best[r["monster"]] = (rank, r)
        rows_all = [v[1] for v in sorted(best.values(), key=lambda v: v[0])]
        pages = max(1, -(-len(rows_all) // 6))
        page = max(0, min(page, pages - 1))
        chunk = rows_all[page * 6:(page + 1) * 6]
        head = f"<b>🐾 Монстры: доступность</b>  ·  {len(rows_all)}\n<i>🟢 доступен сейчас · 🟡 скоро · 🔮 прогноз</i>"
        text = render.paginate_lines([render.shop_item(r, now, tz) for r in chunk], head) if chunk else \
            head + "\n\nДанных о доступности на ближайшие 45 дней нет."
        kb = []
        if pages > 1:
            kb.append([_btn("◀️", f"s:{page - 1}"), _btn(f"{page + 1}/{pages}", "noop"), _btn("▶️", f"s:{page + 1}")])
        kb.append([_btn("🏠 Меню", "m")])
        return text, {"inline_keyboard": kb}

    # ---------- прочие экраны ----------
    def screen_menu(self, chat_id: int) -> Screen:
        now, tz = self.clock(), self._tz(chat_id)
        n_now = len(self._items("now", now, tz))
        n_soon = len(self._items("soon", now, tz))
        text = (f"<b>🎵 MSM Tracker</b>\n{render.LINE}\n"
                f"🟢 Сейчас идёт: <b>{n_now}</b>\n🗓 Скоро (45 дней): <b>{n_soon}</b>\n\n"
                "Выбирай раздел кнопками ниже 👇")
        kb = [[_btn("🟢 Сейчас", "l:now:all:0"), _btn("🗓 Скоро", "l:soon:all:0")],
              [_btn("🐾 Монстры", "s:0"), _btn("🔮 Прогноз", "l:pred:all:0")],
              [_btn("🔔 Уведомления", "st"), _btn("🌍 Часовой пояс", "z")],
              [_btn("ℹ️ О боте", "a")]]
        return text, {"inline_keyboard": kb}

    def screen_settings(self, chat_id: int) -> Screen:
        u = self.state.user(chat_id)
        subs, tz = u["subs"], u.get("tz", "UTC")
        text = (f"<b>🔔 Уведомления</b>\n{render.LINE}\nНажми на пункт, чтобы включить или выключить.\n\n"
                f"🌍 Часовой пояс: <code>{escape(tz)}</code>\n⏰ Напоминаний: <b>{len(u.get('remind', []))}</b>\n\n"
                "<i>Напоминание ставится кнопкой в карточке события.</i>")
        kb = [[_btn(f"{'✅' if subs.get(k, True) else '⬜'} {label}", f"t:{k}")] for k, label in SUB_LABELS.items()]
        kb.append([_btn("🌍 Часовой пояс", "z")])
        kb.append([_btn("🏠 Меню", "m")])
        return text, {"inline_keyboard": kb}

    def screen_tz(self, chat_id: int) -> Screen:
        cur = self._tz(chat_id)
        text = (f"<b>🌍 Часовой пояс</b>\n{render.LINE}\nСейчас: <code>{escape(cur)}</code>\n"
                "Он влияет на то, когда событие считается «сегодня». Любой другой: <code>/tz Europe/Paris</code>")
        kb, row = [], []
        for i, (name, zone) in enumerate(TIMEZONES):
            row.append(_btn(("✅ " if zone == cur else "") + name, f"z:{i}"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([_btn("◀️ Назад", "st")])
        return text, {"inline_keyboard": kb}

    def screen_about(self) -> Screen:
        now = self.clock()
        gen = self.ds.get("generated_at")
        age = render.fmt_delta(now - parse_iso(gen)) if gen else "?"
        st = self.ds.get("stats", {})
        text = (f"<b>ℹ️ О боте</b>\n{render.LINE}\n"
                f"Данные собираются автоматически из вики My Singing Monsters (таблицы Availability).\n\n"
                f"🔄 Обновлено {age} назад\n📚 Записей расписания: <b>{st.get('availability_rows', st.get('events', 0))}</b>\n"
                f"🎯 Событий в базе: <b>{st.get('events', 0)}</b>\n\n"
                "🔮 <b>Прогноз</b> — даты прошлых лет, перенесённые на следующий год. Не официальный анонс.\n"
                "📅 В вики указаны даты без времени старта, поэтому бот считает дни.")
        return text, {"inline_keyboard": [[_btn("🏠 Меню", "m")]]}

    # ---------- карточка события ----------
    def _card_markup(self, ev: dict, chat_id: int) -> dict:
        reminded = ev["id"] in (self.state.user(chat_id).get("remind") or [])
        rows = [[_btn("🔕 Убрать напоминание" if reminded else "⏰ Напомнить", f"r:{_h(ev['id'])}")]]
        if ev.get("url"):
            rows.append([{"text": "🔗 Страница в вики", "url": ev["url"]}])
        rows.append([_btn("🏠 Меню", "m")])
        return {"inline_keyboard": rows}

    def send_card(self, chat_id: int, ev: dict, header: str = "", markup: dict | None = None) -> None:
        now, tz = self.clock(), self._tz(chat_id)
        markup = markup or self._card_markup(ev, chat_id)
        hist = self.history(ev)
        prefix = f"{header}\n\n" if header else ""
        if ev.get("image"):
            try:
                cap = prefix + render.event_card(ev, now, tz, limit=1000 - render.visible_len(prefix), history=hist)
                self.tg.send_photo(chat_id, ev["image"], cap, markup)
                return
            except Exception as e:  # картинка недоступна/не подошла — шлём текстом
                log.info("photo failed (%s), fallback to text", e)
        self.tg.send(chat_id, prefix + render.event_card(ev, now, tz, history=hist), markup)

    # ---------- обработка апдейтов ----------
    def handle_update(self, upd: dict) -> None:
        try:
            if "callback_query" in upd:
                self._callback(upd["callback_query"])
            elif "message" in upd and "text" in upd["message"]:
                self._message(upd["message"])
        except TelegramError as e:
            log.warning("telegram error: %s", e)
            if e.code == 403:
                chat = (upd.get("message") or upd.get("callback_query", {}).get("message") or {}).get("chat", {})
                if chat.get("id"):
                    self.state.remove_user(chat["id"])
        except Exception:
            log.exception("handler failed for update %s", upd.get("update_id"))

    def _message(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        self.state.user(chat_id)
        if text in KEYBOARD_LABELS:
            text = "/" + KEYBOARD_LABELS[text]
        if not text.startswith("/"):
            text = "/event " + text  # свободный текст = поиск события
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd[1:].split("@")[0].lower(), arg.strip()
        if cmd in ("start", "help"):
            self.tg.send(chat_id, "<b>Привет! 🎵</b>\nЯ слежу за событиями, акциями и монстрами My Singing Monsters "
                                  "и пишу, когда что-то начинается.\n\n"
                                  "Кнопки внизу — быстрый доступ. Название события можно просто написать мне.",
                         REPLY_KEYBOARD)
            return self.tg.send(chat_id, *self.screen_menu(chat_id))
        if cmd == "menu":
            return self.tg.send(chat_id, *self.screen_menu(chat_id))
        if cmd in ("now", "soon", "pred"):
            return self.tg.send(chat_id, *self.screen_list(chat_id, cmd))
        if cmd == "shop":
            return self.tg.send(chat_id, *self.screen_shop(chat_id))
        if cmd == "settings":
            return self.tg.send(chat_id, *self.screen_settings(chat_id))
        if cmd == "about":
            return self.tg.send(chat_id, *self.screen_about())
        if cmd == "tz":
            return self._cmd_tz(chat_id, arg)
        if cmd in ("event", "monster"):
            if not arg:
                return self.tg.send(chat_id, "Напиши название: <code>/event Spooktacle</code>")
            ev = self.find_event(arg, self.clock(), self._tz(chat_id))
            if not ev:
                return self.tg.send(chat_id, "Не нашёл такое событие 🤔 Попробуй часть названия на английском, как в игре.")
            return self.send_card(chat_id, ev)
        self.tg.send(chat_id, "Не знаю такую команду. Открой меню: /menu")

    def _cmd_tz(self, chat_id: int, arg: str) -> None:
        if not arg:
            return self.tg.send(chat_id, *self.screen_tz(chat_id))
        try:
            ZoneInfo(arg)
        except Exception:
            return self.tg.send(chat_id, "Неизвестный пояс. Формат IANA, например: <code>Europe/Kyiv</code>")
        self.state.user(chat_id)["tz"] = arg
        self.tg.send(chat_id, f"✅ Часовой пояс: <code>{escape(arg)}</code>")

    # ---------- inline-кнопки ----------
    def _callback(self, cq: dict) -> None:
        chat_id = cq["message"]["chat"]["id"]
        mid = cq["message"]["message_id"]
        data = cq.get("data", "")
        self.state.user(chat_id)
        parts = data.split(":")
        head = parts[0]

        def show(screen: Screen, note: str = ""):
            try:
                self.tg.edit(chat_id, mid, *screen)
            except TelegramError as e:
                if "no text" in e.desc or "can't be edited" in e.desc:  # экран был фото — шлём новым
                    self.tg.send(chat_id, *screen)
                else:
                    raise
            self.tg.answer(cq["id"], note)

        if head == "m":
            show(self.screen_menu(chat_id))
        elif head == "l" and len(parts) == 4 and parts[1] in self.TITLES:
            show(self.screen_list(chat_id, parts[1], parts[2], int(parts[3])))
        elif head == "s":
            show(self.screen_shop(chat_id, int(parts[1]) if len(parts) > 1 else 0))
        elif head == "st":
            show(self.screen_settings(chat_id))
        elif head == "a":
            show(self.screen_about())
        elif head == "z" and len(parts) == 1:
            show(self.screen_tz(chat_id))
        elif head == "z" and len(parts) == 2 and parts[1].isdigit() and int(parts[1]) < len(TIMEZONES):
            self.state.user(chat_id)["tz"] = TIMEZONES[int(parts[1])][1]
            show(self.screen_tz(chat_id), "Сохранено")
        elif head == "t" and len(parts) == 2 and parts[1] in SUB_LABELS:
            subs = self.state.user(chat_id)["subs"]
            subs[parts[1]] = not subs.get(parts[1], True)
            show(self.screen_settings(chat_id), "Сохранено")
        elif head == "e" and len(parts) == 2 and parts[1] in self.by_hash:
            self.tg.answer(cq["id"])
            self.send_card(chat_id, self.by_hash[parts[1]])
        elif head == "r" and len(parts) == 2 and parts[1] in self.by_hash:
            ev = self.by_hash[parts[1]]
            rem = self.state.user(chat_id).setdefault("remind", [])
            if ev["id"] in rem:
                rem.remove(ev["id"])
                note = "Напоминание убрано"
            else:
                rem.append(ev["id"])
                del rem[:-50]
                note = "Напомню за день до старта ⏰"
            self.tg.edit_markup(chat_id, mid, self._card_markup(ev, chat_id))
            self.tg.answer(cq["id"], note)
        else:
            self.tg.answer(cq["id"])

    # ---------- уведомления ----------
    def due_notifications(self, now: datetime) -> list[tuple[str, str, str, dict]]:
        """→ [(kind, dedupe_key, header, event)] для подтверждённых событий."""
        out = []
        for ev in self.events:
            if ev.get("status") != "confirmed":
                continue
            s, en = parse_iso(ev["start"]), parse_iso(ev["end"])
            if parse_iso(ev.get("first_seen", "1970-01-01T00:00:00Z")) > now - NEW_WINDOW and s > now:
                out.append(("new", f"{ev['id']}:new", "🆕 <b>Новый анонс</b>", ev))
            if s <= now < min(en, s + START_WINDOW):
                # акции/«вне сезона»/новые монстры — тумблер «Акции и монстры», остальное — «Старт событий»
                k = "shop" if ev.get("kind") in ("promotion", "outofseason", "intro", "other") else "start"
                out.append((k, f"{ev['id']}:start", "▶️ <b>Началось!</b>", ev))
            if s <= now < en <= now + ENDING_WINDOW:
                out.append(("ending", f"{ev['id']}:ending", "⏰ <b>Скоро конец</b> — осталось меньше суток", ev))
        return out

    def send_notifications(self) -> int:
        now = self.clock()
        sent = 0
        due = self.due_notifications(now)
        by_id = {ev["id"]: ev for ev in self.events}
        for chat, user in list(self.state.d["users"].items()):
            cid = int(chat)
            todo = [(k, key, header, ev) for k, key, header, ev in due if user["subs"].get(k, True)]
            for rid in list(user.get("remind", [])):  # персональные напоминания
                ev = by_id.get(rid)
                if not ev:
                    continue
                s = parse_iso(ev["start"])
                if s - REMIND_BEFORE <= now < s + timedelta(hours=12):
                    todo.append(("remind", f"{rid}:remind", "⏰ <b>Напоминание</b>", ev))
            for kind, key, header, ev in todo:
                full = f"{chat}:{key}"
                if self.state.was_sent(full):
                    continue
                trig = parse_iso(ev["end"] if kind == "ending" else ev["start"]).timestamp()
                if kind not in ("new", "remind") and trig < user.get("since", 0) - 3600:
                    self.state.mark_sent(full)  # началось до подписки — молча пропускаем
                    continue
                try:
                    if kind == "ending":
                        self.tg.send(cid, f"{header}\n\n{render.event_list_item(ev, now, user.get('tz', 'UTC'))}",
                                     self._card_markup(ev, cid))
                    else:
                        self.send_card(cid, ev, header)
                    sent += 1
                except TelegramError as e:
                    if e.code == 403:
                        self.state.remove_user(cid)
                        break
                    log.warning("notify %s: %s", chat, e)
                    continue
                self.state.mark_sent(full)
        return sent

    # ---------- главный цикл ----------
    def run(self, duration: int, notify_every: int = 60, poll: int = 25) -> None:
        deadline = time.monotonic() + duration
        last_notify = 0.0
        try:
            self.tg.set_commands(COMMANDS)
        except Exception as e:
            log.warning("set_commands: %s", e)
        while True:
            left = deadline - time.monotonic()
            if left < 3:
                break
            try:
                updates = self.tg.get_updates(self.state.d["offset"], poll=int(min(poll, left - 2)))
            except Exception as e:
                log.warning("getUpdates: %s", e)
                time.sleep(3)
                continue
            for u in updates:
                self.state.d["offset"] = u["update_id"] + 1
                self.handle_update(u)
            if time.monotonic() - last_notify >= notify_every:
                self.send_notifications()
                last_notify = time.monotonic()
            if updates:
                self.state.save()
        self.state.save()
