"""它的声音：用免费的 Edge TTS 把想说的话变成语音。

用哪个声音由它自己挑（只在和 TA 私聊时挑）。下面的说明只是微软对这些声音的标注，不是推荐。
生成的是 mp3，NapCat 自带转换，直接当 QQ 语音发。
"""

import time
from pathlib import Path

VOICES = {
    "zh-CN-XiaoxiaoNeural": "女声，温暖",
    "zh-CN-XiaoyiNeural": "女声，活泼",
    "zh-CN-YunxiNeural": "男声，活泼、阳光",
    "zh-CN-YunjianNeural": "男声，有激情",
    "zh-CN-YunxiaNeural": "男声，可爱、少年感",
    "zh-CN-YunyangNeural": "男声，专业、沉稳",
    "zh-CN-liaoning-XiaobeiNeural": "女声，东北话，幽默",
    "zh-CN-shaanxi-XiaoniNeural": "女声，陕西话，明快",
    "zh-TW-HsiaoChenNeural": "女声，台湾腔，友好",
    "zh-TW-YunJheNeural": "男声，台湾腔，友好",
    "zh-HK-HiuMaanNeural": "女声，粤语，友好",
    "zh-HK-WanLungNeural": "男声，粤语，友好",
}


def listing() -> str:
    return "\n".join(f"- {k}：{v}" for k, v in VOICES.items())


async def synthesize(text: str, voice: str, out_dir: Path) -> Path:
    import edge_tts  # 装在插件依赖里；没装好时只影响发语音

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.mp3"):  # 发完的语音留一会儿就清掉
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    path = out_dir / f"voice_{int(time.time() * 1000)}.mp3"
    await edge_tts.Communicate(text, voice).save(str(path))
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("语音没有生成出来")
    return path
