"""翻自己做过的东西：发过的说说、画过的图、换过的签名、在空间和微博上留下的话。

都是现成的记录（对话流水里它自己那几类、表情包收藏里「我画的」），这里只负责找出来、按时间列好。
做得好不好、要不要再做，由它自己看了再想。
"""

import datetime as dt
import json
from pathlib import Path

from .recall import _grams, _score

# 对话流水里算「作品」的那几类（说话人标签里含这些字）
WORK_MARKS = ("发了", "画了", "换了签名", "改了", "评论")
PUBLIC_MARKS = ("说说", "签名", "微博", "评论")  # 别人也看得到的：在和别人聊天时也能翻
MAX_TEXT = 160


def _is_work(who: str) -> bool:
    return who.startswith("我（") and any(m in who for m in WORK_MARKS)


def from_log(log_dir: Path) -> list[dict]:
    """对话流水里它做过的事 → [{"t", "what": 标签里括号中的那句, "text"}]，按时间排。"""
    out = []
    for f in sorted(log_dir.glob("*.jsonl")):
        for raw in f.read_text("utf-8").splitlines():
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            who = m.get("who", "")
            if _is_work(who) and "画了一张图" not in who:  # 画的图从收藏里列，带编号
                out.append({"t": m["t"], "what": who[2:].rstrip("）"), "text": m.get("text", "")})
    return out


def drawings(items: list[dict]) -> list[dict]:
    """表情包收藏里它自己画的 → [{"t", "what", "text", "n": 收藏编号}]。"""
    out = []
    for n, it in enumerate(items, 1):
        if it.get("from") != "我画的":
            continue
        text = it.get("note", "") + (f"（画出来的样子：{it['seen']}）" if it.get("seen") else "")
        out.append({"t": it.get("t", 0), "what": f"画了一张图，收藏编号 {n}", "text": text, "n": n})
    return out


def pick(works: list[dict], query: str = "", public_only: bool = False, limit: int = 15) -> list[dict]:
    """按意思找（不给 query 就是最近的），结果按时间从早到晚。"""
    if public_only:
        works = [w for w in works if any(m in w["what"] for m in PUBLIC_MARKS)]
    q = _grams(query.strip())
    if q:
        scored = [(s, w) for w in works if (s := _score(q, w["what"] + w["text"])) > 0]
        works = [w for _, w in sorted(scored, key=lambda x: (-x[0], -x[1]["t"]))[:limit]]
    else:
        works = sorted(works, key=lambda w: w["t"])[-limit:]
    return sorted(works, key=lambda w: w["t"])


def fmt(works: list[dict]) -> str:
    lines = []
    for w in works:
        text = " ".join(w["text"].split())
        text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "……"
        lines.append(f"[{dt.datetime.fromtimestamp(w['t']):%Y-%m-%d %H:%M}] {w['what']}：{text}")
    return "\n".join(lines)
