"""QQ 空间说说的最小客户端。

登录态不存密码也不手抓 Cookie：每次通过 NapCat 的 OneBot 扩展接口
get_cookies(domain="qzone.qq.com") 现取，过期了重取即可。
"""

import base64
import json
import re
from typing import Awaitable, Callable
from urllib.parse import unquote

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
# 点赞（参数照开源实现 Maizone、qzone_ultra）
LIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
# 说说配图：先把图传到相册，拿 pic_bo / richval 再随说说一起发（参数照开源实现 Maizone、qzone_ultra）
UPLOAD_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"


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


def _pic_bo(url: str) -> str:
    """上传返回的图片地址里带着 bo=…，发说说时要原样带上。"""
    if "bo=" not in url:
        return ""
    bo = url.split("bo=", 1)[1]
    for sep in ("!!", "&", "#"):
        bo = bo.split(sep, 1)[0]
    return unquote(bo)


def parse_upload(body: dict) -> tuple[str, str]:
    """从上传图片的返回里拼出 (pic_bo, richval)。上传失败或缺字段就抛 QzoneError。"""
    if body.get("ret", 0) != 0:
        raise QzoneError(f"图片上传失败：{body.get('msg') or body}"[:200])
    d = body.get("data") if isinstance(body.get("data"), dict) else body
    albumid, lloc, sloc = (str(d.get(k) or "") for k in ("albumid", "lloc", "sloc"))
    if not (albumid and lloc and sloc):
        raise QzoneError(f"图片上传的返回缺字段：{str(d)[:200]}")
    h, w, kind = d.get("height") or 0, d.get("width") or 0, d.get("type") or 0
    bo = str(d.get("pic_bo") or "") or _pic_bo(str(d.get("url") or ""))
    return bo, f",{albumid},{lloc},{sloc},{kind},{h},{w},,{h},{w}"


class QzoneClient:
    def __init__(self, uin: str, cookie_getter: Callable[[], Awaitable[str]]):
        self.uin = str(uin)
        self._get_cookie = cookie_getter

    async def _cookies(self) -> tuple[str, dict[str, str]]:
        raw = await self._get_cookie()
        cookies = parse_cookies(raw)
        if not (cookies.get("p_skey") or cookies.get("skey")):
            raise QzoneError("Cookie 里没有 p_skey/skey，NapCat 登录态可能失效")
        return raw, cookies

    async def _request(
        self, method: str, url: str, params: dict | None = None, data: dict | None = None,
        cookie: tuple[str, dict[str, str]] | None = None, timeout_s: int = 20,
    ) -> dict:
        raw, cookies = cookie or await self._cookies()
        key = cookies.get("p_skey") or cookies.get("skey")
        headers = {
            "User-Agent": UA,
            "Cookie": raw,
            "Referer": f"https://user.qzone.qq.com/{self.uin}",
            "Origin": "https://user.qzone.qq.com",
        }
        params = {"g_tk": calc_gtk(key), "uin": self.uin, **(params or {})}
        timeout = aiohttp.ClientTimeout(total=timeout_s)
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

    async def like(self, host_uin: str, tid: str, post_time: int) -> None:
        """给某条说说点个赞。"""
        key = f"http://user.qzone.qq.com/{host_uin}/mood/{tid}"
        await self._post(
            LIKE_URL,
            {
                "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
                "opuin": self.uin,
                "unikey": key,
                "curkey": key,
                "from": "1",
                "appid": "311",
                "typeid": "0",
                "abstime": str(int(post_time)),
                "fid": str(tid),
                "active": "0",
                "fupdate": "1",
                "format": "json",
            },
        )

    async def upload_image(self, data: bytes) -> tuple[str, str]:
        """把一张图传到空间相册（说说配图用），返回 (pic_bo, richval)。"""
        cookie = await self._cookies()
        cookies = cookie[1]
        p_skey = cookies.get("p_skey") or cookies.get("skey") or ""
        body = await self._request(
            "POST",
            UPLOAD_URL,
            data={
                "filename": "filename",
                "zzpanelkey": "",
                "uploadtype": "1",
                "albumtype": "7",
                "exttype": "0",
                "skey": cookies.get("skey") or p_skey,
                "zzpaneluin": self.uin,
                "p_uin": self.uin,
                "uin": self.uin,
                "p_skey": p_skey,
                "output_type": "json",
                "qzonetoken": "",
                "refer": "shuoshuo",
                "charset": "utf-8",
                "output_charset": "utf-8",
                "upload_hd": "1",
                "hd_width": "2048",
                "hd_height": "10000",
                "hd_quality": "96",
                "backUrls": "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,"
                "http://119.147.64.75/cgi-bin/upload/cgi_upload_image",
                "url": f"{UPLOAD_URL}?g_tk={calc_gtk(p_skey)}",
                "base64": "1",
                "picfile": base64.b64encode(data).decode("ascii"),
            },
            cookie=cookie,
            timeout_s=60,
        )
        return parse_upload(body)

    async def publish(self, text: str, image: bytes | None = None) -> str:
        """发一条说说，返回 tid。给了 image 就先上传，作为配图一起发（上传失败会抛错，由调用方决定退回纯文字）。"""
        data = {
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
        }
        if image:
            bo, richval = await self.upload_image(image)
            data.update(
                {"richtype": "1", "subrichtype": "1", "richval": richval, "pic_bo": bo, "pic_template": ""}
            )
        body = await self._post(PUBLISH_URL, data)
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
