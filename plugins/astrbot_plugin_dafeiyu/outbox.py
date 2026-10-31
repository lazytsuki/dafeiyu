"""它自己发起的时刻（主动私聊、上线补回）要发的东西：把模型的输出拆成一段段文字 / 语音 / 表情包。

某一行写成「[语音] 要说的话」或「[表情包] 编号或描述」就是语音 / 表情包，其余都是文字。
这里只管拆，发不发得出去由 main.py 管（护栏、退回文字都在那边）。
"""

import re

# 行首的标记：[语音] / 【语音】，后面可以带冒号
MARK = re.compile(r"^\s*[\[【]\s*(语音|表情包)\s*[\]】]\s*[:：]?\s*(.*)$")
KINDS = {"语音": "voice", "表情包": "sticker"}


def split_out(text: str, split: bool = True) -> list[tuple[str, str]]:
    """拆成 [(类型, 内容)]，类型是 text / voice / sticker。

    split=True：每行文字单独一段（「|||」也算换行）；False：相邻的文字行合成一段，原样保留换行。
    标记后面什么都没写的行直接丢掉。
    """
    lines = re.split(r"\n|\|\|\|", text) if split else text.split("\n")
    out: list[tuple[str, str]] = []
    buf: list[str] = []  # split=False 时攒着的文字行

    def flush():
        joined = "\n".join(buf).strip()
        if joined:
            out.append(("text", joined))
        buf.clear()

    for line in lines:
        m = MARK.match(line)
        if m:
            flush()
            if m.group(2).strip():
                out.append((KINDS[m.group(1)], m.group(2).strip()))
        elif split:
            if line.strip():
                out.append(("text", line.strip()))
        else:
            buf.append(line)
    flush()
    return out


# 说说正文里单独一行「[配图] 编号」：发说说时配上收藏里的这张图
PIC_MARK = re.compile(r"^\s*[\[【]\s*配图\s*[\]】]\s*[:：]?\s*(.*)$")


def take_pic(text: str) -> tuple[str, str]:
    """把「[配图] …」那一行从说说正文里拿出来：返回 (正文, 配图编号或描述)，没写就是空串。只认第一行配图。"""
    keep, pic = [], ""
    for line in text.split("\n"):
        m = PIC_MARK.match(line)
        if m:
            pic = pic or m.group(1).strip()
        else:
            keep.append(line)
    return "\n".join(keep).strip(), pic
