"""成长：对话流水、它对自己的认识、它和 TA 之间的事、日记与快照。

都存在插件数据目录里（运行时私有，不进仓库）：
  growth/self.md               我是谁——性格、说话的样子、喜好；对所有人都是这样的我
  growth/bond.md               我和 TA 之间——记得的事、TA 的偏好、我们的梗；只在和 TA 私聊时想起
  growth/self_history/*.json   每次改写前的快照（self + bond），用于回滚
  growth/diary/YYYY-MM-DD.md   日记（它自己的，不提供查看指令）
  growth/months/YYYY-MM.md     每月一次回想这个月（它自己的，同上）
  growth/log/YYYY-MM-DD.jsonl  当天和 TA 的对话流水，反思的原料（别人的对话不记）
"""

import datetime as dt
import json
import re
import time
from pathlib import Path

BLANK_SELF = "（还是一张白纸。我刚来到这里，还不太了解自己。）"
BLANK_BOND = "（我们才刚认识。）"


class Growth:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "growth"
        self.history = self.root / "self_history"
        self.diary_dir = self.root / "diary"
        self.log_dir = self.root / "log"
        self.month_dir = self.root / "months"
        for d in (self.history, self.diary_dir, self.log_dir, self.month_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.self_path = self.root / "self.md"
        self.bond_path = self.root / "bond.md"
        if not self.self_path.exists():
            self.self_path.write_text(BLANK_SELF, "utf-8")

    # ---------- 我是谁 / 我和 TA ----------

    def read_self(self) -> str:
        return self.self_path.read_text("utf-8").strip() or BLANK_SELF

    def has_bond(self) -> bool:
        return self.bond_path.exists()

    def read_bond(self) -> str:
        if not self.bond_path.exists():
            return BLANK_BOND
        return self.bond_path.read_text("utf-8").strip() or BLANK_BOND

    def write(self, self_text: str, bond_text: str):
        """改写前先把两份一起存快照。"""
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        snap = {"self": self.read_self(), "bond": self.read_bond() if self.has_bond() else None}
        (self.history / f"{stamp}.json").write_text(json.dumps(snap, ensure_ascii=False), "utf-8")
        self.self_path.write_text(self_text.strip() or BLANK_SELF, "utf-8")
        self.bond_path.write_text(bond_text.strip() or BLANK_BOND, "utf-8")

    def rollback(self) -> str | None:
        """回到上一个快照，返回快照名；没有可回的就返回 None。"""
        snaps = sorted(self.history.glob("*.*"))
        if not snaps:
            return None
        last = snaps[-1]
        if last.suffix == ".json":
            snap = json.loads(last.read_text("utf-8"))
            self.self_path.write_text(snap["self"], "utf-8")
            if snap.get("bond") is None:
                self.bond_path.unlink(missing_ok=True)
            else:
                self.bond_path.write_text(snap["bond"], "utf-8")
        else:  # 旧版只存了 self 的 .md 快照
            self.self_path.write_text(last.read_text("utf-8"), "utf-8")
        last.unlink()
        return last.stem

    def version_at(self, day: dt.date) -> tuple[str, str, str | None] | None:
        """那天结束时的两份认识 → (哪天写下的, self, bond 或 None)。

        快照是改写「之前」存的：那天之后的第一个快照，就是那天的样子；之后再没改过，就是现在的样子。
        比最早的快照还早，就没有那时的记录（返回 None）。
        """
        snaps = sorted(self.history.glob("*.*"))
        cut = (day + dt.timedelta(days=1)).strftime("%Y%m%d")
        after = [p for p in snaps if p.stem >= cut]
        if not after:
            return "现在", self.read_self(), self.read_bond() if self.has_bond() else None
        if after[0] == snaps[0] and day < self.first_seen():
            return None
        p = after[0]
        if p.suffix == ".json":
            snap = json.loads(p.read_text("utf-8"))
            return self._since(snaps, p), snap["self"], snap.get("bond")
        return self._since(snaps, p), p.read_text("utf-8"), None  # 旧版只存了 self

    def _since(self, snaps: list[Path], p: Path) -> str:
        """快照 p 里的那一版是从哪天开始的：上一个快照的时间（没有上一个就说不清）。"""
        i = snaps.index(p)
        if i == 0:
            return "最早能找到的"
        s = snaps[i - 1].stem[:8]
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"

    def first_seen(self) -> dt.date:
        """最早有记录的那天（对话流水、日记里最早的一天）。"""
        days = [p.stem for p in list(self.log_dir.glob("*.jsonl")) + list(self.diary_dir.glob("*.md"))]
        try:
            return dt.date.fromisoformat(min(days)) if days else dt.date.today()
        except ValueError:
            return dt.date.today()

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

    def month_diaries(self, month: str, max_chars: int = 24000) -> str:
        """某个月（YYYY-MM）的全部日记，按日期排；太长只留每篇的前面。"""
        files = sorted(self.diary_dir.glob(f"{month}-*.md"))
        if not files:
            return ""
        per = max(300, max_chars // len(files))
        return "\n\n".join(f"【{p.stem}】\n{p.read_text('utf-8').strip()[:per]}" for p in files)

    def write_month(self, month: str, text: str):
        (self.month_dir / f"{month}.md").write_text(text.strip(), "utf-8")

    def read_month(self, month: str = "") -> tuple[str, str] | None:
        """返回 (月份, 内容)；不给月份就取最近一篇。"""
        if month:
            p = self.month_dir / f"{month}.md"
        else:
            files = sorted(self.month_dir.glob("*.md"))
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

    def relabel_log(self, old: str, new: str):
        """改对话流水里的说话人标签（只改标签，不动内容）。"""
        for f in self.log_dir.glob("*.jsonl"):
            out = []
            for raw in f.read_text("utf-8").splitlines():
                try:
                    m = json.loads(raw)
                except json.JSONDecodeError:
                    out.append(raw)
                    continue
                if m.get("who") == old:
                    m["who"] = new
                out.append(json.dumps(m, ensure_ascii=False))
            f.write_text("\n".join(out) + "\n", "utf-8")

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


_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}


def parse_back(text: str, today: dt.date) -> dt.date | None:
    """「一个月前」「3 天前」「两周前」「上个月」「2026-10-28」「10-28」→ 那一天；认不出来返回 None。"""
    s = str(text or "").strip().replace(" ", "").replace("/", "-")
    if not s:
        return None
    if s in ("上个月", "上月"):
        return today.replace(day=1) - dt.timedelta(days=1)
    if s in ("昨天",):
        return today - dt.timedelta(days=1)
    if s in ("前天",):
        return today - dt.timedelta(days=2)
    if s in ("上周", "上星期", "上礼拜"):
        return today - dt.timedelta(days=7)
    m = re.match(r"^(\d+|[一两二三四五六七八九十半])个?(天|日|周|星期|礼拜|月)(以?前)?$", s)
    if m:
        n = float(m.group(1)) if m.group(1).isdigit() else _CN_NUM[m.group(1)]
        unit = {"天": 1, "日": 1, "周": 7, "星期": 7, "礼拜": 7, "月": 30}[m.group(2)]
        return today - dt.timedelta(days=int(n * unit))
    try:
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", s):
            return dt.date(*map(int, s.split("-")))
        if re.match(r"^\d{4}-\d{1,2}$", s):  # 某个月：看那个月月底的样子
            y, mo = map(int, s.split("-"))
            nxt = dt.date(y + mo // 12, mo % 12 + 1, 1)
            return min(nxt - dt.timedelta(days=1), today)
        if re.match(r"^\d{1,2}-\d{1,2}$", s):
            d = dt.date(today.year, *map(int, s.split("-")))
            return d if d <= today else d.replace(year=today.year - 1)
    except ValueError:
        return None
    return None
