"""群里最近的消息：它在群里被叫到时，能看到叫它之前大家在聊什么。

AstrBot 只把 @它 / 唤醒它的那几句交给模型，群里别的话它一句都看不到。
这里把每个群最近的几条消息记在内存里（不写盘、不进成长记录、不进 TA 的流水），
被叫到时作为背景附上；接不接、怎么接由它自己。

每条：{"t": ts, "id": 消息 ID, "who": 谁, "text": 说了什么,
       "img": 还没看过的图的地址（没有就是 ""）, "ph": 那张图在 text 里的占位（看过之后换成描述）}
"""

import datetime as dt

from .senses import describe_segment
from .vision import tag

MAX_TEXT = 500  # 一条消息最多带这么多字（图的描述也算在里面）


def record(lines: list[dict], entry: dict, now: float, keep: int, hours: float) -> list[dict]:
    """记下一条，只留最近 hours 小时里的最后 keep 条。"""
    return trim(lines + [entry], now, keep, hours)


def trim(lines: list[dict], now: float, keep: int, hours: float) -> list[dict]:
    if hours > 0:
        lines = [x for x in lines if now - x["t"] < hours * 3600]
    return lines[-keep:] if keep > 0 else []


def entry(t: float, msg_id: str, who: str, text: str, img: str = "", ph: str = "") -> dict:
    text = " ".join(str(text or "").split())
    return {"t": t, "id": str(msg_id or ""), "who": who, "text": text[:MAX_TEXT], "img": img, "ph": ph}


def text_of(segments: list, descs: list[str], self_id: str = "", stt: bool = False) -> tuple[str, str, str]:
    """一条群消息 → (文字, 第一张没看过的图的地址, 它在文字里的占位)。

    descs 是这条消息里各张图已经看出来的描述（按顺序，看不了的是空串）；没看过的图写成 [图片]。
    @它写成「@你」。
    """
    out, img, ph, n = [], "", "", 0
    for s in segments if isinstance(segments, list) else []:
        if not isinstance(s, dict):
            continue
        t, data = s.get("type"), s.get("data") or {}
        if t == "text":
            out.append(data.get("text", ""))
        elif t == "at":
            qq = str(data.get("qq", ""))
            out.append("@你 " if self_id and qq == str(self_id) else f"@{data.get('name') or qq} ")
        elif t == "image":
            desc = descs[n] if n < len(descs) else ""
            n += 1
            if desc:
                out.append(tag(desc))
                continue
            p = f"[{str(data.get('summary') or '').strip('[]') or '图片'}]"
            if not img and data.get("url"):
                img, ph = data["url"], p
            out.append(p)
        elif t == "reply":
            out.append("[回复了一条消息]")
        else:
            out.append(describe_segment(s, stt) or f"[{t}]")
    return "".join(out).strip(), img, ph


def fill(x: dict, desc: str) -> dict:
    """补看了某条里的图：占位换成描述，这张不再算没看过。"""
    ph = x.get("ph") or "[图片]"
    text = x["text"].replace(ph, tag(desc), 1) if ph in x["text"] else f"{x['text']} {tag(desc)}"
    return {**x, "text": text, "img": "", "ph": ""}


def unseen_images(lines: list[dict], limit: int) -> list[dict]:
    """还没看过的图（最近的在前），最多 limit 张。"""
    return [x for x in reversed(lines) if x.get("img")][:limit] if limit > 0 else []


def fmt(lines: list[dict], skip_id: str = "") -> str:
    """一行一条：[HH:MM] 谁：说了什么。skip_id 是正在叫它的那条（已经在对话里了，不重复）。"""
    out = []
    for x in lines:
        if skip_id and x["id"] == skip_id:
            continue
        if not x["text"]:
            continue
        out.append(f"[{dt.datetime.fromtimestamp(x['t']):%H:%M}] {x['who']}：{x['text']}")
    return "\n".join(out)
