"""转发的聊天记录和文本文件：把里面写了什么读出来，接在「[转发了一组聊天记录]」「[发来一个文件：…]」后面。

转发：NapCat 的 forward 消息段有时直接带着 content，没有就用 get_forward_msg 取。
文件：只读纯文本类的小文件（txt、md、csv、代码……），别的照旧只知道文件名。
都只是把原文摆出来，太长就截断并说明截断了。
"""

import aiohttp

from .senses import plain_text

MAX_FORWARD_LINES = 40
MAX_FORWARD_CHARS = 2000
MAX_FILE_BYTES = 256 * 1024
MAX_FILE_CHARS = 2000
TEXT_EXT = {
    "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "toml", "ini", "log",
    "py", "js", "ts", "html", "css", "java", "c", "cpp", "h", "go", "rs", "sh", "bat", "ps1", "sql", "srt", "lrc",
}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"……（后面还有 {len(text) - limit} 字没放进来）"


def forward_lines(nodes: list, depth: int = 0) -> list[str]:
    """转发里的每一条 → 「昵称：内容」。转发里套着的转发也展开一层。"""
    out = []
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        d = n.get("data") if n.get("type") == "node" else n
        d = d if isinstance(d, dict) else {}
        sender = d.get("sender") or {}
        who = sender.get("card") or sender.get("nickname") or d.get("nickname") or d.get("name") or "某人"
        segs = d.get("message") or d.get("content") or []
        inner = [s for s in segs if isinstance(s, dict) and s.get("type") == "forward"] if isinstance(segs, list) else []
        text = plain_text(segs) if isinstance(segs, list) else str(segs)
        out.append(f"{who}：{text}")
        if depth < 1:
            for f in inner:
                content = (f.get("data") or {}).get("content")
                if isinstance(content, list):
                    out += ["  " + x for x in forward_lines(content, depth + 1)]
    return out


def forward_text(nodes: list) -> str:
    lines = forward_lines(nodes)
    if not lines:
        return ""
    more = len(lines) - MAX_FORWARD_LINES
    body = "\n".join(lines[:MAX_FORWARD_LINES]) + (f"\n……（后面还有 {more} 条）" if more > 0 else "")
    return f" [转发的聊天记录：\n{_cut(body, MAX_FORWARD_CHARS)}\n]"


async def fetch_forward(get_bot, seg_data: dict) -> list:
    """forward 消息段 → 里面的节点。content 现成就直接用，否则问 NapCat（get_bot() 拿 OneBot 客户端）。"""
    content = seg_data.get("content")
    if isinstance(content, list) and content:
        return content
    fid = seg_data.get("id")
    if not fid:
        return []
    ret = await get_bot().api.call_action("get_forward_msg", message_id=str(fid))
    return (ret or {}).get("messages") or (ret or {}).get("message") or []


def is_text_file(name: str) -> bool:
    return "." in (name or "") and name.rsplit(".", 1)[1].lower() in TEXT_EXT


async def read_text_file(url: str, name: str) -> str:
    """下载一个小文本文件，返回「[文件 xx 的内容：…]」；太大、不是文本、读不了都返回空串。"""
    if not url.startswith("http"):
        return ""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.get(url) as r:
            if r.status >= 400:
                return ""
            size = int(r.headers.get("Content-Length") or 0)
            if size > MAX_FILE_BYTES:
                return f" [文件 {name} 有 {size // 1024} KB，太大了没打开]"
            data = await r.content.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        return f" [文件 {name} 太大了没打开]"
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ""
    if "\x00" in text:
        return ""
    return f" [文件 {name} 的内容：\n{_cut(text.strip(), MAX_FILE_CHARS)}\n]"
