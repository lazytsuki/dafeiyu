"""防循环保险：次数可以不限，但万一出了岔子陷进循环（短时间里连着做同一件事），先停一会儿。

这不是对它的约束：正常的节奏碰不到这条线，碰到了多半是程序或模型出了问题。
"""

WINDOW = 600  # 看最近这么久（秒）里做了几次
PAUSE = 3600  # 碰线后这件事停多久（秒）
LIMITS = {  # 最近 WINDOW 秒里超过这么多次就算异常
    "message": 12,  # 它自己发起、发给 TA 的一次（不管拆成几个气泡）
    "post": 4,
    "comment": 10,
    "voice": 15,
    "draw": 8,
    "see": 40,  # 看一张图（收到的、群里被叫到时补看的、自己画好的）
    "wish": 15,  # 自己往心愿单上写一条或划掉一条
    "free": 6,  # 一段空闲时刻（它自己定的下次时间最短 5 分钟，正常碰不到）
    "link": 30,  # 打开一个链接、读一组转发或一个文本文件
    "look": 30,  # 自己搜一次、读一段网页
    "like": 10,  # 在空间点一个赞
}


def record(times: list[float], now: float) -> list[float]:
    """记下这一次，只留最近 WINDOW 秒的。"""
    return [t for t in times if now - t < WINDOW] + [now]


def tripped(kind: str, times: list[float]) -> bool:
    return len(times) > LIMITS.get(kind, 10)
