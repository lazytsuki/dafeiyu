"""书架：插件数据目录下的 books/ 里放着的书（.txt / .md），它想读的时候读一段，记住读到哪。

书由 TA 放进去（公版书、TA 想给它看的东西都行）；读哪本、读不读、读多快都由它。
读到哪存在 state.json 的 "reading"：{文件名: {"pos": 读到第几个字, "t": 上次读的时间}}。
这里只放纯逻辑（找书、读文件、取一段），存取在 main.py。
"""

from pathlib import Path

EXTS = (".txt", ".md")
PAGE_CHARS = 2000  # 一次读这么多字
MAX_BYTES = 20 * 1024 * 1024


def shelf(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in EXTS)


def title(p: Path) -> str:
    return p.stem


def load(p: Path) -> str:
    """读整本：先当 UTF-8，不是就当 GB18030（中文 txt 常见）。"""
    raw = p.read_bytes()[:MAX_BYTES]
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out, blank = [], False
    for ln in lines:  # 连着的空行并成一个
        if ln.strip():
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def find(books: list[Path], name: str) -> Path | None:
    """按书名找：完全一样的优先，其次包含；也认书架编号（从 1 开始）。"""
    name = str(name or "").strip().strip("《》「」\"'")
    if not name:
        return None
    if name.isdigit() and 1 <= int(name) <= len(books):
        return books[int(name) - 1]
    for p in books:
        if title(p) == name:
            return p
    hits = [p for p in books if name in title(p) or title(p) in name]
    return hits[0] if len(hits) == 1 else None


def page(text: str, pos: int, size: int = PAGE_CHARS) -> tuple[str, int]:
    """从 pos 起读一段，尽量在段落或句号处断开。返回 (这一段, 读完后的位置)。"""
    pos = max(0, min(int(pos or 0), len(text)))
    end = pos + size
    if end >= len(text):
        return text[pos:], len(text)
    cut = max(text.rfind("\n", pos + size // 2, end), text.rfind("。", pos + size // 2, end))
    end = cut + 1 if cut > 0 else end
    return text[pos:end], end


def percent(pos: int, total: int) -> int:
    return 100 if total <= 0 else min(100, int(pos * 100 / total))


def listing(books: list[Path], reading: dict, sizes: dict) -> str:
    """书架上的书和读到哪了。sizes：{文件名: 总字数}。"""
    lines = []
    for i, p in enumerate(books, 1):
        r = reading.get(p.name) or {}
        pos, total = int(r.get("pos", 0)), sizes.get(p.name, 0)
        if not pos:
            where = "还没读"
        elif pos >= total:
            where = "读完了"
        else:
            where = f"读到 {percent(pos, total)}%"
        lines.append(f"{i}. 《{title(p)}》（约 {total // 1000 or 1} 千字，{where}）")
    return "\n".join(lines)
