"""翻原话：在它和 TA 的对话流水和它自己的日记里找线索。

bond.md 只记得「重要的事」，细节会被压掉。想不起来的时候，它可以自己回去翻原始记录——
找到的是原话和日期，不是摘要。中文不分词：按两个字一组（二元组）算重合，够用、无依赖。
"""

import datetime as dt
import json
import re
from pathlib import Path

_SPLIT = re.compile(r"[\s,，。.!！?？、;；:：\"“”'‘’()（）\[\]【】<>《》~～…-]+")


def _grams(text: str) -> set[str]:
    grams = set()
    for word in _SPLIT.split(text.lower()):
        if not word:
            continue
        if len(word) == 1 or word.isascii():
            grams.add(word)
        else:
            grams.update(word[i : i + 2] for i in range(len(word) - 1))
    return grams


def _score(query: set[str], text: str) -> float:
    if not query:
        return 0.0
    hit = query & _grams(text)
    return len(hit) / len(query)


def search(root: Path, query: str, limit: int = 8, context: int = 1) -> list[str]:
    """在 growth/log/*.jsonl（对话）和 growth/diary/*.md（日记）里找和 query 最像的地方。

    返回若干段文字，每段带日期，对话会带上前后各 context 句。
    """
    q = _grams(query)
    hits: list[tuple[float, str, str]] = []  # (分数, 排序键, 文本)

    for f in sorted((root / "log").glob("*.jsonl")):
        rows = []
        for raw in f.read_text("utf-8").splitlines():
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        for i, m in enumerate(rows):
            s = _score(q, m.get("text", ""))
            if s < 0.34:
                continue
            seg = rows[max(0, i - context) : i + context + 1]
            when = dt.datetime.fromtimestamp(m["t"])
            body = "\n".join(
                f"  [{dt.datetime.fromtimestamp(x['t']):%H:%M}] {x['who']}：{x['text'][:200]}" for x in seg
            )
            hits.append((s, f"{when:%Y-%m-%d %H:%M}", f"{when:%Y-%m-%d} 的聊天：\n{body}"))

    for f in sorted((root / "diary").glob("*.md")):
        for para in f.read_text("utf-8").split("\n\n"):
            s = _score(q, para)
            if s >= 0.34:
                hits.append((s, f.stem, f"{f.stem} 的日记：\n  {para.strip()[:300]}"))

    hits.sort(key=lambda h: (-h[0], h[1]))
    picked, seen = [], set()
    for _, key, text in hits:
        if text in seen:
            continue
        seen.add(text)
        picked.append((key, text))
        if len(picked) >= limit:
            break
    return [text for _, text in sorted(picked)]  # 按时间先后给它看
