"""Логика бота: команды, inline-кнопки, уведомления."""
from __future__ import annotations

import difflib
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
    ("now", "Что идёт прямо сейчас"),
    ("soon", "Ближайшие события (30 дней)"),
    ("shop", "Какие монстры доступны и когда"),
    ("event", "Подробно о событии: /event название"),
    ("monster", "Когда был/будет доступен монстр"),
    ("settings", "Настройки уведомлений"),
    ("tz", "Часовой пояс: /tz Europe/Moscow"),
    ("about", "Источник данных и свежесть"),
    ("help", "Справка"),
]

HELP = (
    "🎵 <b>MSM Tracker</b> — расписание событий My Singing Monsters.\n\n"
    "/now — активные события и сколько осталось\n"
    "/soon — что стартует в ближайшие 30 дней (включая 🔮 прогнозы)\n"
    "/shop — окна доступности монстров\n"
    "/event &lt;название&gt; — описание и «что это значит для тебя»\n"
    "/monster &lt;имя&gt; — история и прогноз доступности\n"
    "/settings — какие уведомления получать\n"
    "/tz &lt;пояс&gt; — например /tz Europe/Kyiv\n"
    "/about — откуда данные\n\n"
    "Уведомления включены по умолчанию: новый анонс, старт, окончание за 24 ч, монстры в магазине."
)

SUB_LABELS = {"new": "🆕 Новые анонсы", "start": "▶️ Старт события", "ending": "⏰ Конец через 24 ч",
              "shop": "🐾 Монстры в магазине"}
NOTIFY_PAST_WINDOW = timedelta(hours=6)  # не слать «старт», если он давно прошёл
ENDING_WINDOW = timedelta(hours=24)
NEW_WINDOW = timedelta(hours=72)


class Bot:
    def __init__(self, tg: Telegram, state: State, dataset: dict, *, clock=None):
        self.tg, self.state, self.ds = tg, state, dataset
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ---------- данные ----------
    @property
    def events(self) -> list[dict]:
        return self.ds.get("events", [])

    def _tz(self, chat_id: int) -> str:
        return (self.state.user(chat_id) or {}).get("tz", "UTC")

    def find_event(self, query: str, now: datetime) -> dict | None:
        q = query.lower().strip()
        pool = {}
        for e in self.events:
            pool.setdefault(e["title"], []).append(e)
        names = list(pool)
        hit = [n for n in names if q in n.lower()] or difflib.get_close_matches(query, names, 1, 0.5)
        if not hit:
            return None
        occ = pool[hit[0]]
        # ближайшее по времени: активное/предстоящее, иначе последнее прошедшее
        future = [e for e in occ if parse_iso(e["end"]) > now]
        return min(future, key=lambda e: e["start"]) if future else max(occ, key=lambda e: e["start"])

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
        if not text.startswith("/"):
            text = "/event " + text  # свободный текст = поиск события
        cmd, _, arg = text.partition(" ")
        cmd = cmd[1:].split("@")[0].lower()
        arg = arg.strip()
        now = self.clock()
        self.state.user(chat_id)
        tz = self._tz(chat_id)
        fn = getattr(self, f"cmd_{cmd}", None)
        if not fn:
            return self.tg.send(chat_id, "Не знаю такую команду. /help")
        out = fn(chat_id, arg, now, tz)
        if out:
            self.tg.send(chat_id, *out) if isinstance(out, tuple) else self.tg.send(chat_id, out)

    # ---------- команды ----------
    def cmd_start(self, chat_id, arg, now, tz):
        return HELP

    cmd_help = cmd_start

    def _list_markup(self, events: list[dict]) -> dict | None:
        rows = [[{"text": e["title"][:40], "callback_data": f"ev:{e['id']}"[:64]}] for e in events[:8]]
        return {"inline_keyboard": rows} if rows else None

    def cmd_now(self, chat_id, arg, now, tz):
        act = sorted((e for e in self.events if render.status_of(e, now) == "active"),
                     key=lambda e: e["end"])
        if not act:
            return "Сейчас активных событий нет. Загляни в /soon."
        text = render.paginate([render.event_line(e, now, tz) for e in act], "<b>🟢 Идёт сейчас</b>\n")
        return text, self._list_markup(act)

    def cmd_soon(self, chat_id, arg, now, tz):
        horizon = now + timedelta(days=30)
        up = sorted((e for e in self.events
                     if render.status_of(e, now) == "upcoming" and parse_iso(e["start"]) <= horizon),
                    key=lambda e: e["start"])
        if not up:
            return "В ближайшие 30 дней событий не найдено."
        text = render.paginate([render.event_line(e, now, tz) for e in up], "<b>🗓 Ближайшие 30 дней</b>\n")
        return text, self._list_markup(up)

    def cmd_event(self, chat_id, arg, now, tz):
        if not arg:
            return "Напиши название: /event Christmas"
        e = self.find_event(arg, now)
        if not e:
            return "Не нашёл такое событие. Попробуй часть названия (на английском, как в игре)."
        hist = sorted(x["start"][:4] for x in self.events if x["series"] == e["series"] and x["status"] == "confirmed")
        extra = f"\n\n📜 В прошлом: {', '.join(dict.fromkeys(hist))}" if len(hist) > 1 else ""
        return render.event_card(e, now, tz) + extra

    def cmd_shop(self, chat_id, arg, now, tz):
        rows = [r for r in self.ds.get("shop", []) if render.status_of(r, now) != "past"
                and parse_iso(r["start"]) <= now + timedelta(days=60)]
        if not rows:
            return "Данных о доступности монстров на ближайшие 60 дней нет."
        rows.sort(key=lambda r: (render.status_of(r, now) != "active", r["start"]))
        return render.paginate([render.shop_line(r, now) for r in rows], "<b>🛒 Монстры: доступность</b>\n")

    def cmd_monster(self, chat_id, arg, now, tz):
        if not arg:
            return "Напиши имя: /monster Rare Wubbox"
        names = sorted({r["monster"] for r in self.ds.get("shop", [])})
        hit = [n for n in names if arg.lower() in n.lower()] or difflib.get_close_matches(arg, names, 1, 0.5)
        if not hit:
            return "Про этого монстра нет данных о доступности."
        rows = sorted((r for r in self.ds["shop"] if r["monster"] == hit[0]), key=lambda r: r["start"])
        lines = [render.shop_line(r, now) for r in rows[-12:]]
        return render.paginate(lines, f"<b>🐾 {escape(hit[0])}</b>\n")

    def cmd_tz(self, chat_id, arg, now, tz):
        if not arg:
            return f"Текущий пояс: <code>{escape(tz)}</code>. Смена: /tz Europe/Moscow"
        try:
            ZoneInfo(arg)
        except Exception:
            return "Неизвестный пояс. Формат IANA, например: Europe/Kyiv, Asia/Almaty, Europe/Moscow"
        self.state.user(chat_id)["tz"] = arg
        return f"Часовой пояс: <code>{escape(arg)}</code>"

    def cmd_about(self, chat_id, arg, now, tz):
        gen = self.ds.get("generated_at")
        age = render.fmt_delta(now - parse_iso(gen)) if gen else "?"
        st = self.ds.get("stats", {})
        return (f"Данные собираются автоматически из вики: {escape(self.ds.get('wiki', ''))}\n"
                f"Обновлено {age} назад · событий: {st.get('events', 0)} · монстров в базе: {st.get('monsters', 0)}\n"
                "🔮 — прогноз по датам прошлых лет, не официальный анонс.")

    def _settings_view(self, chat_id) -> tuple[str, dict]:
        subs = self.state.user(chat_id)["subs"]
        rows = [[{"text": f"{'✅' if subs.get(k) else '⬜'} {label}", "callback_data": f"set:{k}"}]
                for k, label in SUB_LABELS.items()]
        return "<b>🔔 Уведомления</b>\nНажми, чтобы включить/выключить:", {"inline_keyboard": rows}

    def cmd_settings(self, chat_id, arg, now, tz):
        return self._settings_view(chat_id)

    # ---------- inline-кнопки ----------
    def _callback(self, cq: dict) -> None:
        chat_id = cq["message"]["chat"]["id"]
        mid = cq["message"]["message_id"]
        data = cq.get("data", "")
        now = self.clock()
        if data.startswith("set:") and data[4:] in SUB_LABELS:
            subs = self.state.user(chat_id)["subs"]
            subs[data[4:]] = not subs.get(data[4:], True)
            text, markup = self._settings_view(chat_id)
            self.tg.edit(chat_id, mid, text, markup)
            self.tg.answer(cq["id"], "Сохранено")
        elif data.startswith("ev:"):
            e = next((x for x in self.events if x["id"] == data[3:]), None)
            self.tg.answer(cq["id"])
            if e:
                self.tg.send(chat_id, render.event_card(e, now, self._tz(chat_id)))
        else:
            self.tg.answer(cq["id"])

    # ---------- уведомления ----------
    def due_notifications(self, now: datetime) -> list[tuple[str, str, str, dict]]:
        """→ [(kind, dedupe_key, text, event)] для всех подтверждённых событий."""
        out = []
        for e in self.events:
            if e.get("status") != "confirmed":
                continue
            s, en = parse_iso(e["start"]), parse_iso(e["end"])
            if parse_iso(e.get("first_seen", "1970-01-01T00:00:00Z")) > now - NEW_WINDOW and now < en:
                if s > now:
                    out.append(("new", f"{e['id']}:new", "🆕 <b>Новый анонс</b>\n" + render.event_card(e, now), e))
            if s <= now < min(en, s + NOTIFY_PAST_WINDOW):
                # акции/«вне сезона»/разовые окна — это про магазин, их включает тумблер «Монстры в магазине»
                k = "shop" if e.get("kind") in ("promotion", "outofseason", "other") else "start"
                out.append((k, f"{e['id']}:start", "▶️ <b>Началось</b>\n" + render.event_card(e, now), e))
            if now < en <= now + ENDING_WINDOW and now >= s:
                out.append(("ending", f"{e['id']}:ending", "⏰ <b>Скоро конец</b>\n" + render.event_line(e, now), e))
        for r in self.ds.get("shop", []):
            if r.get("status") != "confirmed" or r.get("event_id"):
                continue  # окна, привязанные к событию, уже покрыты уведомлением о событии
            s, en = parse_iso(r["start"]), parse_iso(r["end"])
            if s <= now < min(en, s + NOTIFY_PAST_WINDOW):
                out.append(("shop", f"shop:{r['monster']}:{r['start']}", "🐾 <b>Монстр доступен</b>\n" + render.shop_line(r, now), r))
        return out

    def send_notifications(self) -> int:
        now = self.clock()
        sent = 0
        due = self.due_notifications(now)
        for chat, user in list(self.state.d["users"].items()):
            for kind, key, text, _ in due:
                if not user["subs"].get(kind, True):
                    continue
                full = f"{chat}:{key}"
                if self.state.was_sent(full):
                    continue
                if kind != "new" and parse_iso(_trigger_time(kind, _)).timestamp() < user.get("since", 0) - 3600:
                    self.state.mark_sent(full)  # событие началось до подписки — молча пропускаем
                    continue
                try:
                    self.tg.send(int(chat), text)
                    sent += 1
                except TelegramError as e:
                    if e.code == 403:
                        self.state.remove_user(int(chat))
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
            if updates or time.monotonic() - last_notify >= notify_every:
                if time.monotonic() - last_notify >= notify_every:
                    self.send_notifications()
                    last_notify = time.monotonic()
                self.state.save()
        self.state.save()


def _trigger_time(kind: str, obj: dict) -> str:
    return obj["start"] if kind in ("start", "shop") else obj["end"]
