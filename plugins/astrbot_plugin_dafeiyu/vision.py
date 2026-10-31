"""看图：用硅基流动的视觉模型（OpenAI 兼容的 POST {api_base}/chat/completions + image_url）把图转成一段客观描述。

只写「图上是什么、上面写了什么字」，不替它判断好不好看、该有什么感受——那些由它自己看了描述再决定。
Key 和画图一样（draw.find_key），一个 Key 多用。
接口说明见 https://docs.siliconflow.cn/cn/userguide/capabilities/vision ：
图片放在 user 消息 content 里的 image_url，url 可以是 base64 的 data URL。
QQ 的图片地址外网不一定拉得到，所以先自己下载下来，转成 data URL 再发。
"""

import base64
import io

import aiohttp

DEFAULT_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
MAX_BYTES = 8 * 1024 * 1024  # 下载下来的图超过这么大就不看了
MAX_SIDE = 1280              # 长边缩到这么大再发（字还看得清，token 少一些）
MAX_DESC = 300               # 描述最长这么多字

PROMPT = (
    "客观地描述这张图：它是什么（照片、截图、表情包、画……）、画面里有什么、是什么样子；"
    "图上有文字就照原样写出来。只写看得到的，不评价，不猜发图人的意思，不写感受。"
    "用中文，一段话，200 字以内。"
)


class VisionError(Exception):
    pass


def prepare(data: bytes) -> tuple[bytes, str]:
    """把图整理成适合发给模型的样子：动图取第一帧，长边缩到 MAX_SIDE，转成 JPEG。

    返回 (字节, MIME)。Pillow 认不出来的就原样返回，MIME 按文件头猜。
    """
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.seek(0)
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:
        from .stickers import _ext

        ext = _ext(data)
        return data, f"image/{'jpeg' if ext == 'jpg' else ext}"


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def build_request(image_url: str, model: str = "") -> dict:
    """拼出看图请求的 JSON：一张图 + 固定的客观描述要求。"""
    return {
        "model": (model or "").strip() or DEFAULT_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        "max_tokens": 400,
        "temperature": 0.2,
        "stream": False,
    }


def clean(text: str) -> str:
    """描述压成一行、截到 MAX_DESC 字；方括号换掉，免得和「[图片：…]」的括号混在一起。"""
    text = " ".join(str(text or "").split())
    text = text.replace("[", "「").replace("]", "」")
    return text[:MAX_DESC].strip()


def parse_response(body) -> str:
    """从返回里取出描述；出错（或是空的）就抛 VisionError，带上接口给的原因。"""
    if not isinstance(body, dict):
        raise VisionError(f"返回格式不对：{str(body)[:200]}")
    try:
        msg = body["choices"][0]["message"]
        content = msg.get("content")
        if isinstance(content, list):  # 个别模型把文字放在分段里
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = clean(content)
    except (KeyError, IndexError, TypeError, AttributeError):
        text = ""
    if text:
        return text
    err = body.get("message") or body.get("error") or body
    if isinstance(err, dict):
        err = err.get("message") or err
    raise VisionError(f"没看出来：{str(err)[:200]}")


def tag(desc: str) -> str:
    """给模型看的样子：[图片：描述]。"""
    return f"[图片：{desc}]"


async def fetch(url: str) -> bytes:
    """把图下载下来（QQ 的图片地址、或本地文件路径）。"""
    if not url.startswith(("http://", "https://")):
        path = url[7:] if url.startswith("file://") else url
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    else:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as r:
                r.raise_for_status()
                data = await r.read()
    if not data or len(data) > MAX_BYTES:
        raise VisionError("图片是空的，或者太大了")
    return data


async def describe(data: bytes, key: str, base: str = "", model: str = "") -> tuple[str, dict]:
    """看一张图，返回 (客观描述, 用量)。用量是接口给的 usage（没有就是空字典）。"""
    url = f"{(base or 'https://api.siliconflow.cn/v1').rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    img, mime = prepare(data)
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url, json=build_request(data_url(img, mime), model), headers=headers) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:
                raise VisionError(f"接口返回 HTTP {r.status}：{(await r.text())[:200]}")
    return parse_response(body), body.get("usage") or {}
