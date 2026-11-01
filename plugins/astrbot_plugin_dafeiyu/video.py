"""看视频：把一个 B 站视频真的看一遍——截几张画面、听一段声音、读弹幕和热评。

全都不用登录、不用 ffmpeg：
- 画面：B 站的「视频快照」接口（/x/player/videoshot）给一张张 10×10 的缩略图拼图（每格 480×270），
  按时间均匀挑几格切出来，拼成一张图，用看图模型（vision.py）一次看完。
- 声音：playurl 的 dash 音频流（最低码率那一路，m4a），只下前几分钟，交给硅基流动的语音转文字
  （和 AstrBot 里配的语音转文字同一个模型）。转写是机器听的字，可能有错，音乐会标成 🎼。
- 弹幕：comment.bilibili.com/{cid}.xml，按时间分段抽样，同一句合并成「×N」。
- 热评：评论区按热度的第一页前几条。
字幕要登录才拿得到，这里不取。

看到的写成一段如实的报告：看了几张画面、听了多久、哪些没看到，都写清楚。
"""

import asyncio
import io
import re
import xml.etree.ElementTree as ET

import aiohttp

from . import vision

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
API = "https://api.bilibili.com"
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
P_RE = re.compile(r"[?&]p=(\d+)")
STT_MODEL = "FunAudioLLM/SenseVoiceSmall"
FRAMES = 6  # 截几张画面
AUDIO_BYTES_PER_MIN = 500_000  # 最低码率的音频大约每分钟这么多字节（64kbps）
MAX_TRANSCRIPT = 3000  # 转写最多给它看这么多字
DANMAKU_SAMPLES = 30
COMMENTS = 6
TIMEOUT = 30

FRAMES_PROMPT = (
    "这是从一个视频里按时间顺序截下的 {n} 张画面，拼成了一张图（从左到右、从上到下，"
    "依次在 {times}）。逐张客观描述画面里有什么、发生了什么；画面上有字就照原样写出来。"
    "只写看得到的，不评价，不写感受。用中文，每张一两句，按「[时间] 描述」一行一张。"
)


class VideoError(Exception):
    pass


def find_bv(text: str) -> tuple[str, int]:
    """链接或文字里的 BV 号和分 P（没写就是第 1 段）；找不到返回 ("", 1)。"""
    m = BV_RE.search(str(text or ""))
    if not m:
        return "", 1
    p = P_RE.search(str(text))
    return m.group(1), max(1, int(p.group(1))) if p else 1


def fmt_t(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


async def _json(s: aiohttp.ClientSession, url: str, **params) -> dict:
    async with s.get(url, params=params, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}) as r:
        body = await r.json(content_type=None)
    if not isinstance(body, dict) or body.get("code") != 0:
        raise VideoError(f"B 站返回：{(body or {}).get('message') if isinstance(body, dict) else '格式不对'}")
    return body.get("data") or {}


async def info(s: aiohttp.ClientSession, bvid: str, part: int = 1) -> dict:
    """标题、UP 主、简介、这一段的 cid 和时长。"""
    d = await _json(s, f"{API}/x/web-interface/view", bvid=bvid)
    pages = d.get("pages") or []
    page = pages[part - 1] if 0 < part <= len(pages) else (pages[0] if pages else {})
    desc = re.sub(r"\s+", " ", d.get("desc") or "").strip()
    return {
        "bvid": bvid, "aid": d.get("aid"), "title": d.get("title", ""),
        "up": (d.get("owner") or {}).get("name", ""), "desc": "" if desc == "-" else desc[:300],
        "cid": page.get("cid") or d.get("cid"), "part": page.get("page", 1), "parts": len(pages) or 1,
        "part_title": page.get("part", "") if len(pages) > 1 else "",
        "duration": int(page.get("duration") or d.get("duration") or 0),
        "views": (d.get("stat") or {}).get("view"),
    }


def pick_shots(times: list, n: int) -> list[int]:
    """从快照的时间表里均匀挑 n 格的下标（不重复）。"""
    if not times:
        return []
    if len(times) <= n:
        return list(range(len(times)))
    step = len(times) / n
    return sorted({min(len(times) - 1, int(step * i + step / 2)) for i in range(n)})


def collage(cells: list[bytes]) -> bytes:
    """几张画面拼成一张（每行 3 张），返回 JPEG。"""
    from PIL import Image

    ims = [Image.open(io.BytesIO(c)).convert("RGB") for c in cells]
    w, h = ims[0].size
    cols = min(3, len(ims))
    rows = (len(ims) + cols - 1) // cols
    out = Image.new("RGB", (w * cols, h * rows), "white")
    for i, im in enumerate(ims):
        out.paste(im.resize((w, h)), ((i % cols) * w, (i // cols) * h))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=85)
    return buf.getvalue()


async def frames(s: aiohttp.ClientSession, v: dict, n: int = FRAMES) -> tuple[bytes, list[int]]:
    """截 n 张画面拼成一张图 → (JPEG, 每张的秒数)。拿不到就抛 VideoError。"""
    from PIL import Image

    d = await _json(s, f"{API}/x/player/videoshot", bvid=v["bvid"], cid=v["cid"], index=1)
    sheets = [("https:" + u) if u.startswith("//") else u for u in d.get("image") or []]
    xs, ys = int(d.get("img_x_len") or 10), int(d.get("img_y_len") or 10)
    w, h = int(d.get("img_x_size") or 160), int(d.get("img_y_size") or 90)
    times = (d.get("index") or [])[1:]  # 第一项是占位
    if not sheets or not times:
        raise VideoError("这个视频没有画面快照")
    per = xs * ys
    times = times[: per * len(sheets)]
    chosen = pick_shots(times, n)
    cache, cells = {}, []
    for i in chosen:
        k = i // per
        if k not in cache:
            async with s.get(sheets[k], headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}) as r:
                cache[k] = Image.open(io.BytesIO(await r.read())).convert("RGB")
        j = i % per
        box = ((j % xs) * w, (j // xs) * h, (j % xs + 1) * w, (j // xs + 1) * h)
        buf = io.BytesIO()
        cache[k].crop(box).save(buf, "JPEG", quality=90)
        cells.append(buf.getvalue())
    return collage(cells), [int(times[i]) for i in chosen]


async def look(s: aiohttp.ClientSession, v: dict, key: str, base: str, model: str) -> tuple[str, list[int], dict]:
    """截画面并看一遍 → (逐张描述, 秒数, 用量)。"""
    img, secs = await frames(s, v)
    prompt = FRAMES_PROMPT.format(n=len(secs), times="、".join(fmt_t(t) for t in secs))
    url = f"{(base or 'https://api.siliconflow.cn/v1').rstrip('/')}/chat/completions"
    req = vision.build_request(vision.data_url(img, "image/jpeg"), model)
    req["messages"][0]["content"][1]["text"] = prompt
    req["max_tokens"] = 800
    async with s.post(url, json=req, headers={"Authorization": f"Bearer {key}"}) as r:
        body = await r.json(content_type=None)
    try:
        text = body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise VideoError(f"画面没看出来：{str(body)[:150]}")
    return text, secs, body.get("usage") or {}


async def listen(s: aiohttp.ClientSession, v: dict, key: str, base: str, minutes: int) -> tuple[str, int]:
    """听前 minutes 分钟 → (转写, 实际听了几秒)。"""
    d = await _json(s, f"{API}/x/player/playurl", bvid=v["bvid"], cid=v["cid"], fnval=16, qn=32)
    audios = ((d.get("dash") or {}).get("audio")) or []
    if not audios:
        raise VideoError("这个视频拿不到声音")
    a = min(audios, key=lambda x: x.get("bandwidth") or 0)
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if v["duration"] > minutes * 60:
        headers["Range"] = f"bytes=0-{minutes * AUDIO_BYTES_PER_MIN - 1}"
    async with s.get(a.get("baseUrl") or a.get("base_url"), headers=headers) as r:
        if r.status >= 400:
            raise VideoError(f"声音下载失败（{r.status}）")
        data = await r.read()
    form = aiohttp.FormData()
    form.add_field("model", STT_MODEL)
    form.add_field("file", data, filename="audio.m4a", content_type="audio/mp4")
    url = f"{(base or 'https://api.siliconflow.cn/v1').rstrip('/')}/audio/transcriptions"
    async with s.post(url, data=form, headers={"Authorization": f"Bearer {key}"}) as r:
        body = await r.json(content_type=None)
    text = (body or {}).get("text") if isinstance(body, dict) else None
    if text is None:
        raise VideoError(f"声音没转出来：{str(body)[:150]}")
    heard = int(((body.get("usage") or {}).get("seconds")) or min(v["duration"], minutes * 60))
    text = re.sub(r"(🎼\s*)+", "🎼", text)  # 一串音乐符号并成一个
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_TRANSCRIPT], heard


def sample_danmaku(rows: list[tuple[float, str]], limit: int = DANMAKU_SAMPLES) -> list[str]:
    """按时间分成 limit 段，每段取出现最多的一句；同一句合并成「×N」。"""
    if not rows:
        return []
    rows.sort()
    end = rows[-1][0] or 1
    buckets: dict[int, dict[str, int]] = {}
    for t, text in rows:
        b = min(limit - 1, int(t / end * limit))
        buckets.setdefault(b, {})
        buckets[b][text] = buckets[b].get(text, 0) + 1
    out = []
    for b in sorted(buckets):
        text, n = max(buckets[b].items(), key=lambda kv: kv[1])
        out.append(f"[{fmt_t(b * end / limit)}] {text}" + (f" ×{n}" if n > 1 else ""))
    return out


async def danmaku(s: aiohttp.ClientSession, v: dict) -> list[str]:
    async with s.get(f"https://comment.bilibili.com/{v['cid']}.xml", headers={"User-Agent": UA}) as r:
        raw = await r.read()
    root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    rows = []
    for d in root.iter("d"):
        text = (d.text or "").strip()
        try:
            t = float((d.get("p") or "0").split(",")[0])
        except ValueError:
            continue
        if text:
            rows.append((t, text[:40]))
    return sample_danmaku(rows)


async def hot_comments(s: aiohttp.ClientSession, v: dict) -> list[str]:
    d = await _json(s, f"{API}/x/v2/reply/main", type=1, oid=v["aid"], mode=3)
    out = []
    for x in (d.get("replies") or [])[:COMMENTS]:
        msg = re.sub(r"\s+", " ", (x.get("content") or {}).get("message") or "").strip()
        if msg:
            out.append(f"{(x.get('member') or {}).get('uname', '')}：{msg[:100]}")
    return out


async def watch(link: str, key: str, base: str = "", model: str = "", minutes: int = 10) -> tuple[str, dict]:
    """看一个 B 站视频 → (如实的报告, 用量 {"see": 看图用量, "heard": 听了几秒})。

    每一部分各看各的，哪部分拿不到就在报告里说一句，不影响其他部分。没有 Key 时只读弹幕和热评。
    """
    bvid, part = find_bv(link)
    if not bvid:
        raise VideoError("没找到 B 站视频的 BV 号")
    usage = {"see": {}, "heard": 0}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as s:
        v = await info(s, bvid, part)
        jobs = {
            "look": look(s, v, key, base, model) if key else None,
            "listen": listen(s, v, key, base, minutes) if key else None,
            "danmaku": danmaku(s, v),
            "comments": hot_comments(s, v) if v.get("aid") else None,
        }
        names = [k for k, j in jobs.items() if j is not None]
        got = dict(zip(names, await asyncio.gather(*(jobs[k] for k in names), return_exceptions=True)))

    head = f"B 站视频「{v['title']}」，UP 主 {v['up']}，{fmt_t(v['duration'])}"
    if v["parts"] > 1:
        head += f"（第 {v['part']}/{v['parts']} 段{'：' + v['part_title'] if v['part_title'] else ''}）"
    lines = [head]
    if v["desc"]:
        lines.append(f"简介：{v['desc']}")
    missed = []

    r = got.get("look")
    if isinstance(r, tuple):
        text, secs, u = r
        usage["see"] = u
        lines.append(f"\n画面（按时间截了 {len(secs)} 张缩略图看，不是连着看的）：\n{text}")
    else:
        missed.append("画面" + (f"（{r}）" if isinstance(r, Exception) else "（没有看图用的 Key）"))

    r = got.get("listen")
    if isinstance(r, tuple):
        text, heard = r
        usage["heard"] = heard
        whole = heard >= v["duration"] - 5
        span = "整段" if whole else f"前 {fmt_t(heard)}"
        lines.append(f"\n声音（听了{span}，机器转写的字，可能有错；🎼 是音乐）：\n{text or '（没听出说话）'}")
    else:
        missed.append("声音" + (f"（{r}）" if isinstance(r, Exception) else "（没有转写用的 Key）"))

    r = got.get("danmaku")
    if isinstance(r, list) and r:
        lines.append(f"\n弹幕（按时间抽了 {len(r)} 条，是观众的反应）：\n" + "\n".join(r))
    elif isinstance(r, Exception):
        missed.append(f"弹幕（{r}）")

    r = got.get("comments")
    if isinstance(r, list) and r:
        lines.append("\n热评：\n" + "\n".join(r))

    if missed:
        lines.append("\n（这次没看到：" + "；".join(missed) + "）")
    return "\n".join(lines), usage
