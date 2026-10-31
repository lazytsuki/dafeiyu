"""它能画图：用硅基流动的生图接口（OpenAI 兼容的 POST {api_base}/images/generations）。

画不画、画什么都是它自己的事。这里只管：找 Key、拼请求、解析返回、把图下载下来。
画好的图存进它自己的收藏（stickers.py），和表情包放在一起。

Key 优先用插件配置里的 draw_api_key；没填就从 AstrBot 已经配好的提供商里找硅基流动那一项
（比如语音转文字 siliconflow_stt），一个 Key 多用。
接口说明见 https://docs.siliconflow.cn/cn/docs/api/images-generations-post ：
返回 {"images": [{"url": ...}], "timings": {...}, "seed": ...}，图片地址一小时内有效，所以拿到就下载。
"""

import aiohttp

DEFAULT_MODEL = "Kwai-Kolors/Kolors"
DEFAULT_BASE = "https://api.siliconflow.cn/v1"
MAX_PROMPT = 800          # 描述太长就截掉
MAX_BYTES = 10 * 1024 * 1024  # 下载下来的图超过这么大就不要了

# image_size 是必填项，按官方推荐值取一个方的；不认识的模型就用 1024x1024
SIZES = {
    "Kwai-Kolors/Kolors": "1024x1024",
    "Qwen/Qwen-Image": "1328x1328",
}


class DrawError(Exception):
    pass


def find_key(conf: dict) -> tuple[str, str]:
    """从 AstrBot 的配置里找硅基流动的 Key：返回 (key, api_base)，找不到是 ("", "")。

    提供商有几种写法：api_key + api_base（语音转文字等）、embedding_api_key + embedding_api_base（向量），
    以及 provider_sources 里的 key（列表）+ api_base。只认 api_base 指向 siliconflow 的那些。
    """
    entries = list(conf.get("provider") or []) + list(conf.get("provider_sources") or [])
    for p in entries:
        if not isinstance(p, dict) or p.get("enable") is False:
            continue
        for key_field, base_field in (
            ("api_key", "api_base"),
            ("embedding_api_key", "embedding_api_base"),
            ("key", "api_base"),
        ):
            base = str(p.get(base_field) or "")
            if "siliconflow" not in base:
                continue
            key = p.get(key_field)
            if isinstance(key, list):
                key = next((k for k in key if k), "")
            if key:
                return str(key).strip(), base.rstrip("/")
    return "", ""


def build_request(prompt: str, model: str = "") -> dict:
    """拼出生图请求的 JSON。一次只画一张。"""
    model = (model or "").strip() or DEFAULT_MODEL
    body = {
        "model": model,
        "prompt": prompt.strip()[:MAX_PROMPT],
        "image_size": SIZES.get(model, "1024x1024"),
    }
    if model == "Kwai-Kolors/Kolors":
        body["num_inference_steps"] = 20  # 官方默认值，写明白免得以后默认变了多花钱
    return body


def parse_response(body) -> str:
    """从返回里取出第一张图的地址；出错（或没图）就抛 DrawError，带上接口给的原因。"""
    if not isinstance(body, dict):
        raise DrawError(f"返回格式不对：{str(body)[:200]}")
    images = body.get("images") or body.get("data") or []
    if images and isinstance(images[0], dict) and images[0].get("url"):
        return str(images[0]["url"])
    err = body.get("message") or body.get("error") or body
    if isinstance(err, dict):
        err = err.get("message") or err
    raise DrawError(f"没画出来：{str(err)[:200]}")


async def generate(prompt: str, key: str, base: str = "", model: str = "") -> bytes:
    """画一张，返回图片字节。"""
    url = f"{(base or DEFAULT_BASE).rstrip('/')}/images/generations"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url, json=build_request(prompt, model), headers=headers) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:
                raise DrawError(f"接口返回 HTTP {r.status}：{(await r.text())[:200]}")
        img_url = parse_response(body)
        async with s.get(img_url) as r:
            r.raise_for_status()
            data = await r.read()
    if not data or len(data) > MAX_BYTES:
        raise DrawError("图片是空的，或者太大了")
    return data
