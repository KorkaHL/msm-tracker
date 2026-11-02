"""Минимальный разбор вики-разметки MediaWiki (шаблоны, таблицы, ссылки, очистка)."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

_IGNORED_PREFIXES = ("file:", "image:", "category:", "template:", "user:", "special:", "help:", "talk:")


def strip_comments(t: str) -> str:
    return re.sub(r"<!--.*?-->", "", t, flags=re.S)


def _match_braces(t: str, i: int) -> int:
    depth, j, n = 0, i, len(t)
    while j < n:
        if t.startswith("{{", j):
            depth += 1
            j += 2
        elif t.startswith("}}", j):
            depth -= 1
            j += 2
            if depth == 0:
                return j
        else:
            j += 1
    return -1


def _split_top(s: str, sep: str = "|") -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    brace = link = 0
    i = 0
    while i < len(s):
        two = s[i : i + 2]
        if two == "{{":
            brace += 1
        elif two == "}}":
            brace -= 1
        elif two == "[[":
            link += 1
        elif two == "]]":
            link -= 1
        if two in ("{{", "}}", "[[", "]]"):
            cur.append(two)
            i += 2
            continue
        if s[i] == sep and brace <= 0 and link <= 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(s[i])
        i += 1
    parts.append("".join(cur))
    return parts


@dataclass
class Template:
    name: str
    params: dict[str, str] = field(default_factory=dict)


def parse_templates(text: str) -> list[Template]:
    """Шаблоны верхнего уровня: {{Name|a=b|positional}}."""
    out: list[Template] = []
    i = 0
    while i < len(text) - 1:
        if text.startswith("{{", i):
            end = _match_braces(text, i)
            if end == -1:
                break
            parts = _split_top(text[i + 2 : end - 2])
            tpl = Template(parts[0].strip().lower())
            pos = 0
            for part in parts[1:]:
                if "=" in part:
                    k, v = part.split("=", 1)
                    tpl.params[k.strip().lower()] = v.strip()
                else:
                    pos += 1
                    tpl.params[str(pos)] = part.strip()
            out.append(tpl)
            i = end
        else:
            i += 1
    return out


def _link_sub(m: re.Match) -> str:
    inner = m.group(1)
    target, _, label = inner.partition("|")
    if target.strip().lower().startswith(_IGNORED_PREFIXES):
        return ""
    return (label or target).strip()


def clean(text: str) -> str:
    """Вики-разметка → читаемый текст (без таблиц и шаблонов)."""
    t = strip_comments(text)
    t = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", t, flags=re.S | re.I)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\{\|.*?\|\}", "", t, flags=re.S)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\[\[([^\[\]]*)\]\]", _link_sub, t)
    t = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", t)
    t = re.sub(r"\[https?://\S+\]", "", t)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("'''", "").replace("''", "")
    t = html.unescape(t)
    return re.sub(r"[ \t]+", " ", t)


def links(text: str) -> list[str]:
    """Цели внутренних ссылок [[Target|label]] (без File/Category/...)."""
    out: list[str] = []
    for m in re.finditer(r"\[\[([^\[\]|#]+)", strip_comments(text)):
        target = m.group(1).strip()
        if not target.lower().startswith(_IGNORED_PREFIXES):
            out.append(target)
    return out


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]


_ATTR_CELL = re.compile(r"^([^\[\]{}|]*=[^\[\]{}|]*)\|(?!\|)(.*)$", re.S)


def parse_tables(text: str) -> list[Table]:
    tables: list[Table] = []
    for m in re.finditer(r"\{\|(.*?)\n\|\}", strip_comments(text), flags=re.S):
        headers: list[str] = []
        rows: list[list[str]] = []
        cur: list[str] | None = None
        is_header = False
        for line in m.group(1).split("\n"):
            line = line.strip()
            if line.startswith("|-"):
                if cur:
                    (headers.extend(cur) if is_header and not headers else rows.append(cur))
                cur, is_header = [], False
                continue
            if line.startswith("|+"):
                continue
            if line.startswith(("!", "|")):
                if cur is None:
                    cur = []
                if line.startswith("!") and not cur:
                    is_header = True
                for c in re.split(r"\|\||!!", line[1:]):
                    am = _ATTR_CELL.match(c)
                    cur.append((am.group(2) if am else c).strip())
            elif cur:
                cur[-1] += "\n" + line
        if cur:
            (headers.extend(cur) if is_header and not headers else rows.append(cur))
        tables.append(Table(headers, rows))
    return tables


def lead_paragraph(cleaned: str, max_len: int = 500) -> str:
    for block in re.split(r"\n\s*\n|\n", cleaned):
        b = block.strip()
        if len(b) < 40 or b.startswith(("=", "*", "#", ":", ";", "|", "!")):
            continue
        if len(b) <= max_len:
            return b
        cut = b[:max_len]
        end = max(cut.rfind(". "), cut.rfind("! "))
        return cut[: end + 1] if end > 120 else cut.rstrip() + "…"
    return ""
