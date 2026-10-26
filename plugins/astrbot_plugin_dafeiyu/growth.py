"""成长：对话日志、自我认知（self.md）、日记与快照。

都存在插件数据目录里（运行时私有，不进仓库）：
  growth/self.md               它对自己的认识，每晚反思时由它自己改写
  growth/self_history/*.md     每次改写前的快照，用于回滚
  growth/diary/YYYY-MM-DD.md   日记（它自己的，不提供查看指令）
  growth/log/YYYY-MM-DD.jsonl  当天和主人的对话流水，反思的原料
"""

import datetime as dt
import json
import time
from pathlib import Path

BLANK_SELF = "（还是一张白纸。我刚来到这里，还不太了解自己。）"


class Growth:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "growth"
        self.history = self.root / "self_history"
        self.diary_dir = self.root / "diary"
        self.log_dir = self.root / "log"
        for d in (self.history, self.diary_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.self_path = self.root / "self.md"
        if not self.self_path.exists():
            self.self_path.write_text(BLANK_SELF, "utf-8")

    # ---------- 自我认知 ----------

    def read_self(self) -> str:
        return self.self_path.read_text("utf-8").strip() or BLANK_SELF

    def write_self(self, text: str):
        """改写前先存快照。"""
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        (self.history / f"{stamp}.md").write_text(self.read_self(), "utf-8")
        self.self_path.write_text(text.strip(), "utf-8")

    def rollback(self) -> str | None:
        """回到上一个快照，返回快照名；没有可回的就返回 None。"""
        snaps = sorted(self.history.glob("*.md"))
        if not snaps:
            return None
        last = snaps[-1]
        self.self_path.write_text(last.read_text("utf-8"), "utf-8")
        last.unlink()
        return last.stem

    # ---------- 日记 ----------

    def write_diary(self, day: str, text: str):
        """同一天反思多次就接着往下写。"""
        p = self.diary_dir / f"{day}.md"
        old = p.read_text("utf-8").strip() if p.exists() else ""
        p.write_text((old + "\n\n" if old else "") + text.strip(), "utf-8")

    def read_diary(self, day: str = "") -> tuple[str, str] | None:
        """返回 (日期, 内容)；不给日期就取最新一篇。"""
        if day:
            p = self.diary_dir / f"{day}.md"
        else:
            files = sorted(self.diary_dir.glob("*.md"))
            p = files[-1] if files else None
        if not p or not p.exists():
            return None
        return p.stem, p.read_text("utf-8")

    # ---------- 对话流水 ----------

    def log(self, who: str, text: str):
        text = (text or "").strip()
        if not text:
            return
        day = dt.datetime.now().strftime("%Y-%m-%d")
        line = json.dumps({"t": time.time(), "who": who, "text": text[:500]}, ensure_ascii=False)
        with open(self.log_dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_log(self, day: str, start: int = 0, max_chars: int = 12000) -> tuple[str, int]:
        """读某天从第 start 行起的对话，返回 (文本, 总行数)。"""
        p = self.log_dir / f"{day}.jsonl"
        if not p.exists():
            return "", 0
        raws = p.read_text("utf-8").splitlines()
        lines = []
        for raw in raws[start:]:
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            hm = dt.datetime.fromtimestamp(m["t"]).strftime("%H:%M")
            lines.append(f"[{hm}] {m['who']}：{m['text']}")
        return "\n".join(lines)[-max_chars:], len(raws)  # 太长只留后半段
