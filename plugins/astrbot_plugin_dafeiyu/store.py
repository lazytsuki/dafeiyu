"""插件自己的小状态，存成一个 JSON 文件，重启不丢。"""

import json
import os
from pathlib import Path

DEFAULT = {
    "owner_umo": "",          # 给它起名字的人（owner）的私聊会话 ID，第一次私聊时自动记下
    "owner_last_msg": 0.0,    # owner 最后一次说话的时间戳
    "last_proactive": 0.0,    # 上次主动私聊的时间戳
    "day": "",                # 计数所属日期 YYYY-MM-DD
    "proactive_count": 0,     # 当天主动私聊次数
    "fixed_done": [],         # 当天已发过的固定时间点
    "qzone_done": [],         # 当天已处理过的说说时间点
    "qzone_slots": {},        # 当天各说说时间点抽到的随机偏移（分钟）
    "recent_proactive": [],   # 最近主动发出的话 [{"t": ts, "text": ...}]
    "drafts": {},             # 待审核说说草稿 {id: text}
    "next_draft_id": 1,
    "posted": [],             # 已发说说 [{"t": ts, "tid": ..., "text": ...}]
    "reflected_day": "",      # 夜间反思已处理到哪一天
    "reflect_offsets": {},    # 每天的对话已经反思到第几行 {day: n}
    "thoughts": [],           # 它心里还没说出口的话，说不说由它自己
    "guest_seen": {},         # 别人（和群）最后一次对话的时间 {umo: ts}
    "migrations": {},         # 已执行过的一次性迁移 {name: True}
    "drafts_unsent": [],      # 已写好、但还没成功发给 TA 审核的草稿编号（仅开启审核时）
    "qzone_count": 0,         # 当天它自己发了几条说说（防封护栏用）
    "signature": "",          # 它最近一次给自己设的个性签名
    "pending_signature": "",  # 反思时决定要换、还没生效的签名
    "last_online": 0.0,       # 最近一次确认 QQ 在线的时间
    "missed_since": 0.0,      # 上一段不在线从什么时候开始（等补回 TA 的消息，补完清零）
    "missed_after": 0.0,      # 重新连上后等到这个时间再补回（先让 NapCat 推过来的离线消息走完）
    "jobs_handled": [],       # 已经补做过的、当时错过的未来任务 ID
    "send_error": "",         # 最近一次主动发消息失败的时间和原因
    "qzone_seen": [],         # 空间里已经看过的评论 / TA 的说说
    "qzone_comment_count": 0, # 当天在空间评论了几条（防封护栏用）
    "moods": [],              # 它自己记下的心情 [{"t": ts, "text": ..., "with_ta": bool}]
    "usage": {},              # 插件自己当天用掉的 token {"day", "in", "cached", "out"}
    "budget_paused": "",      # 哪天超了预算（别的扩展也看这个，自发的事那天先停）
    "rhythm": {},             # 它在反思里给自己定的节奏，覆盖配置里的初始值（见 rhythm.py）
}


def data_dir() -> Path:
    try:
        from astrbot.api.star import StarTools

        return Path(StarTools.get_data_dir("astrbot_plugin_dafeiyu"))
    except Exception:
        return Path("data") / "plugin_data" / "astrbot_plugin_dafeiyu"


class Store:
    def __init__(self):
        d = data_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "state.json"
        self.data = json.loads(json.dumps(DEFAULT))  # 深拷贝，别让默认值被改掉
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text("utf-8")))
            except Exception:
                pass

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.path)
