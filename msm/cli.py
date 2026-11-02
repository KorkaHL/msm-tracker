"""CLI: update-data | run-bot | probe | gen-key.

  python -m msm.cli update-data        # вики → data/events.json (с защитой от «сломанного» парсинга)
  python -m msm.cli run-bot --seconds 330 --state state/state.bin
  python -m msm.cli probe --search event   # помогает подобрать категории вики
  python -m msm.cli gen-key            # ключ шифрования для секрета STATE_KEY
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import pipeline
from .wiki import Wiki


def cmd_update(args) -> int:
    cfg = pipeline.load_config()
    wiki = Wiki(cfg["wiki"])
    previous = pipeline.load_dataset()
    now = datetime.now(timezone.utc)
    ds = pipeline.build_dataset(cfg, wiki, now, previous)
    ok, why = pipeline.sanity_ok(ds["events"], previous)
    if not ok:
        print(f"::error::данные НЕ обновлены: {why}", file=sys.stderr)
        _alert(f"⚠️ MSM Tracker: данные не обновлены — {why}")
        return 1
    if previous and all(previous.get(k) == ds[k] for k in ("events", "shop")):
        print("данные не изменились")  # не трогаем файл — не будет пустых коммитов
        return 0
    pipeline.save_dataset(ds)
    print(f"ok: {ds['stats']}")
    return 0


def _alert(text: str) -> None:
    token, admin = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("ADMIN_CHAT_ID")
    if token and admin:
        try:
            from .telegram import Telegram
            Telegram(token).send(int(admin), text)
        except Exception as e:
            print(f"alert failed: {e}", file=sys.stderr)


def cmd_bot(args) -> int:
    from .bot import Bot
    from .state import State
    from .telegram import Telegram

    ds = pipeline.load_dataset()
    if not ds:
        print("нет data/events.json — сначала update-data", file=sys.stderr)
        return 1
    bot = Bot(Telegram(os.environ["TELEGRAM_TOKEN"]), State(Path(args.state)), ds)
    bot.run(args.seconds)
    return 0


def cmd_probe(args) -> int:
    cfg = pipeline.load_config()
    wiki = Wiki(cfg["wiki"])
    for prefix in ("Event", "Season", "Monster", "Limited", "Holiday", "Wublin", "Celestial", "Amber"):
        print(f"категории «{prefix}…»:", wiki.categories_with_prefix(prefix))
    if args.search:
        print(f"поиск «{args.search}»:", wiki.search(args.search, 20))
    ds = pipeline.build_dataset(cfg, wiki, datetime.now(timezone.utc), None)
    print("статистика:", ds["stats"])
    for e in ds["events"][:40]:
        print(f"  {e['start'][:10]}→{e['end'][:10]} {e['status']:9} {e['title']}  [{e['source'][:50]}]")
    return 0


def cmd_key(args) -> int:
    from cryptography.fernet import Fernet
    print(Fernet.generate_key().decode())
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="msm")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update-data").set_defaults(fn=cmd_update)
    b = sub.add_parser("run-bot")
    b.add_argument("--seconds", type=int, default=330)
    b.add_argument("--state", default="state/state.bin")
    b.set_defaults(fn=cmd_bot)
    pr = sub.add_parser("probe")
    pr.add_argument("--search", default="")
    pr.set_defaults(fn=cmd_probe)
    sub.add_parser("gen-key").set_defaults(fn=cmd_key)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
