"""QQ 空间说说的最小客户端。

登录态不存密码也不手抓 Cookie：每次通过 NapCat 的 OneBot 扩展接口
get_cookies(domain="qzone.qq.com") 现取，过期了重取即可。
"""

import json
import re
from typing import Awaitable, Callable

import aiohttp

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
PUBLISH_URL = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com"
    "/cgi-bin/emotion_cgi_publish_v6"
)
DELETE_URL = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com"
    "/cgi-bin/emotion_cgi_delete_v6"
)
LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
COMMENT_URL = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com"
    "/cgi-bin/emotion_cgi_re_feeds"
)


class QzoneError(Exception):
    pass


def calc_gtk(skey: str) -> int:
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


def parse_cookies(raw: str) -> dict[str, str]:
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            out[k] = v
    return out


def _parse_resp(text: str) -> dict:
    # 接口有时返回 JSON，有时包一层 callback(...)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise QzoneError(f"无法解析返回：{text[:200]}")
        return json.loads(m.group(0))


class QzoneClient:
    def __init__(self, uin: str, cookie_getter: Callable[[], Awaitable[str]]):
        self.uin = str(uin)
        self._get_cookie = cookie_getter

    async def _request(self, method: str, url: str, params: dict | None = None, data: dict | None = None) -> dict:
        raw = await self._get_cookie()
        cookies = parse_cookies(raw)
        key = cookies.get("p_skey") or cookies.get("skey")
        if not key:
            raise QzoneError("Cookie 里没有 p_skey/skey，NapCat 登录态可能失效")
        headers = {
            "User-Agent": UA,
            "Cookie": raw,
            "Referer": f"https://user.qzone.qq.com/{self.uin}",
            "Origin": "https://user.qzone.qq.com",
        }
        params = {"g_tk": calc_gtk(key), "uin": self.uin, **(params or {})}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.request(method, url, params=params, data=data, headers=headers) as r:
                body = _parse_resp(await r.text())
        if body.get("code", 0) != 0:
            raise QzoneError(f"QQ 空间返回错误：{body.get('message') or body}")
        return body

    async def _post(self, url: str, data: dict) -> dict:
        return await self._request("POST", url, data=data)

    async def list_posts(self, host_uin: str, num: int = 10) -> list[dict]:
        """某人（可以是自己）最近的说说，带评论：
        [{"tid", "text", "time", "comments": [{"id", "uin", "name", "text", "time"}]}]
        看不了（不是好友、设了权限）会抛 QzoneError。"""
        body = await self._request(
            "GET",
            LIST_URL,
            params={
                "uin": str(host_uin),
                "hostUin": str(host_uin),
                "ftype": "0",
                "sort": "0",
                "pos": "0",
                "num": str(num),
                "replynum": "100",
                "code_version": "1",
                "format": "json",
                "need_private_comment": "1",
            },
        )
        posts = []
        for m in body.get("msglist") or []:
            comments = []
            for c in m.get("commentlist") or []:
                comments.append(
                    {
                        "id": str(c.get("tid", "")),
                        "uin": str(c.get("uin", "")),
                        "name": c.get("name", ""),
                        "text": c.get("content", ""),
                        "time": int(c.get("create_time", 0)),
                    }
                )
                for r in c.get("list_3") or []:  # 楼中楼
                    comments.append(
                        {
                            "id": f"{c.get('tid', '')}-{r.get('tid', '')}",
                            "uin": str(r.get("uin", "")),
                            "name": r.get("name", ""),
                            "text": r.get("content", ""),
                            "time": int(r.get("create_time", 0)),
                        }
                    )
            posts.append(
                {
                    "tid": str(m.get("tid", "")),
                    "text": m.get("content", ""),
                    "time": int(m.get("created_time", 0)),
                    "pics": int(m.get("pictotal", 0) or 0),
                    "comments": comments,
                }
            )
        return posts

    async def comment(self, host_uin: str, tid: str, text: str) -> None:
        """在某条说说下面发一条评论（所有能看到这条说说的人都看得到）。"""
        await self._post(
            COMMENT_URL,
            {
                "topicId": f"{host_uin}_{tid}__1",
                "feedsType": "100",
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "plat": "qzone",
                "source": "ic",
                "hostUin": str(host_uin),
                "isSignIn": "",
                "platformid": "52",
                "format": "json",
                "ref": "feeds",
                "content": text,
                "richval": "",
                "richtype": "",
                "private": "0",
                "paramstr": "1",
                "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
            },
        )

    async def publish(self, text: str) -> str:
        """发一条纯文字说说，返回 tid。"""
        body = await self._post(
            PUBLISH_URL,
            {
                "syn_tweet_verson": "1",
                "paramstr": "1",
                "who": "1",
                "con": text,
                "feedversion": "1",
                "ver": "1",
                "ugc_right": "1",
                "to_sign": "0",
                "hostuin": self.uin,
                "code_version": "1",
                "format": "json",
                "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
            },
        )
        return str(body.get("tid", ""))

    async def delete(self, tid: str) -> None:
        await self._post(
            DELETE_URL,
            {
                "hostuin": self.uin,
                "tid": tid,
                "t1_source": "1",
                "code_version": "1",
                "format": "json",
                "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
            },
        )
