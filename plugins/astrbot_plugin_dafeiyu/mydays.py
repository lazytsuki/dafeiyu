"""它自己记下的日子：它觉得重要的那一天，可以每年都记得，也可以只是某一年的某一天。

和 days.py 不一样：那边是日历本身（农历、节气、节日），这里是它自己写进去的日子。
到了那天（或者快到了），插件在「今天是什么日子」那一句里提一下；提不提、在不在意由它。

每条：{"id": 编号, "date": "MM-DD"（每年）或 "YYYY-MM-DD"（只那一天）, "what": 它自己写的,
       "year": 记的是哪一年的事（每年的那种才有，算「第几年」用，可以没有）, "made": 记下的时间戳}
这里只放纯逻辑，存取和护栏在 main.py。
"""

import datetime as dt
import re

MAX_DAYS = 60  # 防出错刷满的保险；满了要先划掉几条才能再记
AHEAD = 3  # 提前几天提一句
FORMATS = "「MM-DD」（每年的这一天）或「YYYY-MM-DD」（只是那一天；想每年都记得，就把 every_year 设成 true）"

_FULL = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MD = re.compile(r"^(\d{1,2})-(\d{1,2})$")


def parse(text: str, every_year: bool) -> dict | None:
    """它写的日期 → {"date": ..., "year": ...}；认不出来（或日期不存在）返回 None。

    「MM-DD」总是每年；「YYYY-MM-DD」默认只是那一天，every_year 时变成每年的这一天、记住是哪年开始的。
    """
    s = str(text or "").strip().replace("/", "-").replace(".", "-")
    s = re.sub(r"(\d+)年(\d+)月(\d+)日?", r"\1-\2-\3", s)
    s = re.sub(r"(\d+)月(\d+)日?", r"\1-\2", s)
    try:
        m = _FULL.match(s)
        if m:
            y, mo, d = map(int, m.groups())
            dt.date(y, mo, d)
            if every_year:
                return {"date": f"{mo:02d}-{d:02d}", "year": y}
            return {"date": f"{y:04d}-{mo:02d}-{d:02d}"}
        m = _MD.match(s)
        if m:
            mo, d = map(int, m.groups())
            dt.date(2024, mo, d)  # 闰年，2 月 29 日也认
            return {"date": f"{mo:02d}-{d:02d}"}
    except ValueError:
        return None
    return None


def add(items: list, did: int, parsed: dict, what: str, made: float) -> list:
    return items + [{"id": int(did), **parsed, "what": what, "made": made}]


def drop(items: list, did) -> tuple[list, dict | None]:
    try:
        did = int(str(did).strip().lstrip("#[").rstrip("]"))
    except ValueError:
        return items, None
    gone = next((x for x in items if x["id"] == did), None)
    return [x for x in items if x["id"] != did], gone


def _next_on(item: dict, today: dt.date) -> dt.date | None:
    """这个日子下一次（含今天）是哪天；只那一天的、已经过了就是 None。"""
    s = item["date"]
    if len(s) == 10:
        d = dt.date.fromisoformat(s)
        return d if d >= today else None
    mo, d = map(int, s.split("-"))
    for y in (today.year, today.year + 1):
        try:
            on = dt.date(y, mo, d)
        except ValueError:  # 2 月 29 日，不是闰年就在 2 月 28 日提
            on = dt.date(y, 2, 28)
        if on >= today:
            return on
    return None


def fmt_date(item: dict) -> str:
    s = item["date"]
    if len(s) == 10:
        return s
    return f"每年 {s}" + (f"（从 {item['year']} 年起）" if item.get("year") else "")


def near(items: list, today: dt.date) -> list[str]:
    """今天和接下来几天的，写成一句句给它看。"""
    out = []
    for x in sorted(items, key=lambda x: _next_on(x, today) or dt.date.max):
        on = _next_on(x, today)
        if on is None:
            continue
        gap = (on - today).days
        if gap > AHEAD:
            continue
        nth = f"（第 {on.year - x['year']} 年）" if x.get("year") and on.year > x["year"] else ""
        when = "今天" if gap == 0 else ("明天" if gap == 1 else f"再过 {gap} 天")
        out.append(f"{when}是你记下的日子：{x['what']}{nth}")
    return out


def tidy(items: list, today: dt.date) -> list:
    """只那一天的、过去超过一个月的就清掉；每年的一直留着。"""
    return [x for x in items if len(x["date"]) != 10 or (today - dt.date.fromisoformat(x["date"])).days <= 31]


def listing(items: list) -> str:
    return "\n".join(f"- [{x['id']}] {fmt_date(x)}：{x['what']}" for x in sorted(items, key=lambda x: x["date"][-5:]))
