"""它自己的节奏：每晚反思时，它可以调整自己什么时候、多常想找 TA，什么时候去空间看看。

配置里的值只是起点；它写过自己的节奏以后，就按它的来。硬限制只有两类，都不是对它的约束：
- TA 的免打扰时段（照顾 TA）；
- 防封护栏：每天主动私聊、说说、评论的上限。
"""

import datetime as dt
import re

_HM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MAX_GREETS = 3
MAX_WINDOWS = 3
MAX_POST_TIMES = 4


def _hm(s: str) -> dt.time | None:
    m = _HM.match(str(s).strip())
    return dt.time(int(m.group(1)), int(m.group(2))) if m else None


def _window(s: str) -> tuple[dt.time, dt.time] | None:
    parts = str(s).replace("～", "-").replace("~", "-").replace("–", "-").split("-")
    if len(parts) != 2:
        return None
    a, b = _hm(parts[0]), _hm(parts[1])
    return (a, b) if a and b and a != b else None


def in_window(t: dt.time, start: dt.time, end: dt.time) -> bool:
    return start <= t < end if start <= end else (t >= start or t < end)


def in_windows(t: dt.time, windows: list[str]) -> bool:
    """没有时间段表示什么时候都行。"""
    parsed = [w for w in (_window(x) for x in windows) if w]
    return not parsed or any(in_window(t, a, b) for a, b in parsed)


def window_minutes(windows: list[str]) -> int:
    total = 0
    for w in windows:
        p = _window(w)
        if p:
            a, b = (x.hour * 60 + x.minute for x in p)
            total += (b - a) % 1440
    return total or 1440


def normalize(raw, daily_max: int) -> dict | None:
    """把它在反思里写下的节奏整理成能用的样子；完全没法用就返回 None（保持原样）。

    只保留认得出来的部分：写错的时间直接丢掉，不替它猜。
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    if "reach_out_per_day" in raw:
        try:
            out["reach_out_per_day"] = max(0, min(int(raw["reach_out_per_day"]), daily_max))
        except (TypeError, ValueError):
            pass
    if isinstance(raw.get("reach_out_hours"), list):
        out["reach_out_hours"] = [
            f"{a:%H:%M}-{b:%H:%M}" for a, b in (w for w in map(_window, raw["reach_out_hours"]) if w)
        ][:MAX_WINDOWS]
    if isinstance(raw.get("greet_times"), list):
        greets = []
        for item in raw["greet_times"]:
            t, _, why = str(item).strip().partition(" ")
            if _hm(t):
                greets.append(f"{_hm(t):%H:%M} {why.strip() or '想说句话'}")
        out["greet_times"] = sorted(greets)[:MAX_GREETS]
    if isinstance(raw.get("post_times"), list):
        times = sorted({f"{_hm(x):%H:%M}" for x in raw["post_times"] if _hm(x)})
        out["post_times"] = times[:MAX_POST_TIMES]
    return out or None


def describe(r: dict) -> str:
    """给它自己看的现在的节奏。"""
    lines = []
    if "reach_out_per_day" in r:
        lines.append(f"每天随口找 TA 最多 {r['reach_out_per_day']} 次")
    if "reach_out_hours" in r:
        lines.append(f"想找 TA 的时间段：{'、'.join(r['reach_out_hours']) or '什么时候都行'}")
    if "greet_times" in r:
        lines.append(f"到点想不想跟 TA 说句话的时刻：{'；'.join(r['greet_times']) or '没有'}")
    if "post_times" in r:
        lines.append(f"想去空间看看的时刻：{'、'.join(r['post_times']) or '没有'}")
    return "\n".join(lines)
