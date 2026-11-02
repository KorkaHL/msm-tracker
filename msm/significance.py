"""Классификация событий и объяснение «что это значит для игрока» на русском.

Работает по ключевым словам английского описания, поэтому не требует ручной разметки.
Правила можно улучшать — на данные в events.json они влияют только через теги,
а текст собирается при показе.
"""
from __future__ import annotations

import re

# kind: (regex по названию/тексту, русское название, общий смысл)
KINDS: list[tuple[str, str, str, str]] = [
    ("wublin", r"\bwublin", "🥁 Wublin", "Раз в какое-то время Wublin-остров оживает: нужно собрать монстров острова, пока он «включён»."),
    ("celestial", r"\bcelestial", "🌌 Celestial", "Небесный остров: Celestial-монстры доступны по расписанию и требуют заранее накопленных ресурсов."),
    ("amber", r"\bamber|\bamber island", "🔶 Amber Island", "Янтарный остров открывается периодически — собирай Amber-монстров и их предметы пока он активен."),
    ("seasonal", r"christmas|halloween|valentine|easter|thanksgiving|new year|lunar|spring|summer|autumn|winter|holiday|yuletide|harvest|snow", "🎄 Сезонное", "Сезонное событие: приносит тематических монстров/украшения, которые возвращаются только раз в год."),
    ("anniversary", r"anniversar|birthday", "🎂 Годовщина", "Юбилейное событие: обычно щедрые подарки, скидки и ограниченные награды."),
    ("update", r"\bupdate\b|\bpatch\b|\bversion \d", "🆕 Обновление", "Обновление игры: могут появиться новые острова, монстры и механики."),
    ("sale", r"\bsale\b|\bdiscount|\bbundle", "🏷️ Распродажа", "Распродажа: цены в магазине временно ниже."),
    ("collab", r"collab|crossover|partner", "🤝 Коллаборация", "Коллаборация: уникальные монстры/предметы, которые вряд ли вернутся."),
]

# tag: (regex по тексту, ярлык, смысл для игрока)
TAGS: list[tuple[str, str, str, str]] = [
    ("monsters", r"\b(new|limited|exclusive|special)\b.{0,50}\bmonsters?\b|\bmonsters?\b.{0,60}\b(available|return|returns|introduced)\b", "🐾 Монстры",
     "В событии доступны особые монстры — после окончания купить/вывести их может быть нельзя."),
    ("costumes", r"\bcostumes?\b|\bskins?\b", "🎭 Костюмы", "Выходят костюмы для монстров. Обычно их можно получить только пока идёт событие."),
    ("diamonds", r"\b(double|2x|extra|bonus|free)\b.{0,40}\bdiamonds?\b|\bdiamonds?\b.{0,40}\b(double|2x|bonus|free)\b", "💎 Бонус алмазов",
     "Алмазов можно получить больше обычного — выгодно тратить и копить именно сейчас."),
    ("sale", r"\b(sale|discount\w*|% off|cheaper|reduced)\b", "🏷️ Скидки", "Временные скидки: если планировал покупку, сейчас дешевле."),
    ("relics", r"\brelics?\b", "🏺 Реликвии", "Связано с реликвиями (Relics) — ресурс для украшения островов, их легко упустить."),
    ("ethereal", r"\bethereal\b", "👻 Ethereal", "Затрагивает Ethereal Island: отправляй ethereal-ресурсы и следи за Ethereal-монстрами."),
    ("wublin", r"\bwublin", "🥁 Wublin", "Wublin-остров активен/меняется — можно собирать Wublin-монстров и их Zap-награды."),
    ("celestial", r"\bcelestial", "🌌 Celestial", "Celestial-остров: для монстров нужны заранее накопленные ресурсы, готовься."),
    ("amber", r"\bamber\b", "🔶 Amber", "Amber Island: пока активна, можно получить амберных монстров и предметы."),
    ("rewards", r"\b(rewards?|prizes?|goals?|quests?|tasks?|challenges?)\b", "🎁 Награды", "В событии есть задания и награды — чем раньше начнёшь, тем проще всё выполнить."),
    ("login", r"\b(login|daily gifts?|free gift|calendar)\b", "📅 Ежедневные подарки", "Заходи каждый день — подарки привязаны к дням события."),
    ("island", r"\b(island|isle)\b", "🏝️ Острова", "Могут быть временные или специальные острова — плати вниманием к монетам и ресурсам заранее."),
    ("breeding", r"\bbreed\w*\b|\bbreeding\b", "🧬 Размножение", "Меняются условия или шансы размножения — хорошее время для получения редких монстров."),
    ("update", r"\bupdate\b|\bpatch\b", "🆕 Обновление", "Поменялись механики или контент — стоит проверить, что нового."),
]

_KIND_BY_ID = {k[0]: k for k in KINDS}
_TAG_BY_ID = {t[0]: t for t in TAGS}


def analyze(title: str, text: str) -> tuple[str, list[str]]:
    """→ (kind, tags). Вид определяется по названию в первую очередь."""
    kind = "other"
    for kid, rx, *_ in KINDS:
        if re.search(rx, title, re.I):
            kind = kid
            break
    else:
        head = text[:600]
        for kid, rx, *_ in KINDS:
            if re.search(rx, head, re.I):
                kind = kid
                break
    tags = [tid for tid, rx, *_ in TAGS if re.search(rx, text, re.I | re.S)]
    return kind, tags


def kind_label(kind: str) -> str:
    return _KIND_BY_ID[kind][2] if kind in _KIND_BY_ID else "📌 Событие"


def tag_labels(tags: list[str]) -> list[str]:
    return [_TAG_BY_ID[t][2] for t in tags if t in _TAG_BY_ID]


def meaning(kind: str, tags: list[str]) -> list[str]:
    out: list[str] = []
    if kind in _KIND_BY_ID:
        out.append(_KIND_BY_ID[kind][3])
    for t in tags:
        if t in _TAG_BY_ID and _TAG_BY_ID[t][3] not in out and t != kind:
            out.append(_TAG_BY_ID[t][3])
    return out[:5]
