"""打开链接：对方发来的网址、分享卡片，先去看一眼标题和摘要，写成「[链接：……]」接进消息里。

只写网页自己说的（标题、简介、作者），不评价、不替它决定要不要点进去看。
B 站视频走公开接口拿标题、UP 主、时长、简介；别的网页读 <title> 和 description。
需要登录才看得到的（微博、小红书……）多半只拿得到一个标题，拿不到就说打不开。
不碰内网和本机地址：群里谁都能发链接，不能让它替人去敲本机的后台。
"""

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_BYTES = 1024 * 1024  # 网页最多读这么多
MAX_HOPS = 5  # 最多跟几次跳转（b23.tv 这样的短链）
MAX_SUMMARY = 200  # 摘要最长这么多字
TIMEOUT = 10

URL_RE = re.compile(r"(?:https?://|(?<![A-Za-z0-9.])(?=b23\.tv/))[^\s<>\"'，。！？、）)】\]]+", re.I)
LOGIN_RE = re.compile(r"passport\.|/login|/signin|visitor", re.I)  # 跳到这种地址就是要登录
BV_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b")


class LinkError(Exception):
    pass


def find_urls(text: str) -> list[str]:
    """文字里的网址（去重，保持顺序）。b23.tv 短链没写 https:// 的也认。"""
    out = []
    for m in URL_RE.findall(text or ""):
        u = m if m.lower().startswith("http") else "https://" + m
        if u not in out:
            out.append(u)
    return out


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_card(raw) -> dict:
    """QQ 的分享卡片（json 消息段）→ {"app": 来自哪, "title", "desc", "url"}，认不出来的字段就是空串。

    小程序卡片（B 站、知乎……）在 meta.detail_1 里：title 是应用名，desc 是内容标题，qqdocurl 是链接；
    普通链接卡片在 meta.news 里：title、desc、jumpUrl、tag（来源）。
    """
    try:
        card = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    if not isinstance(card, dict):
        return {}
    meta = card.get("meta") or {}
    d1 = meta.get("detail_1") if isinstance(meta, dict) else None
    if isinstance(d1, dict):
        return {
            "app": str(d1.get("title") or ""),
            "title": str(d1.get("desc") or ""),
            "desc": "",
            "url": str(d1.get("qqdocurl") or d1.get("url") or ""),
        }
    for node in _walk(meta):
        url = node.get("jumpUrl") or node.get("qqdocurl") or node.get("url") or ""
        title = node.get("title") or ""
        if title or (isinstance(url, str) and url.startswith("http")):
            return {
                "app": str(node.get("tag") or node.get("source_name") or ""),
                "title": str(title),
                "desc": str(node.get("desc") or ""),
                "url": str(url) if str(url).startswith("http") else "",
            }
    prompt = str(card.get("prompt") or "")
    return {"app": "", "title": prompt, "desc": "", "url": ""} if prompt else {}


def card_text(card: dict) -> str:
    """不打开链接也能说的：卡片自己写着的标题和来源。"""
    if not card:
        return "[分享了一张卡片，看不出是什么]"
    app = f"{card['app']}的" if card.get("app") else ""
    title = f"「{card['title']}」" if card.get("title") else ""
    desc = f"：{card['desc'][:80]}" if card.get("desc") and card["desc"] != card.get("title") else ""
    return f"[分享了一张{app}卡片{title}{desc}]"


async def _public(host: str) -> bool:
    """主机名解析出来的地址全都是公网的才去。"""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addrs = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos}
    return bool(addrs) and all(a.is_global for a in addrs)


async def _get(s: aiohttp.ClientSession, url: str) -> tuple[str, str, bytes]:
    """一跳一跳地跟跳转，每一跳都检查是不是公网地址。返回 (最终地址, Content-Type, 内容)。"""
    for _ in range(MAX_HOPS + 1):
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise LinkError("不是网页链接")
        if not await _public(p.hostname):
            raise LinkError("内网或本机地址，不去")
        async with s.get(url, allow_redirects=False, headers={"User-Agent": UA}) as r:
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                continue
            if r.status >= 400:
                raise LinkError(f"打开失败（{r.status}）")
            body = await r.content.read(MAX_BYTES)
            return url, r.headers.get("Content-Type", ""), body
    raise LinkError("跳转太多次")


async def _bilibili(s: aiohttp.ClientSession, bvid: str) -> str:
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    async with s.get(api, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}) as r:
        data = (await r.json(content_type=None)).get("data") or {}
    if not data.get("title"):
        raise LinkError("B 站视频没拿到信息")
    mins, secs = divmod(int(data.get("duration") or 0), 60)
    up = (data.get("owner") or {}).get("name", "")
    desc = re.sub(r"\s+", " ", data.get("desc") or "").strip()
    desc = "" if desc in ("-", "") else f"；简介：{desc[:MAX_SUMMARY]}"
    return f"B 站视频「{data['title']}」，UP 主 {up}，{mins} 分 {secs:02d} 秒{desc}"


def _decode(body: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype, re.I) or re.search(rb'charset=["\']?([\w-]+)', body[:2048], re.I)
    enc = m.group(1) if m else "utf-8"
    enc = enc.decode() if isinstance(enc, bytes) else enc
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def page_summary(html: str, url: str) -> str:
    """网页自己写的标题和简介；没有简介就取正文开头一段。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    meta = lambda *names: next(
        (
            (tag.get("content") or "").strip()
            for n in names
            for tag in [soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})]
            if tag and (tag.get("content") or "").strip()
        ),
        "",
    )
    title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "")
    desc = meta("og:description", "description", "twitter:description")
    if not desc:
        for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            t.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        desc = " ".join(p for p in paras if len(p) > 20)
    desc = re.sub(r"\s+", " ", desc).strip()[:MAX_SUMMARY]
    site = urlparse(url).hostname or ""
    if not title and not desc:
        return ""
    return f"网页「{title or site}」（{site}）" + (f"：{desc}" if desc else "")


async def summarize(url: str) -> str:
    """打开一个链接，返回一句话的内容说明；打不开就抛 LinkError（调用方写成「打不开」）。"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as s:
        bv = BV_RE.search(url)
        if bv and "bilibili.com" in url:
            return await _bilibili(s, bv.group(1))
        final, ctype, body = await _get(s, url)
        bv = BV_RE.search(final)
        if bv and "bilibili.com" in final:
            return await _bilibili(s, bv.group(1))
        if "html" not in ctype.lower() and not body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
            kind = ctype.split(";")[0].strip() or "未知类型"
            return f"不是网页，是一个文件（{kind}）"
        if LOGIN_RE.search(final):
            raise LinkError("跳到了登录页，要登录才看得到")
        out = page_summary(_decode(body, ctype), final)
        if not out:
            raise LinkError("网页里没写标题和内容，可能要登录才看得到")
        return out


def tag(url: str, summary: str) -> str:
    host = urlparse(url).hostname or url
    return f" [链接 {host}：{summary}]" if summary else f" [链接 {host}：打不开，不知道里面是什么]"
