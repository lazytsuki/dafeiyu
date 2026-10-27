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
