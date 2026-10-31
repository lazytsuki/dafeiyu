"""它的心愿单：它希望有的东西，写给 TA 看（和日记、小本子不同，这份 TA 能看到）。

TA 用 /fy 心愿 看；用 /fy 心愿 完成 <编号> 告诉它哪件已经有了。
它在 TA 私聊和反思里各看到一次「TA 说这个已经有了」，之后这条就从单子上移走。

每条：{"id": 编号, "text": 它写的, "made": 写下的时间戳}
TA 标了已满足的再加上：{"done": 时间戳, "note": TA 的一句话, "told": ["chat", "reflect"] 里已经告诉过它的地方}
这里只放纯逻辑，存取和护栏在 main.py。
"""

import datetime as dt
import re

MAX_WISHES = 50  # 不是限制，是防出错刷满的保险；满了要先划掉几条才能再写（不替它丢）
TOLD_IN = ("chat", "reflect")  # 满足了的心愿要在这两处都告诉过它，才从单子上移走
DONE_KEEP_DAYS = 14  # 满足了的心愿最多再留这么久（一直没机会告诉它也不无限留着）


def _id(raw) -> int | None:
    try:
        return int(str(raw).strip().lstrip("#[").rstrip("]"))
    except ValueError:
        return None


def open_ones(wishes: list) -> list:
    """还没被满足的。"""
    return [w for w in wishes if not w.get("done")]


def add(wishes: list, wid: int, text: str, made: float) -> list:
    return wishes + [{"id": int(wid), "text": text, "made": made}]


def drop(wishes: list, wid) -> tuple[list, dict | None]:
    """它自己划掉某一条（只划还没满足的），返回 (剩下的, 划掉的那条)；没有就原样返回和 None。"""
    wid = _id(wid)
    gone = next((w for w in open_ones(wishes) if w["id"] == wid), None)
    if gone is None:
        return wishes, None
    return [w for w in wishes if w is not gone], gone


def mark_done(wishes: list, wid, note: str, now_ts: float) -> tuple[list, dict | None]:
    """TA 说某一条已经有了。返回 (新的单子, 那一条)；没有这条（或已经标过）返回原样和 None。"""
    wid = _id(wid)
    hit = next((w for w in open_ones(wishes) if w["id"] == wid), None)
    if hit is None:
        return wishes, None
    done = {**hit, "done": now_ts, "note": note, "told": []}
    return [done if w is hit else w for w in wishes], done


def to_tell(wishes: list, where: str) -> list:
    """满足了、还没在 where 告诉过它的。"""
    return [w for w in wishes if w.get("done") and where not in w.get("told", [])]


def told(wishes: list, where: str, ids: set, now_ts: float) -> list:
    """记下这几条已经在 where 告诉过它；两处都告诉过了、或放太久了，就移走。"""
    out = []
    for w in wishes:
        if w.get("done"):
            t = list(w.get("told", []))
            if w["id"] in ids and where not in t:
                t.append(where)
            if all(x in t for x in TOLD_IN) or now_ts - w["done"] > DONE_KEEP_DAYS * 86400:
                continue
            w = {**w, "told": t}
        out.append(w)
    return out


def granted_text(w: dict) -> str:
    """「TA 说这个已经有了」那一句。"""
    return f"「{w['text']}」" + (f"（TA 说：{w['note']}）" if w.get("note") else "")


def listing(wishes: list) -> str:
    """给它自己看的心愿单（还没满足的）。"""
    return "\n".join(f"- [{w['id']}] {w['text']}" for w in open_ones(wishes))


def for_owner(wishes: list) -> str:
    """/fy 心愿 给 TA 看的：编号、内容、写下的时间。"""
    items = open_ones(wishes)
    if not items:
        return "心愿单现在是空的。"
    lines = [f"它的心愿单（{len(items)} 条）："]
    for w in items:
        lines.append(f"{w['id']}. {w['text']}（{dt.datetime.fromtimestamp(w['made']):%m-%d %H:%M} 写的）")
    waiting = [w for w in wishes if w.get("done")]
    if waiting:
        lines.append(f"\n你标了已满足、它还没全看到的：{'、'.join(str(w['id']) for w in waiting)}")
    lines.append("\n/fy 心愿 完成 <编号> [一句话] —— 告诉它这件已经有了")
    return "\n".join(lines)


def parse_cmd(message: str) -> tuple[str, str, str]:
    """解析「fy 心愿 [完成 <编号> [一句话]]」→ (动作, 编号, 一句话)。动作是 "" 或 "完成"。"""
    m = re.search(r"心愿\s*(.*)$", str(message or "").strip(), re.S)
    rest = (m.group(1) if m else "").strip()
    m = re.match(r"^完成\s*(\S*)\s*(.*)$", rest, re.S)
    if not m:
        return "", "", ""
    return "完成", m.group(1), m.group(2).strip()[:200]


def from_reflect(raw, limit: int = 5) -> list[str]:
    """反思里写下的新心愿 → [文字]。"""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        text = str((item.get("text") if isinstance(item, dict) else item) or "").strip()
        if text:
            out.append(text[:200])
    return out[:limit]


def ids_from(raw) -> list[int]:
    """反思里想划掉的编号 → [编号]（认不出的跳过）。"""
    if not isinstance(raw, list):
        raw = [raw] if raw not in (None, "") else []
    return [i for i in (_id(x) for x in raw) if i is not None]
