"""它自己的表情包收藏：看到喜欢的就存下来，配一句自己写的描述；想用的时候按意思挑出来。

收藏什么、怎么描述、什么时候发，都是它自己的事。这里只管存和找。
  stickers/index.json   [{"file": "ab12….gif", "note": "它写的描述", "from": "TA"/"别人"/"我画的", "t": ts,
                          "seen": 看图模型给的客观描述（看过才有）}]
  stickers/<md5>.<ext>  图片本身
"""

import hashlib
import json
import time
from pathlib import Path

from .recall import _score, _grams

MAX_STICKERS = 300


def _ext(data: bytes) -> str:
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


class Stickers:
    def __init__(self, root: Path):
        self.dir = root / "stickers"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"

    def all(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text("utf-8"))
        except json.JSONDecodeError:
            return []

    def _save(self, items: list[dict]):
        self.index_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")

    def keep(self, data: bytes, note: str, source: str, seen: str = "") -> tuple[bool, str]:
        """存一张。返回 (是不是新的, 文件名)。同一张图再存一次就只更新描述。seen 是看图看出来的客观描述，可以没有。"""
        name = f"{hashlib.md5(data).hexdigest()}.{_ext(data)}"
        items = self.all()
        for it in items:
            if it["file"] == name:
                it["note"] = note
                if seen:
                    it["seen"] = seen
                self._save(items)
                return False, name
        (self.dir / name).write_bytes(data)
        items.append({"file": name, "note": note, "from": source, "t": time.time(), **({"seen": seen} if seen else {})})
        if len(items) > MAX_STICKERS:  # 太多了就把最早存的挪出去
            for old in items[: len(items) - MAX_STICKERS]:
                (self.dir / old["file"]).unlink(missing_ok=True)
            items = items[-MAX_STICKERS:]
        self._save(items)
        return True, name

    def find(self, query: str) -> dict | None:
        """按编号（从 1 开始）或按意思找一张最像的。"""
        items = self.all()
        q = query.strip()
        if q.isdigit():
            i = int(q) - 1
            return items[i] if 0 <= i < len(items) else None
        grams = _grams(q)
        scored = [(_score(grams, it["note"]), -n, it) for n, it in enumerate(items)]
        scored = [s for s in scored if s[0] > 0]
        if not scored:
            return None
        return max(scored, key=lambda s: (s[0], s[1]))[2]

    def listing(self, limit: int = 60, pick=None) -> str:
        """编号 + 描述，一行一张。pick 给了就只列它挑出来的那些（编号还是整个收藏里的编号）。"""
        lines = [f"{i}. {it['note']}" for i, it in enumerate(self.all(), 1) if pick is None or pick(it)]
        return "\n".join(lines)[-limit * 40 :]

    def number(self, file: str) -> int:
        """某个文件在收藏里的编号（从 1 开始），没有就是 0。"""
        for i, it in enumerate(self.all(), 1):
            if it["file"] == file:
                return i
        return 0

    def by_file(self, file: str) -> dict | None:
        """按文件名找。编号会因为挪走最早存的而变，草稿里记的是文件名。"""
        return next((it for it in self.all() if it["file"] == file), None)

    def path(self, item: dict) -> Path:
        return self.dir / item["file"]
