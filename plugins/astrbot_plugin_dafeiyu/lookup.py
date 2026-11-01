"""查资料：它好奇的时候，自己搜一搜、点开一个网页读一读。

搜索用必应中文版的网页结果（不要 Key，国内能打开）；打开网页走 links.py 的 _get：
一跳一跳跟跳转、每一跳都检查是不是公网地址，不去内网和本机。
读网页只取正文，分段给它（books.page），一次一段，想往下读就接着读。
"""

import re
from urllib.parse import quote, urlparse

import aiohttp

from .links import TIMEOUT, UA, LinkError, LOGIN_RE, _decode, _get

SEARCH = "https://cn.bing.com/search?q={q}&ensearch=0"
MAX_RESULTS = 6
PAGE_CHARS = 1500  # 读网页一次读这么多字（分段用 books.page）
SNIPPET = 120


class SearchError(Exception):
    pass


def parse_results(html: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not (a.get("href") or "").startswith("http"):
            continue
        p = li.select_one(".b_caption p") or li.select_one("p")
        snippet = re.sub(r"\s+", " ", p.get_text(" ", strip=True) if p else "").strip()
        out.append({"title": a.get_text(" ", strip=True), "url": a["href"], "snippet": snippet[:SNIPPET]})
        if len(out) >= MAX_RESULTS:
            break
    return out


async def search(query: str) -> list[dict]:
    """搜一下，返回 [{"title", "url", "snippet"}]；搜不了就抛 SearchError。"""
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as s:
            async with s.get(SEARCH.format(q=quote(query)), headers=headers) as r:
                if r.status >= 400:
                    raise SearchError(f"搜索打不开（{r.status}）")
                html = await r.text(errors="replace")
    except aiohttp.ClientError as e:
        raise SearchError(f"搜索打不开（{e.__class__.__name__}）") from e
    return parse_results(html)


def fmt_results(results: list[dict]) -> str:
    return "\n".join(
        f"[{i}] {r['title']}（{urlparse(r['url']).hostname}）\n    {r['snippet']}\n    {r['url']}"
        for i, r in enumerate(results, 1)
    )


def page_text(html: str) -> tuple[str, str]:
    """网页 → (标题, 正文)。正文按段落拼起来，去掉导航、脚本这些。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for t in soup(["script", "style", "nav", "header", "footer", "noscript", "aside", "form", "iframe"]):
        t.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    paras = []
    for el in root.find_all(["p", "h1", "h2", "h3", "li", "pre", "blockquote"]):
        if el.find(["p", "li"]):  # 外层容器：里面的段落会单独取到
            continue
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(t) >= 8 and (not paras or paras[-1] != t):
            paras.append(t)
    if not paras:  # 没有段落标签的网页：退回整页文字
        paras = [re.sub(r"\s+", " ", root.get_text(" ", strip=True))]
    return title, "\n".join(paras)


async def read(url: str) -> tuple[str, str]:
    """打开网页，返回 (标题, 正文)；打不开抛 LinkError。"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as s:
        final, ctype, body = await _get(s, url)
    if "html" not in ctype.lower() and not body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        if ctype.lower().startswith("text/"):
            return urlparse(final).path.rsplit("/", 1)[-1], _decode(body, ctype)
        raise LinkError(f"不是网页，是一个文件（{ctype.split(';')[0].strip() or '未知类型'}）")
    if LOGIN_RE.search(final):
        raise LinkError("跳到了登录页，要登录才看得到")
    title, text = page_text(_decode(body, ctype))
    if len(text.strip()) < 20:
        raise LinkError("网页里几乎没有文字，可能要登录或者要开脚本才看得到")
    return title, text

