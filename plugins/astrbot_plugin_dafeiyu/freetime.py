"""空闲时刻：什么时候有一段自己的空闲、有了做什么，都由它自己定。

每到它上次定下的时间（在线、不在免打扰、没超预算），插件给它一段空闲：告诉它现在的状态，
列出这次真的能做的事（发消息、去空间、逛 B 站 / 微博、画图、记一件事、接着做记下的事、查点什么、读书），它可以做几件，也可以什么都不做，
顺便定下次什么时候再有这样一段（"next"）。见 main.py 的 _free_moment。

这里只放纯逻辑（解析 "next"、整理它的选择），存取和护栏在 main.py。
"""

import datetime as dt
import random
import re

from . import plans

DEFAULT_GAP = (60, 120)  # 它没说下次什么时候（或认不出来）就在这么多分钟后随机一个
FIRST_DELAY = (3, 8)  # 第一次启动时，几分钟后给它第一段空闲
MIN_GAP = 5 * 60  # 防出错：下次最早 5 分钟后
MAX_GAP = 24 * 3600  # 最晚 24 小时后
# 能做的事，也是执行的顺序（逛得最久，放最后）
KINDS = ("plan", "progress", "draw", "message", "qzone", "look_up", "read", "wander")

_MIN = re.compile(r"^(\d+(?:\.\d+)?)\s*(分钟|分|min|mins|minutes?|m)?(以?后)?$", re.I)
_HOUR = re.compile(r"^(\d+(?:\.\d+)?)\s*(个?小时|个?钟头|h|hours?)(以?后)?$", re.I)
_NO = ("", "null", "none", "false", "no", "skip", "否", "不")


def default_gap() -> float:
    return random.randint(*DEFAULT_GAP) * 60.0


def first_delay() -> float:
    return random.randint(*FIRST_DELAY) * 60.0


def _clamp(seconds: float) -> float:
    return max(MIN_GAP, min(MAX_GAP, seconds))


def next_gap(raw, now: dt.datetime) -> float:
    """它写的「下次什么时候」→ 多少秒以后。

    数字（或「90 分钟」「2 小时」）是多少分钟 / 小时以后；也可以写具体时间（格式同小本子，「HH:MM」过了就是明天）。
    不填、认不出来、或者写的时间已经过了，就用默认的一两个小时。结果夹在 5 分钟到 24 小时之间。
    """
    if isinstance(raw, bool) or raw is None:
        return _clamp(default_gap())
    if isinstance(raw, (int, float)):
        return _clamp(float(raw) * 60) if raw > 0 else _clamp(default_gap())
    s = str(raw).strip().replace("：", ":")
    if s.lower() in _NO:
        return _clamp(default_gap())
    m = _MIN.match(s)
    if m:
        return _clamp(float(m.group(1)) * 60)
    m = _HOUR.match(s)
    if m:
        return _clamp(float(m.group(1)) * 3600)
    at = plans.parse_when(s, now)
    if at and at > now:
        return _clamp((at - now).total_seconds())
    return _clamp(default_gap())


def text_of(v) -> str:
    s = str(v or "").strip() if isinstance(v, str) else ""
    return "" if s.lower() in _NO else s


def _yes(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "是"))


def read(out: dict, can: set, now: dt.datetime) -> dict:
    """把它输出的 JSON 整理成要做的事：只认这次能做的、认得出来的；写错的不替它猜。

    返回 {kind: 内容}：message → 要发的话；qzone → True；wander → 想看的（随便逛是空串）；
    draw → {"prompt", "note", "send"}；plan → (要做的事, 时间戳或 0)；
    progress → (小本子上的编号, 写下的进度)；look_up → 想查的；read → 想读的书名（空串是接着读上次那本）。
    """
    if not isinstance(out, dict):
        return {}
    got = {}
    if "message" in can:
        msg = text_of(out.get("message"))
        if msg:
            got["message"] = msg
    if "qzone" in can and _yes(out.get("qzone")):
        got["qzone"] = True
    if "wander" in can:
        w = out.get("wander")
        if _yes(w):
            got["wander"] = ""
        elif text_of(w):
            got["wander"] = text_of(w)[:40]
    if "draw" in can and isinstance(out.get("draw"), dict):
        d = out["draw"]
        prompt = text_of(d.get("prompt"))
        if prompt:
            note = (text_of(d.get("note")) or prompt)[:100]  # 没写备注就拿画面描述当备注，免得在收藏里找不到
            got["draw"] = {"prompt": prompt, "note": note, "send": "message" in can and _yes(d.get("send"))}
    if "plan" in can:
        p = plans.from_reflect([out.get("plan")] if out.get("plan") else [], now, 1)
        if p:
            got["plan"] = p[0]
    if "progress" in can and isinstance(out.get("progress"), dict):
        pr = out["progress"]
        note = text_of(pr.get("text"))[: plans.PROGRESS_CHARS]
        if note and pr.get("id") is not None:
            got["progress"] = (pr["id"], note)
    if "look_up" in can:
        q = text_of(out.get("look_up"))
        if q:
            got["look_up"] = q[:60]
    if "read" in can:
        r = out.get("read")
        if _yes(r):
            got["read"] = ""
        elif text_of(r):
            got["read"] = text_of(r)[:60]
    return {k: got[k] for k in KINDS if k in got}
