"""感官：把它「看不到」的消息段翻译成文字，让它至少知道对方发了什么。

AstrBot 只把文字放进 message_str：小黄脸、戳一戳、骰子、语音（没开语音识别时）都会变成空消息，
空消息不会交给模型——对方发了个表情，它就像没看见一样。这里只做翻译，不替它决定怎么回应。
图片和商城表情（NapCat 会转成带 summary 的图片）由模型自己看，不在这里处理。
"""


def describe_segment(seg: dict, stt_enabled: bool = False) -> str:
    """一个 OneBot 消息段 → 给模型看的文字；不需要翻译的返回空串。"""
    t, data = seg.get("type"), seg.get("data") or {}
    if t == "face":
        raw = data.get("raw") or {}
        name = str(raw.get("faceText") or "").strip().lstrip("/").strip("[]").strip()
        return f"[表情：{name}]" if name and name != "表情" else "[一个 QQ 表情]"
    if t == "poke":
        return "[戳了你一下]"
    if t == "dice":
        return f"[掷了个骰子：{data.get('result', '?')} 点]"
    if t == "rps":
        return f"[出了个猜拳：{ {'1': '布', '2': '剪刀', '3': '石头'}.get(str(data.get('result')), '?') }]"
    if t == "record" and not stt_enabled:
        return "[发来一段语音。你现在还听不了语音，不知道里面说了什么]"
    return ""


def describe_message(segments: list, stt_enabled: bool = False) -> str:
    """整条消息里需要翻译的部分，拼成一段文字。"""
    if not isinstance(segments, list):
        return ""
    return "".join(describe_segment(s, stt_enabled) for s in segments if isinstance(s, dict))


def plain_text(segments: list) -> str:
    """消息的文字内容（翻聊天记录时用）：文字原样，图片写成 [图片]，其余按上面翻译。"""
    if not isinstance(segments, list):
        return str(segments or "")
    out = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        t, data = s.get("type"), s.get("data") or {}
        if t == "text":
            out.append(data.get("text", ""))
        elif t == "image":
            out.append(f"[{data.get('summary') or '图片'}]".replace("[[", "[").replace("]]", "]"))
        elif t == "reply":
            continue
        else:
            out.append(describe_segment(s) or f"[{t}]")
    return "".join(out).strip()
