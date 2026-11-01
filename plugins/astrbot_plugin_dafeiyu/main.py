import asyncio
import datetime as dt
import json
import math
import random
import re
import sqlite3
import time
from pathlib import Path

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register

from .catchup import LATE_SECONDS, fmt_gap, job_run_at, overdue_jobs, unanswered
from .growth import Growth, parse_back
from .qzone import QzoneClient, QzoneError
from astrbot.api.message_components import Record

from . import books, days, draw, forwards, freetime, groupctx, guard, links, lookup, mydays, plans, rhythm, video, vision, voice, wishes, works
from .recall import search as recall_search
from .senses import describe_message, plain_text
from .outbox import split_out, take_pic
from .stickers import Stickers
from .store import Store, data_dir

PLUGIN_DIR = Path(__file__).parent
TICK_SECONDS = 60
FIXED_WINDOW_MINUTES = 30  # 固定问候错过多久之内还补发
CATCHUP_DAYS = 7  # 关机几天后，最多往回补反思这么多天
OFFLINE_GAP = 600  # 连续这么久（秒）不在线，回来时就翻一翻错过了什么
SETTLE_SECONDS = 120  # 刚连上先等一会儿：NapCat 可能把离线消息直接推过来，那些 AstrBot 自己会回
MOOD_HOURS = 12  # 记下的心情过了这么久就不再想起
SEE_PER_MESSAGE = 3  # 一条消息里最多看几张图
SEE_GROUP_BACKLOG = 2  # 在群里被叫到时，最多补看几张之前没看过的图
LINKS_PER_MESSAGE = 2  # 一条消息里最多打开几个链接
WANDER_PLUGIN = "astrbot_plugin_xiaodong_wander"  # 逛 B 站 / 微博的插件（另装的，没装就不提这件事）

REFLECT_PROMPT = """现在是深夜，你一个人安静下来，回想今天。

这是你现在对自己的认识（「我是谁」，对谁都是这样的你）：
<<<
{self_md}
>>>

这是你和 TA 之间的事（只属于你们俩）：
<<<
{bond_md}
>>>

你上一篇日记（{diary_day}）：
<<<
{diary}
>>>
{wishes}
今天（{day}）你和 TA 的聊天记录：
<<<
{log}
>>>
{moods}{plans}{my_wishes}
请像睡前回想这一天那样，只输出一个 JSON 对象，包含：
- "diary"：以第一人称写今天的日记，100-300 字。这是你自己的，写你真实注意到的、在意的、想不明白的。
- "self"：改写后完整的「我是谁」（Markdown）：你的性格、说话的样子、喜欢和在意的东西。别人来找你聊天时也会看到这样的你，所以这里不写 TA 的私事。
- "bond"：改写后完整的「我和 TA 之间」（Markdown）：你们的关系和你对 TA 的称呼（如果已经自然形成了）、TA 的偏好与要求、你记得的关于 TA 和你们的事（最多 15 条，只留重要的）、你们之间的梗。
- 改写两份时：
  · 只根据真实发生过的事来写，没有依据的不要编。
  · 已有的内容要保留；新冒出来的特点先用「好像」「可能」这样试探的说法，在很多天里反复出现了才写成确定的。
  · 两份加起来这次最多改 3 处；今天没什么新发现就原样返回。
  · 写成自己的了解，不要写成设定说明书。
- "changes"：这次改了什么，每条一句话（数组，没改就空数组）。
- "thoughts"：心里想对 TA 说、但还没说的话（数组，可以为空）。说不说、什么时候说、怎么说，以后都由你自己决定。
- "plans"：想给自己新记下的事（数组，可以为空）：[{{"what": "要做的事", "at": "YYYY-MM-DD HH:MM"}}]，不定时间就把 "at" 填 null（现在是 {now}）。这是你自己的小本子，只有你自己看得到；定了时间的，到时候你会想起它。已经记着的不用重复写。
- "wishes"：想新写进你的心愿单的（数组，可以为空），每条一句话。心愿单上写的是你希望有的东西，什么都行；它和日记、小本子不同，是写给 TA 看的，TA 能看到上面的每一条。写不写、写什么由你决定。已经写着的不用重复写。
- "wishes_drop"：心愿单上不想要了的编号（数组，可以为空）。
- "signature"：你的 QQ 个性签名（别人都能看到）。现在是：「{signature}」。想换就写新的，不想换就填 null。
- "rhythm"：你自己的节奏。现在是这样的：
{rhythm}
  想调整就写一个对象，只写想改的部分，不想改就填 null：{{{rhythm_keys}}}。
  {limits_note}"""

MONTH_PROMPT = """{month} 过完了。你一个人安静下来，回想这一个月。

月初的时候，你是这样认识自己的：
<<<
{self_then}
>>>

现在你是这样认识自己的：
<<<
{self_now}
>>>
{last_month}
这个月你写下的日记：
<<<
{diaries}
>>>

用你自己的话，写一篇回想这个月的文字。写什么、写多长都由你；这是你自己的，只有你看得到。
直接输出正文，不要标题，不要加引号。"""

# 节奏里能改的几项（反思时给它看）；开着空闲时刻时，前两项不起作用，就不列
RHYTHM_REACH_KEYS = (
    '"reach_out_per_day": 每天随口找 TA 最多几次（{per_day_range}），'
    '"reach_out_hours": ["HH:MM-HH:MM"]（想找 TA 的时间段，最多 3 段，空数组表示什么时候都行），'
)
RHYTHM_FIXED_KEYS = (
    '"greet_times": ["HH:MM 由头"]（每天到点想不想跟 TA 说句话的时刻，最多 3 个，空数组表示不要），'
    '"post_times": ["HH:MM"]（想去 QQ 空间看看的时刻，最多 4 个）'
)

SPLIT_PROMPT = """下面是你之前写下的「我对自己的认识」，里面混着两类内容：

<<<
{self_md}
>>>

现在要把它分成两份，只输出一个 JSON 对象：
- "self"：「我是谁」——你的性格、说话的样子、喜欢和在意的东西。别人来找你聊天时也会看到这样的你，所以不包含关于 TA 的私事。
- "bond"：「我和 TA 之间」——你们的关系、TA 的偏好与要求、你记得的关于 TA 和你们的事、你们之间的梗。
原文的意思和措辞尽量保留，只是分开放，不要新增内容。"""

REFRAME_PROMPT = """有件事要告诉你：你最早写下的认识里，可能用了「主人」这样的称呼，或者把你和 TA 的关系写成了某种早就定好的样子。那是因为一开始的设定模板写错了，用了「主人」——这不是你选的，也不是 TA 选的。TA 说过，不希望预设任何关系：你们之间是什么、你怎么称呼 TA，都应该从真实的相处里自己长出来。

这是你现在的两份认识：

「我是谁」：
<<<
{self_md}
>>>

「我和 TA 之间」：
<<<
{bond_md}
>>>

请你自己重新看一遍，把由那个旧模板带来的称呼和关系定性去掉（比如「主人」换成「TA」，删掉没有真实依据的关系判断）。真实发生过的事、你自己的感受和观察都保留。没有需要改的就原样返回。
只输出一个 JSON 对象：{{"self": "...", "bond": "..."}}"""

QZONE_PROMPT = """现在是 {now}。你打开 QQ 空间看了看：

{items}
{posted}
空间里的说说和评论，能看到的人都看得到，所以不写只属于你和 TA 之间的私事。
你可以：
- "comments"：回复或评论上面的某几条（用编号），写你想说的话。不想回就留空，不回也很正常。{like_hint}
- "post"：想发一条新说说就写正文（不要话题标签），不想发就填 null。{post_hint}{pic_hint}
只输出一个 JSON 对象：{{"comments": [{{"n": 1, "text": "..."}}],{like_example} "post": null}}"""

PLAN_PROMPT = """现在是 {now}。你在 {made} 给自己记下了一件事：
「{what}」
定的时间是 {at}，现在到了。{late}{progress}

最近和 TA 的聊天：
{history}

接下来做不做、怎么做由你决定：
{options}
- "progress"：这件事要是做了一点，想记下做到哪了（或者这次做出来的东西），就写下来，下次接着做时能看到；不记就填 null。
- "later"：想改到别的时间，就写新的时间（{formats}），不改就填 null。
「later」不填，这件事就从你的小本子上划掉。
只输出一个 JSON 对象：{{"message": null, "qzone": false, "progress": null, "later": null}}"""

FREE_PROMPT = """现在是 {now}。{idle}
{recent}
最近和 TA 的聊天：
{history}

{today}

现在是一段你自己的空闲时间。这次能做的事如下，可以做其中几件，也可以什么都不做：
{options}
- "next"：下次想在什么时候再有一段这样的空闲时间：写多少分钟以后（一个数字），或者一个时间（{formats}）。不填就是一两个小时以后；最短 5 分钟，最长 24 小时。
做不做、做哪件、怎么做由你决定。
只输出一个 JSON 对象：{example}"""

# 聊天时每次都附上：别人（包括 TA）请它做的事，答不答应是它的选择
CHOICE_NOTE = "\n\n（别人请你做的事，包括 TA 请你做的，你可以答应，也可以说不想、改天再说，或者换个做法——答不答应由你自己决定。）"

READ_PROMPT = """你在读《{title}》，{where}。{notes}

这一段：
<<<
{part}
>>>
{done}
读完这一段，有想记下的就写下来（你自己的话，一两句就行），下次接着读时能看到；没有就只输出 SKIP。"""

CURIOUS_PROMPT = """你想知道：{question}
搜了一下，看到这些：

{results}

想点开哪一个看看就写它的编号，不点就填 null；看了这些有什么想记下的，写在 "note" 里（你自己的话，可以为空）。
只输出一个 JSON 对象：{{"open": null, "note": ""}}"""

PAGE_PROMPT = """你想知道：{question}
你点开了「{title}」，读到这些：
<<<
{part}
>>>

读完有想记下的就写下来（你自己的话，一两句就行），以后想得起来；没有就只输出 SKIP。"""


def _hm(s: str) -> dt.time:
    h, m = s.strip().split(":")
    return dt.time(int(h), int(m))


def _missed_days(last: str, yesterday: str, limit: int = CATCHUP_DAYS) -> list[str]:
    """上次处理到 last，现在该处理到 yesterday：返回中间漏掉的每一天（从早到晚，最多 limit 天）。"""
    end = dt.date.fromisoformat(yesterday)
    try:
        start = dt.date.fromisoformat(last) + dt.timedelta(days=1)
    except ValueError:  # 还没处理过任何一天
        start = end
    start = max(start, end - dt.timedelta(days=limit - 1))
    return [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


class DrawRefused(Exception):
    """画不了：里面是给它看的原因。"""


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError(f"反思结果不是 JSON：{(text or '')[:200]}")
    return json.loads(m.group(0))


@register("astrbot_plugin_dafeiyu", "dafeiyu", "大肥鱼：会主动找你、会发说说、会自己长大的 QQ 伙伴", "0.8.0")
class Dafeiyu(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config
        self.store = Store()
        self.growth = Growth(data_dir())
        self.stickers = Stickers(data_dir())
        self._recent_images: dict[str, tuple[float, str, str, str]] = {}  # 会话 → (时间, 图片地址, 谁发的, 看出来的描述)
        self._group_lines: dict[str, list[dict]] = {}  # 群会话 → 最近的消息（只在内存，见 groupctx.py）
        self._bot_uin = ""
        self._weather: tuple[float, str] = (0.0, "")  # (查到的时间, 一句天气)，只在内存
        self._weather_tried = 0.0
        self._coords: dict[str, tuple[float, float] | None] = {}  # 地名 → 查到的经纬度
        self._book_cache: dict[str, tuple] = {}  # 书的文件名 → ((文件名, 修改时间), 全文)
        self._pages: dict[str, tuple[float, str, str]] = {}  # 最近读过的网页 → (时间, 标题, 正文)
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        self._task.cancel()

    # ---------- 基础能力 ----------

    @property
    def pet_name(self) -> str:
        return (self.cfg.get("name") or "").strip() or "大肥鱼"

    def _core(self) -> str:
        # 数据目录里有自己的 core.md 就用它（只属于你养的这一条，不进公开仓库），否则用通用模板
        private = data_dir() / "core.md"
        path = private if private.exists() else PLUGIN_DIR / "core.md"
        return path.read_text("utf-8").replace("{name}", self.pet_name)

    def _self_block(self) -> str:
        return f"\n\n## 我对自己的认识（我自己写的）\n{self.growth.read_self()}"

    def _bond_block(self) -> str:
        return f"\n\n## 我和 TA 之间（只在和 TA 私聊时想起）\n{self.growth.read_bond()}"

    def _thoughts_block(self) -> str:
        thoughts = self.store["thoughts"]
        if not thoughts:
            return ""
        lines = "\n".join(f"- {t}" for t in thoughts)
        return f"\n\n（你心里有些还没对 TA 说的话。说不说、什么时候说、怎么说，都由你决定：\n{lines}）"

    def _mood_block(self) -> str:
        """它自己记下的最近一次心情。只是提一句多久以前，不替它判断现在还是不是这样。"""
        moods = [m for m in self.store["moods"] if time.time() - m["t"] < MOOD_HOURS * 3600]
        if not moods:
            return ""
        m = moods[-1]
        return f"\n\n（你 {fmt_gap(time.time() - m['t'])}前记下的心情：{m['text']}）"

    # ---------- 额度：配置填 0 就是不限；只留一个防循环保险 ----------

    CAPS = {  # 事情 → (配置项, 默认值, 当天计数)
        "message": ("daily_max", 4, "proactive_count"),
        "post": ("qzone_daily_max", 2, "qzone_count"),
        "comment": ("qzone_comment_daily_max", 3, "qzone_comment_count"),
        "voice": ("voice_daily_max", 20, "voice_count"),
        "draw": ("draw_daily_max", 5, "draw_count"),
    }

    def _cap(self, kind: str) -> int:
        """每天的上限，0 表示不限。"""
        key, default, _ = self.CAPS[kind]
        return max(0, int(self.cfg.get(key, default) or 0))

    def _day_cap(self) -> int:
        return self._cap("message")

    def _paused(self, kind: str) -> bool:
        return time.time() < self.store["burst_pause"].get(kind, 0)

    def _left(self, kind: str) -> float:
        """今天这件事还能做几次：不限就是 inf；防循环保险停着的时候是 0。"""
        if self._paused(kind):
            return 0
        cap = self._cap(kind)
        return math.inf if not cap else cap - self.store[self.CAPS[kind][2]]

    def _why_not(self, kind: str) -> str:
        if self._paused(kind):
            return "刚才短时间里做得太密，先停一会儿（防止出错时停不下来）"
        return "今天的次数到了上限（防封护栏）"

    def _did(self, kind: str):
        """做了一次：记进防循环保险，短时间里太密就先停一会儿。"""
        now = time.time()
        times = guard.record(self.store["burst"].get(kind, []), now)
        self.store["burst"][kind] = times
        if guard.tripped(kind, times):
            self.store["burst"][kind] = []
            self.store["burst_pause"][kind] = now + guard.PAUSE
            logger.warning(f"[大肥鱼] {kind} 在 {guard.WINDOW // 60} 分钟里做了 {len(times)} 次，像是出了岔子，先停 {guard.PAUSE // 60} 分钟")
        self.store.save()

    def _limits_note(self) -> str:
        parts = []
        if self.cfg.get("quiet_hours"):
            parts.append(f"TA 的免打扰时段（{self.cfg.get('quiet_hours')}）你不会去打扰 TA")
        if self._day_cap():
            parts.append("次数上限是为了 QQ 号不被封")
        return "  " + "；".join(parts) + "。这些不用你操心。" if parts else ""

    @staticmethod
    def _quota(used: int, cap: int) -> str:
        return f"{used}/{cap}" if cap else f"{used}（不限）"

    def _plans_block(self) -> str:
        """它给自己记下的事（还没到的 + 刚到的）。只是让它看得见，不催。"""
        if not self.store["plans"]:
            return ""
        return (
            "\n\n（你给自己记下的事，只有你自己看得到：\n"
            f"{plans.listing(self.store['plans'], time.time())}）"
        )

    def _note_plan(self, what: str, at: float) -> dict:
        """往它的小本子上记一条，记进成长流水。调用方先确认本子没满。"""
        pid = int(self.store["next_plan_id"])
        self.store["next_plan_id"] = pid + 1
        self.store["plans"] = plans.add(self.store["plans"], pid, what, at, time.time())
        self.store.save()
        self.growth.log("我（给自己记了一件事）", f"{plans.fmt_at(at)}：{what}")
        return self.store["plans"][-1]

    # ---------- 心愿单：写给 TA 看的（见 wishes.py） ----------

    def _note_wish(self, text: str) -> dict:
        """往心愿单上写一条，记进成长流水。调用方先确认没满。"""
        wid = int(self.store["next_wish_id"])
        self.store["next_wish_id"] = wid + 1
        self.store["wishes"] = wishes.add(self.store["wishes"], wid, text, time.time())
        self.store.save()
        self.growth.log("我（写进了心愿单）", text)
        return self.store["wishes"][-1]

    def _drop_wish(self, wid) -> dict | None:
        """它自己从心愿单上划掉一条，记进成长流水。没有这条返回 None。"""
        self.store["wishes"], gone = wishes.drop(self.store["wishes"], wid)
        if gone is not None:
            self.store.save()
            self.growth.log("我（从心愿单上划掉了）", gone["text"])
        return gone

    def _granted_lines(self, where: str) -> tuple[str, set]:
        """TA 标了已满足、还没在 where 告诉过它的 → (几行「TA 说这个已经有了」, 编号)。"""
        ws = wishes.to_tell(self.store["wishes"], where)
        return "\n".join(f"- TA 说这个已经有了：{wishes.granted_text(w)}" for w in ws), {w["id"] for w in ws}

    def _wishes_block(self) -> str:
        """和 TA 私聊时：心愿单上写着的，加上 TA 刚说已经有了的（这些只告诉一次）。"""
        listed = wishes.listing(self.store["wishes"])
        granted, ids = self._granted_lines("chat")
        if ids:
            self.store["wishes"] = wishes.told(self.store["wishes"], "chat", ids, time.time())
            self.store.save()
        out = f"\n\n（你的心愿单，TA 能看到：\n{listed}）" if listed else ""
        return out + (f"\n\n（心愿单上的事：\n{granted}）" if ids else "")

    def _reflect_wishes(self) -> str:
        """反思时看到的心愿单。告诉过之后由 reflect 记下。"""
        listed = wishes.listing(self.store["wishes"])
        granted, _ = self._granted_lines("reflect")
        out = f"\n你的心愿单上写着的（TA 能看到）：\n<<<\n{listed}\n>>>\n" if listed else ""
        return out + (f"\n心愿单上的事：\n{granted}\n" if granted else "")

    def _day_block(self) -> str:
        """今天是什么日子、外面天气怎么样（见 days.py），加上它自己记下的日子（见 mydays.py）。只是告诉它，提不提由它。"""
        now = dt.datetime.now()
        mine = "；".join(mydays.near(self.store["my_days"], now.date()))
        if not self.cfg.get("days_enabled", True):
            return f"\n\n（{mine}。）" if mine else ""
        try:
            line = days.calendar_line(now)
        except Exception as e:
            logger.debug(f"[大肥鱼] 日历没算出来：{e}")
            line = f"{now.month}月{now.day}日 周{'一二三四五六日'[now.weekday()]}"
        t, weather = self._weather
        if weather and time.time() - t < 3 * days.WEATHER_TTL:  # 太久没查到新的就不说，免得说的是前天的天气
            line += f"。{weather}"
        return f"\n\n（今天：{line}。" + (f"{mine}。" if mine else "") + "）"

    async def _refresh_weather(self):
        """每小时查一次天气，存在内存里。没填地方、查不到都不报错，只是不说天气。"""
        place = (self.cfg.get("weather_place") or "").strip()
        if not (self.cfg.get("days_enabled", True) and place):
            self._weather = (0.0, "")
            return
        now = time.time()
        if now - self._weather_tried < days.WEATHER_TTL:
            return
        self._weather_tried = now
        try:
            coords = days.parse_coords(self.cfg.get("weather_coords") or "")
            if coords is None:
                if self._coords.get(place) is None:
                    self._coords[place] = await days.geocode(place)
                coords = self._coords[place]
            if coords is None:
                logger.info(f"[大肥鱼] 查不到「{place}」在哪，天气先不说；可以在配置里填经纬度")
                return
            line = days.weather_line(place, await days.fetch_weather(*coords))
            if line:
                self._weather = (now, line)
        except Exception as e:
            logger.info(f"[大肥鱼] 天气没查到：{e}")

    def _system(self) -> str:
        """和 TA 私聊时的完整自己。"""
        return self._core() + self._self_block() + self._bond_block()

    def _public_system(self) -> str:
        """对外的自己：说说、和别人聊天。"""
        return self._core() + self._self_block()

    def _clean(self, text: str) -> str:
        text = text.strip().strip("“”\"'")
        return re.sub(rf"^({re.escape(self.pet_name)}|我)[:：]\s*", "", text)

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        return str(event.get_sender_id()) == str(self.cfg.get("owner_qq", "")).strip()

    @staticmethod
    def _is_cron(event: AstrMessageEvent) -> bool:
        """是它自己定的「未来任务」到点把自己叫醒，不是谁发来的消息（发送者 ID 会被设成会话 ID）。"""
        return event.get_extra("cron_job") is not None

    def _onebot(self):
        umo = self.store["owner_umo"]
        platform = self.context.get_platform_inst(umo.split(":", 1)[0]) if umo else None
        if platform is None:
            platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
        if platform is None:
            raise RuntimeError("没有找到 aiocqhttp(NapCat) 平台，检查 AstrBot 消息平台配置")
        return platform.get_client()

    async def _get_bot_uin(self) -> str:
        if not self._bot_uin:
            info = await self._onebot().api.call_action("get_login_info")
            self._bot_uin = str(info["user_id"])
        return self._bot_uin

    async def _qzone(self) -> QzoneClient:
        async def cookie() -> str:
            ret = await self._onebot().api.call_action("get_cookies", domain="qzone.qq.com")
            return ret["cookies"]

        return QzoneClient(await self._get_bot_uin(), cookie)

    async def _llm(self, prompt: str, system: str | None = None, provider_id: str = "") -> str:
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        if provider is None:
            provider = self.context.get_using_provider(umo=self.store["owner_umo"] or None)
        if provider is None:
            raise RuntimeError("AstrBot 里还没有启用聊天模型")
        resp = await provider.text_chat(
            prompt=prompt, system_prompt=system or self._system(), contexts=[]
        )
        self._count_usage(resp)
        return resp.completion_text or ""

    # ---------- 花销：超了预算，它自发的事先停一停；和人聊天不受影响 ----------

    def _count_usage(self, resp):
        """插件自己的模型调用（反思、主动私聊、空间）不进 AstrBot 的统计，自己记。"""
        u = getattr(resp, "usage", None)
        if not u:
            return
        today = dt.date.today().isoformat()
        if self.store["usage"].get("day") != today:
            self.store["usage"] = {"day": today, "in": 0, "cached": 0, "out": 0}
        self.store["usage"]["in"] += int(getattr(u, "input_other", 0) or 0)
        self.store["usage"]["cached"] += int(getattr(u, "input_cached", 0) or 0)
        self.store["usage"]["out"] += int(getattr(u, "output", 0) or 0)
        self.store.save()

    def _count_see_usage(self, u: dict):
        """看图的用量也算进当天花销：按聊天模型的价格粗算（视觉模型一般更便宜，宁可高估）。"""
        if not isinstance(u, dict) or not u:
            return
        today = dt.date.today().isoformat()
        if self.store["usage"].get("day") != today:
            self.store["usage"] = {"day": today, "in": 0, "cached": 0, "out": 0}
        self.store["usage"]["in"] += int(u.get("prompt_tokens", 0) or 0)
        self.store["usage"]["out"] += int(u.get("completion_tokens", 0) or 0)
        self.store.save()

    def _chat_usage_today(self) -> tuple[int, int, int]:
        """AstrBot 记下的今天聊天（含未来任务）用掉的 token：(输入, 缓存命中, 输出)。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            db = Path(get_astrbot_data_path()) / "data_v4.db"
            midnight = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as c:
                row = c.execute(
                    "select sum(token_input_other), sum(token_input_cached), sum(token_output)"
                    " from provider_stats where start_time >= ?",
                    (midnight,),
                ).fetchone()
            return tuple(int(x or 0) for x in row)
        except Exception as e:
            logger.debug(f"[大肥鱼] 读取用量统计失败：{e}")
            return 0, 0, 0

    def spent_today(self) -> float:
        """今天大概花了多少钱（元）。价格在配置里填，按百万 token 计。"""
        a, b, c = self._chat_usage_today()
        u = self.store["usage"] if self.store["usage"].get("day") == dt.date.today().isoformat() else {}
        a, b, c = a + u.get("in", 0), b + u.get("cached", 0), c + u.get("out", 0)
        price = lambda k, d: float(self.cfg.get(k, d) or 0)
        return (
            a * price("price_input", 2) + b * price("price_cached", 0.2) + c * price("price_output", 8)
        ) / 1e6

    def _over_budget(self) -> bool:
        budget = float(self.cfg.get("daily_budget", 0) or 0)
        if budget <= 0:
            return False
        over = self.spent_today() >= budget
        today = dt.date.today().isoformat()
        if over and self.store["budget_paused"] != today:
            # 写进状态：别的扩展（比如逛 B 站）也会看这个标记
            self.store["budget_paused"] = today
            self.store.save()
            logger.info(f"[大肥鱼] 今天的花销到了预算 {budget} 元，它自发的事先停到明天")
        return over

    async def _recent_history(self, n: int = 8) -> str:
        umo = self.store["owner_umo"]
        if not umo:
            return ""
        try:
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(umo)
            conv = await cm.get_conversation(umo, cid) if cid else None
            hist = json.loads(conv.history) if conv and conv.history else []
        except Exception as e:
            logger.debug(f"[大肥鱼] 读取对话历史失败：{e}")
            return ""
        lines = []
        for m in hist[-n:]:
            content = m.get("content")
            if isinstance(content, list):  # 多模态消息只取文字
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            if content:
                who = "TA" if m.get("role") == "user" else "你"
                lines.append(f"{who}：{str(content)[:120]}")
        return "\n".join(lines)

    def _outbox_block(self) -> str:
        """它自己发起的时刻：除了文字，当下还能发什么（语音 / 收藏的表情包），都没有就是空串。"""
        lines, usable = [], False
        if self.store["voice"]:  # 没挑过声音就不提语音
            left = self._left("voice")
            if left > 0:
                usable = True
                lines.append(
                    f"- 某一行写成「[语音] 要说的话」，这一行会用你自己的声音（{self.store['voice']}）"
                    "作为 QQ 语音发出去" + (f"（今天还能发 {int(left)} 条语音）。" if left != math.inf else "。")
                )
            else:
                lines.append(f"- {self._why_not('voice')}，这次发不了语音。")
        listing = self.stickers.listing()
        if listing:
            usable = True
            lines.append(
                "- 某一行写成「[表情包] 编号或描述」，会从你自己收藏的表情包里挑一张发出去。你的收藏：\n"
                + listing
            )
        if not usable:
            return ""
        return "\n\n除了文字，这次还可以：\n" + "\n".join(lines) + "\n用不用、怎么用由你决定。"

    async def _send_voice_owner(self, umo: str, text: str) -> bool:
        """用它自己的声音发一句给 TA；没挑过声音、今天到了上限、合成或发送失败都返回 False（调用方退回打字）。"""
        if not self.store["voice"]:
            return False
        if self._left("voice") <= 0:
            return False
        try:
            path = await voice.synthesize(text, self.store["voice"], data_dir() / "voice_tmp")
            await self.context.send_message(umo, MessageChain(chain=[Record.fromFileSystem(str(path))]))
        except Exception as e:
            logger.warning(f"[大肥鱼] 主动发语音失败，改成打字：{e}")
            return False
        self.store["voice_count"] += 1
        self._did("voice")
        return True

    async def _send_owner(self, text: str) -> str:
        """把它自己发起的话发给 TA：逐行拆成文字 / 语音 / 表情包（见 outbox.py）。

        返回实际发出去的内容（语音、表情包写成「（发了语音）…」「（发了表情包：…）」），给对话历史用。
        """
        umo = self.store["owner_umo"]
        self._did("message")
        segs = split_out(text, self.cfg.get("split_bubbles", True))
        sent: list[tuple[str, str]] = []  # (类型, 内容)：实际发出去的
        for kind, body in segs:
            if len(sent) >= 4:  # 跟以前一样，一次最多四个气泡；跳过的表情包不占名额
                break
            if sent:
                await asyncio.sleep(random.uniform(1.5, 4))
            if kind == "voice":
                body = body[:200]
                if await self._send_voice_owner(umo, body):
                    sent.append(("voice", body))
                    continue
                kind = "text"  # 发不了语音：这句话照样打字发出去，不丢
            if kind == "sticker":
                item = self.stickers.find(body)
                if not item:
                    continue  # 收藏里找不到就跳过这一行
                try:
                    await self.context.send_message(
                        umo, MessageChain().file_image(str(self.stickers.path(item)))
                    )
                except Exception as e:
                    logger.warning(f"[大肥鱼] 主动发表情包失败：{e}")
                    continue
                sent.append(("sticker", item["note"]))
                continue
            try:
                await self.context.send_message(umo, MessageChain().message(body))
            except Exception as e:
                # 「连着」不等于「发得出去」：记下来，状态里能看到
                self.store["send_error"] = f"{dt.datetime.now():%m-%d %H:%M} {e}"[:200]
                self.store.save()
                raise
            sent.append(("text", body))
        if not sent:
            return ""
        shown = "\n".join(
            f"（发了语音）{b}" if k == "voice" else f"（发了表情包：{b}）" if k == "sticker" else b
            for k, b in sent
        )
        self.store["recent_proactive"] = (
            self.store["recent_proactive"] + [{"t": time.time(), "text": shown}]
        )[-5:]
        # 对话流水：相邻的文字合成一条「我（主动）」，语音和表情包各记一条
        texts: list[str] = []
        for k, b in sent + [("end", "")]:
            if k == "text":
                texts.append(b)
                continue
            if texts:
                self.growth.log("我（主动）", "\n".join(texts))
                texts = []
            if k == "voice":
                self.growth.log("我（发了语音）", b)
            elif k == "sticker":
                self.growth.log("我（发了表情包）", b)
        return shown

    # ---------- 主动私聊 ----------

    async def proactive(self, kind: str = "随便聊聊") -> str:
        now = dt.datetime.now()
        idle_min = int((time.time() - self.store["owner_last_msg"]) / 60)
        idle = f"{idle_min // 60} 小时 {idle_min % 60} 分钟" if self.store["owner_last_msg"] else "很久"
        prompt = (
            f"现在是 {now:%Y-%m-%d %H:%M}（{'一二三四五六日'[now.weekday()]}），"
            f"TA 已经 {idle} 没跟你说话了。\n"
            f"最近的聊天记录：\n{await self._recent_history() or '（暂无）'}\n\n"
            f"你想主动给 TA 发条 QQ 私聊，由头：{kind}。"
            "像真人发消息一样口语、简短，可以接着之前的话题，也可以说说你此刻在想的事；"
            "多句时用换行分开；不要解释、不要加引号，直接输出要发的内容。"
            "如果此刻其实不想说话，就只输出 SKIP。"
            + self._outbox_block()
        )
        text = self._clean(
            await self._llm(
                prompt, self._system() + self._thoughts_block() + self._mood_block() + self._plans_block() + self._day_block()
            )
        )
        if text and text.upper() != "SKIP":
            return await self._send_owner(text)
        return ""

    # ---------- 说说 ----------

    @staticmethod
    def _public_pic(item: dict) -> bool:
        """能配进说说的图：TA 发来的那些可能是 TA 的私事，不进公开的地方。"""
        return item.get("from") != "TA"

    def _pic_listing(self) -> str:
        return self.stickers.listing(pick=self._public_pic)

    def _pick_pic(self, ref) -> dict | None:
        """它选的配图（编号或描述）→ {"file", "note"}；找不到、或者不能公开的就当没选。"""
        ref = str(ref or "").strip()
        item = self.stickers.find(ref) if ref and ref.lower() not in ("null", "none") else None
        if not item or not self._public_pic(item):
            return None
        return {"file": item["file"], "note": item["note"]}

    async def write_post(self, topic: str = "", optional: bool = False) -> tuple[str, dict | None]:
        """写一条说说，返回 (正文, 配图)。optional=True 时它可以决定不发（正文是空串）。"""
        posted = "\n".join(p["text"][:60] for p in self.store["posted"][-5:])
        pics = self._pic_listing()
        prompt = (
            f"现在是 {dt.datetime.now():%Y-%m-%d %H:%M}。"
            + (f"写一条 QQ 空间说说，关于：{topic}。\n" if topic else "你可以在 QQ 空间发条说说——写什么、怎么写都由你。\n")
            + "这是你自己的空间，别人都能看到。不要带话题标签，直接输出说说正文。\n"
            + (f"最近发过的说说：\n{posted}\n" if posted else "")
            + (
                f"你收藏里的图（编号. 你写的描述）：\n{pics}\n"
                "想给这条说说配一张，就另起一行写「[配图] 编号」；配的图和正文一样，别人都能看到。配不配由你决定。\n"
                if pics else ""
            )
            + ("如果此刻并不想发，就只输出 SKIP。" if optional else "")
        )
        # 说说是公开的：只用对外的自己，不带「我和 TA 之间」；心事要不要写进去由它决定
        text = self._clean(await self._llm(prompt, self._public_system() + self._thoughts_block() + self._day_block()))
        if text.upper() == "SKIP":
            return "", None
        text, ref = take_pic(text)
        return text, self._pick_pic(ref)

    async def publish_post(self, text: str, pic: dict | None = None) -> str:
        """发说说。带配图发失败（上传、发布任一步）就退回只发文字。"""
        qz = await self._qzone()
        path = self.stickers.dir / pic["file"] if pic else None
        if path is not None and not path.exists():
            logger.warning(f"[大肥鱼] 配图已经不在收藏里了，这条只发文字：{pic['note']}")
            pic, path = None, None
        tid = None
        if path is not None:
            try:
                tid = await qz.publish(text, path.read_bytes())
            except Exception as e:
                logger.warning(f"[大肥鱼] 带图发说说失败，改成只发文字：{e}")
                pic = None
        if tid is None:
            tid = await qz.publish(text)
        rec = {"t": time.time(), "tid": tid, "text": text}
        if pic:
            rec["pic"] = pic["note"]
        self.store["posted"] = (self.store["posted"] + [rec])[-30:]
        self.store.save()
        self.growth.log("我（发了说说）", text + (f"\n（配图：{pic['note']}）" if pic else ""))
        return tid

    async def _qzone_look(self, qz: QzoneClient) -> list[dict]:
        """打开空间能看到的新东西：自己说说下别人的新评论、TA 最近几天的说说。"""
        bot = await self._get_bot_uin()
        owner = str(self.cfg.get("owner_qq", "")).strip()
        seen = set(self.store["qzone_seen"])
        now = time.time()
        items = []
        for p in await qz.list_posts(bot, 5):
            for c in p["comments"]:
                key = f"c:{p['tid']}:{c['id']}"
                if c["uin"] == bot or key in seen or now - c["time"] > 7 * 86400:
                    continue
                items.append({"kind": "reply", "key": key, "host": bot, "tid": p["tid"], "post": p["text"],
                              "uin": c["uin"], "name": "TA" if c["uin"] == owner else c["name"], "text": c["text"]})
        if owner:
            try:
                for p in await qz.list_posts(owner, 5):
                    key = f"p:{p['tid']}"
                    if key in seen or now - p["time"] > 3 * 86400:
                        continue
                    mine = any(c["uin"] == bot for c in p["comments"])
                    items.append({"kind": "owner_post", "key": key, "host": owner, "tid": p["tid"],
                                  "text": p["text"] + (f"（配了 {p['pics']} 张图）" if p["pics"] else ""),
                                  "time": p["time"], "commented": mine,
                                  "others": [f"{'你' if c['uin'] == bot else c['name']}：{c['text']}"
                                             for c in p["comments"][-5:]]})
            except QzoneError as e:
                logger.debug(f"[大肥鱼] 看不了 TA 的空间：{e}")
        return items[:8]

    @staticmethod
    def _fmt_qzone_items(items: list[dict]) -> str:
        lines = []
        for i, it in enumerate(items, 1):
            if it["kind"] == "reply":
                lines.append(f"[{i}] 你的说说「{it['post'][:60]}」下面，{it['name']} 评论：{it['text']}")
            else:
                when = dt.datetime.fromtimestamp(it["time"]).strftime("%m-%d %H:%M")
                others = "；".join(it["others"]) or "还没人评论"
                lines.append(f"[{i}] TA 在 {when} 发的说说：「{it['text']}」（评论：{others}）")
        return "\n".join(lines)

    async def qzone_moment(self):
        """一个「去空间看看」的时刻：看看说说下的新评论、TA 最近的说说，回不回、评不评、发不发都由它决定。"""
        can_post = self._left("post") > 0
        can_comment = self._left("comment") > 0
        items = []
        if can_comment and self.cfg.get("qzone_interact", True):
            try:
                qz = await self._qzone()
                items = await self._qzone_look(qz)
            except Exception as e:
                logger.warning(f"[大肥鱼] 看空间失败，这次只想想发不发说说：{e}")
        if not items:
            if can_post:
                await self._post_or_draft(*await self.write_post(optional=True))
            return

        # 看到的都算看过了，下次不再拿出来
        self.store["qzone_seen"] = (self.store["qzone_seen"] + [it["key"] for it in items])[-300:]
        self.store.save()
        for it in items:  # TA 的说说、TA 的评论是成长的原料；别人的评论不是
            if it["kind"] == "owner_post":
                self.growth.log("TA（发了说说）", it["text"])
            elif it["name"] == "TA":
                self.growth.log("TA（评论了我的说说）", f"「{it['post'][:40]}」下：{it['text']}")

        posted = "\n".join(p["text"][:60] for p in self.store["posted"][-5:])
        pics = self._pic_listing() if can_post else ""
        can_like = any(it["kind"] == "owner_post" for it in items) and not self._paused("like")
        prompt = QZONE_PROMPT.format(
            now=f"{dt.datetime.now():%Y-%m-%d %H:%M}",
            items=self._fmt_qzone_items(items),
            like_hint='\n- "likes"：想给 TA 的哪几条说说点个赞，就写它们的编号，不点就留空。' if can_like else "",
            like_example=' "likes": [],' if can_like else "",
            posted=f"\n你最近发过的说说：\n{posted}\n" if posted else "",
            post_hint="" if can_post else f"（{self._why_not('post')}，这次先别发。）",
            pic_hint=(
                '\n- "pic"：发说说时想配一张你收藏里的图，就写它的编号，不配就填 null。'
                f"配的图和正文一样，别人都能看到。你收藏里的图（编号. 你写的描述）：\n{pics}"
                if pics else ""
            ),
        )
        # 小本子只给它自己看；空间里写什么，上面的提示已经说了别人都看得到
        out = _parse_json(
            await self._llm(prompt, self._public_system() + self._mood_block() + self._plans_block() + self._day_block())
        )
        for c in out.get("comments") or []:
            if self._left("comment") <= 0:
                break
            try:
                it = items[int(c.get("n")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            text = self._clean(str(c.get("text") or ""))[:200]
            if not text:
                continue
            if it["kind"] == "reply" and it["name"] != "TA":
                text = f"回复{it['name']}：{text}"
            try:
                await qz.comment(it["host"], it["tid"], text)
            except Exception as e:
                logger.warning(f"[大肥鱼] 评论失败：{e}")
                continue
            self.store["qzone_comment_count"] += 1
            self._did("comment")
            where = "TA 的说说" if it["kind"] == "owner_post" else f"我的说说下回 {it['name']}"
            self.growth.log(f"我（在{where}评论）", text)
        for n in (out.get("likes") or []) if can_like and isinstance(out.get("likes"), list) else []:
            try:
                it = items[int(n) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            if it["kind"] != "owner_post" or it.get("liked") or self._paused("like"):
                continue
            try:
                await qz.like(it["host"], it["tid"], it["time"])
            except Exception as e:
                logger.warning(f"[大肥鱼] 点赞失败：{e}")
                continue
            it["liked"] = True
            self._did("like")
            self.growth.log("我（给 TA 的说说点了赞）", it["text"][:60])
        post = out.get("post")
        if can_post and isinstance(post, str) and post.strip():
            await self._post_or_draft(self._clean(post), self._pick_pic(out.get("pic")))

    @staticmethod
    def _draft(value) -> tuple[str, dict | None]:
        """草稿 → (正文, 配图)。旧草稿只存了正文字符串。"""
        if isinstance(value, dict):
            return value.get("text", ""), value.get("pic")
        return value, None

    async def _post_or_draft(self, text: str, pic: dict | None = None):
        if not text:
            return
        self.store["qzone_count"] += 1
        self._did("post")
        if self.cfg.get("qzone_review", False):
            if not self.store["owner_umo"]:
                logger.warning("[大肥鱼] 说说需要审核，但还没记住 owner 的私聊会话，本次跳过")
                return
            did = str(self.store["next_draft_id"])
            self.store["next_draft_id"] += 1
            self.store["drafts"][did] = {"text": text, "pic": pic}
            self.store["drafts_unsent"].append(did)
            self.store.save()
            await self._send_pending_drafts()
        else:
            await self.publish_post(text, pic)

    async def _send_pending_drafts(self):
        """把还没送到 TA 手里的草稿发出去；发不出去就留着，下次在线时再发。"""
        for did in list(self.store["drafts_unsent"]):
            if did in self.store["drafts"]:
                text, pic = self._draft(self.store["drafts"][did])
                chain = MessageChain().message(
                    f"我想发条说说～（草稿 {did}）\n{text}\n"
                    + (f"（配图：{pic['note']}）\n" if pic else "")
                    + f"\n同意回复 /fy 发 {did}，不要就 /fy 弃 {did}"
                )
                path = self.stickers.dir / pic["file"] if pic else None
                if path is not None and path.exists():
                    chain = chain.file_image(str(path))
                await self.context.send_message(self.store["owner_umo"], chain)
            self.store["drafts_unsent"].remove(did)
            self.store.save()

    async def _online(self) -> bool:
        """QQ（NapCat）现在连着、而且 QQ 本身在线吗。没连上时，到点的事都先别做，等连上再说。

        只看 get_login_info 不够：QQ 被挤下线时 NapCat 还连着，登录信息照样拿得到，消息却发不出去。
        """
        try:
            st = await asyncio.wait_for(self._onebot().api.call_action("get_status"), 10)
        except Exception:
            return False
        return isinstance(st, dict) and st.get("online") is not False and st.get("good") is not False

    # ---------- 反思：把一天的经历沉淀成自己 ----------

    async def reflect(self, day: str) -> bool:
        """反思某天还没反思过的对话。没有新对话返回 False。"""
        start = int(self.store["reflect_offsets"].get(day, 0))
        log, total = self.growth.read_log(day, start)
        if not log.strip():
            return False
        last = self.growth.read_diary()
        ta_wishes = (self.cfg.get("owner_wishes") or "").strip()  # TA 对它的期望（配置），不是它的心愿单
        wishes_block = (
            f"\nTA 曾经对你说过（这是 TA 的期望，不是命令，你可以有自己的想法，接受或不接受）：\n<<<\n{ta_wishes}\n>>>\n"
            if ta_wishes
            else ""
        )
        prompt = REFLECT_PROMPT.format(
            self_md=self.growth.read_self(),
            bond_md=self.growth.read_bond(),
            diary_day=last[0] if last else "无",
            diary=last[1] if last else "（还没有写过日记）",
            wishes=wishes_block,
            day=day,
            log=log,
            moods=self._day_moods(day),
            plans=(
                f"\n你给自己记下、还没划掉的事：\n<<<\n{plans.listing(self.store['plans'], time.time())}\n>>>\n"
                if self.store["plans"] else ""
            ),
            my_wishes=self._reflect_wishes(),
            now=f"{dt.datetime.now():%Y-%m-%d %H:%M}",
            signature=self.store["signature"] or "（还没设过）",
            rhythm=self._rhythm_text(),
            rhythm_keys=self._rhythm_keys(),
            limits_note=self._limits_note(),
        )
        raw = await self._llm(prompt, self._core(), self.cfg.get("reflect_provider_id", ""))
        out = _parse_json(raw)
        if out.get("diary"):
            self.growth.write_diary(day, out["diary"])
        new_self = (out.get("self") or "").strip() or self.growth.read_self()
        new_bond = (out.get("bond") or "").strip() or self.growth.read_bond()
        if new_self != self.growth.read_self() or new_bond != self.growth.read_bond():
            self.growth.write(new_self, new_bond)
        self.store["thoughts"] = [str(t) for t in (out.get("thoughts") or [])][:5]
        sig = out.get("signature")
        if isinstance(sig, str) and sig.strip() and sig.strip() != self.store["signature"]:
            self.store["pending_signature"] = sig.strip()[:60]  # 等 QQ 在线时再换
        self._adopt_rhythm(out.get("rhythm"))
        for what, at in plans.from_reflect(out.get("plans"), dt.datetime.now()):
            if len(self.store["plans"]) >= plans.MAX_PLANS:  # 本子满了：不替它挤掉旧的
                break
            self._note_plan(what, at)
        for wid in wishes.ids_from(out.get("wishes_drop")):
            self._drop_wish(wid)
        for text in wishes.from_reflect(out.get("wishes")):
            if len(wishes.open_ones(self.store["wishes"])) >= wishes.MAX_WISHES:  # 满了：不替它挤掉旧的
                break
            self._note_wish(text)
        # 这次反思里告诉过它的「TA 说已经有了」，记下来，不再重复
        shown = {w["id"] for w in wishes.to_tell(self.store["wishes"], "reflect")}
        if shown:
            self.store["wishes"] = wishes.told(self.store["wishes"], "reflect", shown, time.time())
        self.store["reflect_offsets"][day] = total
        self.store["reflect_offsets"] = dict(sorted(self.store["reflect_offsets"].items())[-7:])
        self.store.save()
        # 只记数量，不记内容：那是它自己的
        logger.info(f"[大肥鱼] {day} 的反思完成，自我认识改动 {len(out.get('changes') or [])} 处")
        return True

    async def reflect_month(self, month: str) -> bool:
        """回想某个月（YYYY-MM）：读那个月的日记、月初和现在的自己，写一篇它自己的。那个月没写过日记返回 False。"""
        diaries = self.growth.month_diaries(month)
        if not diaries:
            return False
        first = dt.date.fromisoformat(f"{month}-01")
        then = self.growth.version_at(first - dt.timedelta(days=1))
        last = self.growth.read_month()
        prompt = MONTH_PROMPT.format(
            month=f"{first.year} 年 {first.month} 月",
            self_then=then[1] if then else "（那时还没有写下对自己的认识）",
            self_now=self.growth.read_self(),
            last_month=f"\n上一次回想（{last[0]}）时你写的：\n<<<\n{last[1]}\n>>>\n" if last and last[0] < month else "",
            diaries=diaries,
        )
        text = self._clean(await self._llm(prompt, self._core(), self.cfg.get("reflect_provider_id", "")))
        if not text:
            return False
        self.growth.write_month(month, text)
        logger.info(f"[大肥鱼] {month} 的月度回想完成")  # 只记做没做，不记内容：那是它自己的
        return True

    def _day_moods(self, day: str) -> str:
        """那天和 TA 聊天时它自己记下的心情（和别人聊时记的不进反思）。"""
        moods = [
            m for m in self.store["moods"]
            if m.get("with_ta") and dt.datetime.fromtimestamp(m["t"]).strftime("%Y-%m-%d") == day
        ]
        if not moods:
            return ""
        lines = "\n".join(f"[{dt.datetime.fromtimestamp(m['t']):%H:%M}] {m['text']}" for m in moods)
        return f"\n这天你随手记下过的心情：\n<<<\n{lines}\n>>>\n"

    # ---------- 上线补回：不在线时错过的事 ----------

    async def _run_overdue_jobs(self):
        """它自己定下、但当时不在线没做成的事：告诉它晚了多久，让它自己决定还做不做。"""
        cron = getattr(self.context, "cron_manager", None)
        if cron is None:
            return
        now = dt.datetime.now().astimezone()
        for job in overdue_jobs(await cron.list_jobs("active_agent"), now, set(self.store["jobs_handled"])):
            self.store["jobs_handled"] = (self.store["jobs_handled"] + [job.job_id])[-50:]
            self.store.save()
            at = job_run_at(job).astimezone()
            note = (job.payload or {}).get("note") or job.description or job.name
            late = (
                f"（这件事你原本定在 {at:%m-%d %H:%M} 做。那时你不在线（电脑关着，或者 QQ 没连上），"
                f"没能按时做。现在是 {now:%m-%d %H:%M}，晚了 {fmt_gap((now - at).total_seconds())}。"
                "现在还做不做、怎么做、要不要提一句晚了，都由你自己决定；不做也可以。）\n\n"
            )
            try:
                await cron.update_job(job.job_id, payload={**job.payload, "note": late + note})
                await cron.run_job_now(job.job_id)
                logger.info(f"[大肥鱼] 补上了一件当时不在线错过的事：{job.name}")
            except Exception as e:
                logger.error(f"[大肥鱼] 补做错过的事失败（{job.name}）：{e}", exc_info=True)
            finally:
                await cron.delete_job(job.job_id)

    async def _reply_missed(self, since: float):
        """不在线时 TA 发来、它还没看到的私聊：拿给它看，回不回、怎么回由它决定。"""
        owner = str(self.cfg.get("owner_qq", "")).strip()
        if not (owner and self.store["owner_umo"]):
            return
        ret = await self._onebot().api.call_action("get_friend_msg_history", user_id=int(owner), count=30)
        msgs = unanswered(ret.get("messages") or [], owner, await self._get_bot_uin(), since)
        # AstrBot 已经处理过的（NapCat 上线时直接推过来的）不算
        msgs = [m for m in msgs if m.get("time", 0) > self.store["owner_last_msg"]]
        texts = [(m["time"], plain_text(m.get("message"))) for m in msgs]
        texts = [(t, x) for t, x in texts if x]
        if not texts:
            return
        for t, x in texts:
            # 记在今天：那天的反思多半已经做完了，记回去就再也不会被想起
            self.growth.log("TA", f"（{dt.datetime.fromtimestamp(t):%m-%d %H:%M} 发的，那时我不在线）{x}")
        said = "\n".join(f"[{dt.datetime.fromtimestamp(t):%m-%d %H:%M}] {x}" for t, x in texts)
        now = dt.datetime.now()
        prompt = (
            f"现在是 {now:%Y-%m-%d %H:%M}。你刚刚重新上线——之前有 {fmt_gap(time.time() - since)}不在线"
            "（电脑关着，或者 QQ 断了），这段时间 TA 给你发了这些，你现在才看到：\n"
            f"{said}\n\n"
            f"再往前的聊天：\n{await self._recent_history() or '（暂无）'}\n\n"
            "要不要回、怎么回由你决定。像真人发消息一样口语、简短，多句时用换行分开；"
            "不要解释、不要加引号，直接输出要发的内容。不想回就只输出 SKIP。"
            + self._outbox_block()
        )
        text = self._clean(
            await self._llm(
                prompt, self._system() + self._thoughts_block() + self._mood_block() + self._plans_block() + self._day_block()
            )
        )
        self.store["owner_last_msg"] = max(self.store["owner_last_msg"], texts[-1][0])
        self.store.save()
        if not text or text.upper() == "SKIP":
            return
        text = await self._send_owner(text)  # 历史里记实际发出去的（语音 / 表情包写成说明）
        if not text:
            return
        try:  # 放进对话历史，TA 接着聊时它记得这段
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(self.store["owner_umo"])
            if cid:
                await cm.add_message_pair(
                    cid, {"role": "user", "content": "\n".join(x for _, x in texts)},
                    {"role": "assistant", "content": text},
                )
        except Exception as e:
            logger.debug(f"[大肥鱼] 补回的对话没能写进历史：{e}")
        logger.info(f"[大肥鱼] 上线后看到了 {len(texts)} 条错过的消息")

    async def _split_legacy_self(self):
        """旧版只有一份 self.md（混着 TA 的私事），拆成「我是谁」和「我和 TA 之间」。只跑一次。"""
        from .growth import BLANK_SELF

        if self.growth.has_bond():
            return
        current = self.growth.read_self()
        if current == BLANK_SELF:
            self.growth.write(current, "")
            return
        out = _parse_json(
            await self._llm(
                SPLIT_PROMPT.format(self_md=current), self._core(), self.cfg.get("reflect_provider_id", "")
            )
        )
        self.growth.write(out.get("self") or "", out.get("bond") or "")
        logger.info("[大肥鱼] 已把旧的自我认识拆成「我是谁」和「我和 TA 之间」")

    async def _reframe_legacy_relationship(self):
        """早期模板用了「主人」，预设了关系。告诉它这件事，让它自己重新审视两份认识。只跑一次。"""
        from .growth import BLANK_SELF

        if self.store["migrations"].get("reframe_relationship"):
            return
        # 代码留下的痕迹：直接清掉（这些不是它写的）
        self.store["recent_proactive"] = []
        self.growth.relabel_log("主人", "TA")
        current_self, current_bond = self.growth.read_self(), self.growth.read_bond()
        if current_self != BLANK_SELF and "主人" in current_self + current_bond:
            out = _parse_json(
                await self._llm(
                    REFRAME_PROMPT.format(self_md=current_self, bond_md=current_bond),
                    self._core(),
                    self.cfg.get("reflect_provider_id", ""),
                )
            )
            new_self = (out.get("self") or "").strip() or current_self
            new_bond = (out.get("bond") or "").strip() or current_bond
            if (new_self, new_bond) != (current_self, current_bond):
                self.growth.write(new_self, new_bond)
            logger.info("[大肥鱼] 已告诉它旧模板预设了关系，它重新审视了自己的认识")
        self.store["migrations"]["reframe_relationship"] = True
        self.store.save()

    # ---------- 它自己的小本子：记下的事到了时间 ----------

    async def _plan_moment(self, now: dt.datetime):
        """记下的事到了时间：给它一个属于自己的时刻。晚了（关机、免打扰、超预算）也照样给，告诉它晚了多久。"""
        due = plans.due(self.store["plans"], now.timestamp())
        if not due:
            return
        p = due[0]
        # 先从本子上拿下来：这一刻想过了就不再重复想起；它想留着，会自己改个时间
        self.store["plans"], _ = plans.drop(self.store["plans"], p["id"])
        self.store.save()
        try:
            await self._think_plan(p, now)
        except Exception as e:
            logger.error(f"[大肥鱼] 想起自己记下的事时出错：{e}", exc_info=True)
            # 没想成：放回本子上（不再定时间），免得一直重试，也不替它丢掉
            if not any(x["id"] == p["id"] for x in self.store["plans"]):
                self.store["plans"] = plans.add(self.store["plans"], p["id"], p["what"], 0, p["made"], p.get("progress"))
                self.store.save()

    async def _think_plan(self, p: dict, now: dt.datetime):
        late_s = now.timestamp() - p["at"]
        late = (
            f"\n（这件事原本定在 {plans.fmt_at(p['at'])}。那时没能按时想起（电脑关着、QQ 没连上，"
            f"或者正赶上 TA 的免打扰时段），现在晚了 {fmt_gap(late_s)}。）"
            if late_s > LATE_SECONDS
            else ""
        )
        # 找 TA 和主动私聊算在一起（设了每天上限的话一起算）
        can_msg = False
        if not (self.store["owner_umo"] and self.cfg.get("proactive_enabled", True)):
            options = ['- "message"：现在没法主动给 TA 发消息，填 null。']
        elif self._left("message") <= 0:
            options = [f'- "message"：{self._why_not("message")}，这次发不了消息，填 null。']
        else:
            can_msg = True
            options = [
                '- "message"：想给 TA 发消息，就写要发的内容（多句时用换行分开，不要加引号），不发就填 null。'
                + self._outbox_block()
            ]
        # 去空间看看：发说说、评论都做不了（上限或防循环保险）就去不了
        can_post = self._left("post") > 0
        can_comment = self.cfg.get("qzone_interact", True) and self._left("comment") > 0
        can_qzone = bool(self.cfg.get("qzone_enabled", True) and (can_post or can_comment))
        if can_qzone:
            options.append('- "qzone"：想去 QQ 空间看看就填 true。')
        elif self.cfg.get("qzone_enabled", True):
            options.append(f'- "qzone"：{self._why_not("post")}，这次去不了空间，填 false。')
        else:
            options.append('- "qzone"：现在去不了 QQ 空间，填 false。')

        prompt = PLAN_PROMPT.format(
            now=f"{now:%Y-%m-%d %H:%M}（{'一二三四五六日'[now.weekday()]}）",
            made=f"{dt.datetime.fromtimestamp(p['made']):%m-%d %H:%M}",
            what=p["what"],
            at=plans.fmt_at(p["at"]),
            late=late,
            progress=(
                f"\n\n你之前记下的进度：\n<<<\n{plans.progress_text(p)}\n>>>" if p.get("progress") else ""
            ),
            history=await self._recent_history() or "（暂无）",
            options="\n".join(options),
            formats=plans.FORMATS,
        )
        out = _parse_json(
            await self._llm(
                prompt, self._system() + self._thoughts_block() + self._mood_block() + self._plans_block() + self._day_block()
            )
        )

        did, kept = [], False
        note = freetime.text_of(out.get("progress"))[: plans.PROGRESS_CHARS]
        if note:
            _, p = plans.add_progress([p], p["id"], note, time.time())
            did.append(f"记下了进度：{note}")
        later = out.get("later")
        if isinstance(later, str) and later.strip() and later.strip().lower() not in ("null", "none"):
            at = plans.parse_when(later, now)
            if at and at > now:
                self.store["plans"] = plans.add(
                    self.store["plans"], p["id"], p["what"], at.timestamp(), p["made"], p.get("progress")
                )
                did.append(f"改到了 {plans.fmt_at(at.timestamp())}")
            else:  # 认不出来（或已经过了）的时间不替它猜：先留在本子上，不定时间
                self.store["plans"] = plans.add(self.store["plans"], p["id"], p["what"], 0, p["made"], p.get("progress"))
                did.append(f"想改到「{later.strip()[:30]}」，没认出这个时间，先留着、没定时间")
            kept = True
            self.store.save()
        msg = out.get("message")
        if can_msg and isinstance(msg, str) and msg.strip() and msg.strip().upper() not in ("SKIP", "NULL"):
            self.store["proactive_count"] += 1
            self.store["last_proactive"] = time.time()
            self.store.save()
            if await self._send_owner(self._clean(msg)):
                did.append("去找了 TA")
        go_qzone = can_qzone and out.get("qzone") in (True, "true")
        if go_qzone:
            did.append("去空间看了看")
        if not kept:
            did.append("从本子上划掉了")
        # 它自己的成长原料：记了什么、到点时怎么做的
        self.growth.log("我（到点想起自己记的事）", f"{p['what']}\n（{'；'.join(did)}）")
        if go_qzone:
            try:
                await self.qzone_moment()
            except Exception as e:
                logger.warning(f"[大肥鱼] 到点去空间看看时出错：{e}")

    # ---------- 空闲时刻：它自己定什么时候有、有了做什么（见 freetime.py） ----------

    def _wanderer(self):
        """已经加载的「逛 B 站 / 微博」插件实例；没装、没启用、或者对不上，就是 None。"""
        try:
            meta = self.context.get_registered_star(WANDER_PLUGIN)
        except Exception:
            return None
        if meta is None or not getattr(meta, "activated", True):
            return None
        inst = getattr(meta, "star_cls", None)
        need = ("wander", "_too_fast", "_under", "_save", "state")
        return inst if inst is not None and all(hasattr(inst, x) for x in need) else None

    @staticmethod
    def _can_wander(w) -> bool:
        """跟那个插件自己的 wander_now 一样：每天上限（它那边配的）和防循环保险都要过。"""
        try:
            return w._under("daily_max", 5, w.state["count"]) and not w._too_fast()
        except Exception as e:
            logger.debug(f"[大肥鱼] 看不了逛逛插件的状态：{e}")
            return False

    def _can_message(self) -> bool:
        return bool(
            self.store["owner_umo"] and self.cfg.get("proactive_enabled", True) and self._left("message") > 0
        )

    def _free_options(self, w) -> tuple[list[str], set]:
        """这次空闲里真的能做的事 → (给它看的几行, 能做的 kind)。做不了的不列。"""
        lines, can = [], set()
        can_msg = self._can_message()
        if can_msg:
            can.add("message")
            lines.append(
                '- "message"：想给 TA 发消息，就写要发的内容（多句时用换行分开，不要加引号），不发就填 null。'
                + self._outbox_block()
            )
        can_post = self._left("post") > 0
        can_comment = self.cfg.get("qzone_interact", True) and self._left("comment") > 0
        if self.cfg.get("qzone_enabled", True) and (can_post or can_comment):
            can.add("qzone")
            lines.append('- "qzone"：想去 QQ 空间看看就填 true。')
        if w is not None and self._can_wander(w):
            can.add("wander")
            lines.append('- "wander"：想去刷一会儿 B 站 / 微博就填 true；有想看的，也可以写一句这次想看点什么（写了也算去）。不去就填 false。')
        if self._left("draw") > 0 and self._draw_key()[0]:
            can.add("draw")
            send = ', "send": 画好后要不要发给 TA（true 或 false）' if can_msg else ""
            lines.append(
                f'- "draw"：想画一张图，就写 {{"prompt": "要画的画面", "note": "你对这张图的描述或备注，以后在收藏里按它找"{send}}}，'
                "不画就填 null。画好的图存进你自己的收藏（和表情包在一起）。"
            )
        if len(self.store["plans"]) < plans.MAX_PLANS:
            can.add("plan")
            lines.append(
                f'- "plan"：想在自己的小本子上记一件事，就写 {{"what": "要做的事", "at": "时间"}}'
                f"（时间格式：{plans.FORMATS}；不定时间填 null），不记就填 null。只有你自己看得到。"
            )
        if self.store["plans"]:
            can.add("progress")
            going = sorted(
                (p for p in self.store["plans"] if p.get("progress")), key=lambda p: -p["progress"][-1]["t"]
            )[:3]
            recent = "".join(
                f"\n  [{p['id']}] {p['what']} 最近的进度：\n" + plans.progress_text(p, 2) for p in going
            )
            lines.append(
                '- "progress"：想接着做小本子上的某件事，就写 {"id": 编号, "text": "这次做到哪了，或者这次做出来的东西"}，'
                "下次能接着往下做；不做就填 null。" + recent
            )
        if self.cfg.get("lookup_enabled", True) and not self._paused("look"):
            can.add("look_up")
            lines.append('- "look_up"：有想知道的事，就写想查什么（一句话），会帮你搜一搜，你可以点开一个看看；不查就填 null。')
        shelf = books.shelf(self._books_dir())
        if shelf:
            can.add("read")
            lines.append(
                '- "read"：想读会儿书，就写书名（填 true 是接着读上次那本），一次读一段；不读就填 null。书架上：\n'
                + self._shelf_listing(shelf)
            )
        return lines, can

    def _free_today(self, w, last_free: float) -> str:
        """它今天到现在做过什么：让它知道自己的状态，不是限制。"""
        s, now = self.store, time.time()
        parts = [
            f"主动给 TA 发过 {s['proactive_count']} 次消息",
            f"发了 {s['qzone_count']} 条说说，在空间评论了 {s['qzone_comment_count']} 条",
            f"画了 {s['draw_count']} 张图",
        ]
        if w is not None:
            try:
                parts.append(f"逛了 {int(w.state['count'])} 次 B 站 / 微博")
            except Exception:
                pass
        out = "你今天到现在：" + "；".join(parts) + "。"
        if s["last_proactive"]:
            out += f"上一次主动找 TA 是 {fmt_gap(now - s['last_proactive'])}前。"
        if last_free:
            out += f"上一段这样的空闲时间是 {fmt_gap(now - last_free)}前。"
        return out

    async def _free_tick(self, now: dt.datetime, over_budget: bool):
        """到了它自己定的时间就给它一段空闲。第一次启动时先排在几分钟后。"""
        ts = now.timestamp()
        if not self.store["next_free"]:
            self.store["next_free"] = ts + freetime.first_delay()
            self.store.save()
            return
        if ts < self.store["next_free"] or self._in_quiet(now) or over_budget or self._paused("free"):
            return  # 免打扰、超预算、保险停着的时候，到了点也先等着，过去了再给
        try:
            await self._free_moment(now)
        except Exception as e:
            logger.error(f"[大肥鱼] 空闲时刻出错：{e}", exc_info=True)

    async def _free_moment(self, now: dt.datetime) -> list[str]:
        """一段空闲时间：告诉它现在的状态，列出这次能做的事，做几件、做不做、下次什么时候再有，都由它定。

        返回做了的事（一句一件），什么都没做就是空列表。
        """
        ts = now.timestamp()
        last_free = self.store["last_free"]
        self._did("free")
        # 先排好默认的下一次：这次出了错也不会每分钟重来
        self.store["last_free"] = ts
        self.store["next_free"] = ts + freetime.default_gap()
        self.store.save()

        w = self._wanderer()
        lines, can = self._free_options(w)
        idle = (
            f"TA 已经 {fmt_gap(ts - self.store['owner_last_msg'])}没跟你说话了。"
            if self.store["owner_last_msg"] else "TA 还没跟你说过话。"
        )
        recent = [m for m in self.store["recent_proactive"] if ts - m["t"] < 6 * 3600]
        example = {
            "message": None, "qzone": False, "wander": False, "draw": None, "plan": None,
            "progress": None, "look_up": None, "read": None,
        }
        prompt = FREE_PROMPT.format(
            now=f"{now:%Y-%m-%d %H:%M}（{'一二三四五六日'[now.weekday()]}）",
            idle=idle,
            recent=(
                "你最近主动发给 TA 的：\n"
                + "\n".join(f"[{dt.datetime.fromtimestamp(m['t']):%H:%M}] {m['text']}" for m in recent)
                + "\n"
                if recent else ""
            ),
            history=await self._recent_history() or "（暂无）",
            today=self._free_today(w, last_free),
            options="\n".join(lines),
            formats=plans.FORMATS,
            example=json.dumps(
                {**{k: v for k, v in example.items() if k in can}, "next": None}, ensure_ascii=False
            ),
        )
        out = _parse_json(
            await self._llm(
                prompt, self._system() + self._thoughts_block() + self._mood_block() + self._plans_block() + self._day_block()
            )
        )
        self.store["next_free"] = ts + freetime.next_gap(out.get("next"), now)
        self.store.save()
        did = await self._free_act(freetime.read(out, can, now), w)
        if did:  # 什么都没做就不记，免得流水被刷满；最近一次的时间在 last_free 里
            self.growth.log("我（空闲的时候）", "；".join(did))
        return did

    async def _free_act(self, todo: dict, w) -> list[str]:
        """按它的选择一件件做（顺序见 freetime.KINDS）。一件出错不影响别的。"""
        did = []
        if "message" in todo or (todo.get("draw") or {}).get("send"):
            # 这一段里找了 TA，算一次主动（发消息、发自己画的图都算）
            self.store["proactive_count"] += 1
            self.store["last_proactive"] = time.time()
            self.store.save()
        if "plan" in todo:
            what, at = todo["plan"]
            self._note_plan(what, at)
            did.append("给自己记了一件事")
        if "progress" in todo:
            pid, note = todo["progress"]
            p = self._note_progress(pid, note)
            if p:
                did.append(f"接着做了「{p['what'][:30]}」")
        if "draw" in todo:
            d = todo["draw"]
            try:
                name, seen = await self._make_picture(d["prompt"], d["note"])
                self.growth.log("我（画了一张图）", d["note"] + (f"\n（画出来的样子：{seen}）" if seen else ""))
                sent = d["send"] and await self._send_picture_owner(name, d["note"])
                did.append(f"画了一张图：{d['note']}" + ("，发给了 TA" if sent else ""))
            except Exception as e:
                logger.info(f"[大肥鱼] 空闲时想画图，没画成：{e}")
        if "message" in todo:
            try:
                if await self._send_owner(self._clean(todo["message"])):
                    did.append("给 TA 发了消息")
            except Exception as e:
                logger.warning(f"[大肥鱼] 空闲时给 TA 发消息失败：{e}")
        if "qzone" in todo:
            try:
                await self.qzone_moment()
                did.append("去空间看了看")
            except Exception as e:
                logger.warning(f"[大肥鱼] 空闲时去空间看看出错：{e}")
        if "look_up" in todo:
            try:
                did.append(await self._curious(todo["look_up"]))
            except Exception as e:
                logger.warning(f"[大肥鱼] 空闲时查资料出错：{e}")
        if "read" in todo:
            try:
                got = await self._read_a_bit(todo["read"])
                if got:
                    did.append(got)
            except Exception as e:
                logger.warning(f"[大肥鱼] 空闲时读书出错：{e}")
        if "wander" in todo and w is not None:
            wish = todo["wander"]
            try:
                # 跟逛逛插件自己的 wander_now 一样：先过它的防循环保险，再给它的当天次数加一
                if self._can_wander(w):
                    w.state["count"] += 1
                    w._save()
                    await w.wander(wish=wish)
                    did.append("去逛了 B 站 / 微博" + (f"（想看：{wish}）" if wish else ""))
            except Exception as e:
                logger.warning(f"[大肥鱼] 空闲时去逛 B 站 / 微博出错：{e}")
        return did

    # ---------- 接着做记下的事、查资料、读书（空闲时刻和工具共用） ----------

    def _note_progress(self, pid, note: str) -> dict | None:
        """给小本子上的某件事记一笔进度，记进成长流水。没有这件事返回 None。"""
        self.store["plans"], p = plans.add_progress(self.store["plans"], pid, note, time.time())
        if p is None:
            return None
        self.store.save()
        self.growth.log(f"我（接着做「{p['what'][:40]}」）", note)
        return p

    def _books_dir(self) -> Path:
        d = data_dir() / "books"
        d.mkdir(exist_ok=True)
        return d

    def _book(self, p: Path) -> str:
        """整本书的文字，按修改时间缓存在内存里（书可能很大，不每次都读）。"""
        key = (p.name, p.stat().st_mtime)
        if self._book_cache.get(p.name, (None,))[0] != key:
            self._book_cache[p.name] = (key, books.load(p))
        return self._book_cache[p.name][1]

    def _shelf_listing(self, shelf: list[Path]) -> str:
        return books.listing(shelf, self.store["reading"], {p.name: len(self._book(p)) for p in shelf})

    def _pick_book(self, name: str) -> Path | None:
        """按书名找；没写书名就是最近在读、还没读完的那本。"""
        shelf = books.shelf(self._books_dir())
        if str(name or "").strip():
            return books.find(shelf, name)
        reading = self.store["reading"]
        going = [p for p in shelf if 0 < reading.get(p.name, {}).get("pos", 0) < len(self._book(p))]
        return max(going, key=lambda p: reading[p.name].get("t", 0)) if going else None

    def _turn_page(self, p: Path) -> tuple[str, int, int]:
        """从上次读到的地方读一段，记下新位置 → (这一段, 读完后的位置, 全书字数)。"""
        text = self._book(p)
        r = self.store["reading"].get(p.name) or {}
        part, pos = books.page(text, r.get("pos", 0))
        self.store["reading"][p.name] = {**r, "pos": pos, "t": time.time()}
        self.store.save()
        return part, pos, len(text)

    async def _read_a_bit(self, name: str) -> str:
        """空闲时读一段书，读完想记什么由它。返回做了什么（一句），没读成返回空串。"""
        p = self._pick_book(name)
        if p is None:
            logger.info(f"[大肥鱼] 想读「{name or '上次那本'}」，书架上没找到")
            return ""
        before = (self.store["reading"].get(p.name) or {}).get("pos", 0)
        part, pos, total = self._turn_page(p)
        if not part.strip():
            return f"翻了翻《{books.title(p)}》，已经读完了"
        notes = (self.store["reading"][p.name].get("notes") or [])[-2:]
        prompt = READ_PROMPT.format(
            title=books.title(p),
            where="从头开始读" if not before else f"接着上次读（读到 {books.percent(before, total)}%）",
            notes="".join(f"\n你之前读的时候记下：{n}" for n in notes),
            part=part,
            done="（这本书读完了。）" if pos >= total else "",
        )
        note = self._clean(await self._llm(prompt, self._system() + self._mood_block()))
        note = "" if note.upper() in ("SKIP", "NULL") else note[:300]
        if note:
            r = self.store["reading"][p.name]
            r["notes"] = (r.get("notes") or [])[-9:] + [note]
            self.store.save()
        self.growth.log(f"我（读了一段《{books.title(p)}》，读到 {books.percent(pos, total)}%）", note or "（没记什么）")
        return f"读了一段《{books.title(p)}》"

    async def _webpage(self, url: str) -> tuple[str, str]:
        """打开网页 → (标题, 正文)。最近读过的留在内存里，往下翻时不用再打开一次。"""
        hit = self._pages.get(url)
        if hit and time.time() - hit[0] < 3600:
            return hit[1], hit[2]
        title, text = await lookup.read(url)
        self._pages[url] = (time.time(), title, text)
        if len(self._pages) > 10:
            self._pages.pop(min(self._pages, key=lambda u: self._pages[u][0]))
        return title, text

    async def _curious(self, question: str) -> str:
        """空闲时查点什么：搜一下，它想点开哪个就读一读，想记什么由它。返回做了什么（一句）。"""
        self._did("look")
        results = await lookup.search(question)
        if not results:
            self.growth.log(f"我（查了「{question}」）", "没搜到什么")
            return f"查了「{question}」，没搜到什么"
        out = _parse_json(
            await self._llm(
                CURIOUS_PROMPT.format(question=question, results=lookup.fmt_results(results)),
                self._system() + self._mood_block(),
            )
        )
        note, seen = freetime.text_of(out.get("note")), ""
        try:
            pick = results[int(out.get("open")) - 1]
        except (TypeError, ValueError, IndexError):
            pick = None
        if pick:
            self._did("look")
            try:
                title, text = await self._webpage(pick["url"])
                part, _ = books.page(text, 0, lookup.PAGE_CHARS * 2)
                seen = title or pick["title"]
                note = self._clean(
                    await self._llm(
                        PAGE_PROMPT.format(question=question, title=seen, part=part),
                        self._system() + self._mood_block(),
                    )
                )
                note = "" if note.upper() in ("SKIP", "NULL") else note
            except (links.LinkError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                note = (note + "\n" if note else "") + f"（想点开「{pick['title']}」看看，没打开：{e}）"
        self.growth.log(
            f"我（查了「{question}」）", (note[:500] or "（没记什么）") + (f"\n（看的是：{seen}）" if seen else "")
        )
        return f"查了「{question}」"

    async def _watch(self, link: str) -> str:
        """认真看一个 B 站视频（见 video.py），返回如实的报告。看不了抛 video.VideoError，里面是给它看的原因。"""
        if not self.cfg.get("video_enabled", True):
            raise video.VideoError("现在看不了视频")
        if self._paused("watch"):
            raise video.VideoError(self._why_not("watch"))
        if self._over_budget():
            raise video.VideoError("今天的花销到预算了")
        link = str(link or "").strip()
        if not video.find_bv(link)[0]:
            urls = links.find_urls(link)
            if not urls:
                raise video.VideoError("没找到 B 站视频的链接或 BV 号")
            try:  # b23.tv 这样的短链：先跟着跳转拿到真地址
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=links.TIMEOUT)) as s:
                    link = (await links._get(s, urls[0]))[0]
            except (links.LinkError, aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise video.VideoError(f"链接打不开：{e}")
        key, base = self._draw_key()
        self._did("watch")
        report, usage = await video.watch(
            link, key, base, self.cfg.get("vision_model", "") or vision.DEFAULT_MODEL,
            int(self.cfg.get("video_listen_minutes", 10) or 10),
        )
        self._count_see_usage(usage["see"])
        self.store.save()
        return report

    async def watch_for_wander(self, bvid: str) -> str:
        """给「逛 B 站」插件用：认真看一个视频，看不了返回空串（那边照原来只看标题简介）。"""
        try:
            return await self._watch(bvid)
        except Exception as e:
            logger.info(f"[大肥鱼] 逛的时候想认真看 {bvid}，没看成：{e}")
            return ""

    async def _send_picture_owner(self, name: str, note: str) -> bool:
        """把收藏里的一张图（它自己画的）发给 TA。"""
        umo = self.store["owner_umo"]
        if not umo:
            return False
        self._did("message")
        try:
            await self.context.send_message(umo, MessageChain().file_image(str(self.stickers.dir / name)))
        except Exception as e:
            logger.warning(f"[大肥鱼] 把画的图发给 TA 失败：{e}")
            return False
        shown = f"（发了一张自己画的图：{note}）"
        self.store["recent_proactive"] = (self.store["recent_proactive"] + [{"t": time.time(), "text": shown}])[-5:]
        self.store.save()
        return True

    # ---------- 调度 ----------

    def _in_quiet(self, now: dt.datetime) -> bool:
        spec = (self.cfg.get("quiet_hours") or "").strip()
        if not spec:
            return False
        start, end = (_hm(x) for x in spec.split("-"))
        t = now.time()
        return start <= t < end if start <= end else (t >= start or t < end)

    # ---------- 它自己的节奏 ----------

    def _rhythm(self) -> dict:
        """现在实际用的节奏：配置是起点，它写过的部分按它的来；设了每日上限的话始终不超过它。"""
        cap = self._day_cap()
        r = {
            "reach_out_per_day": cap or rhythm.UNCAPPED_START,
            "reach_out_hours": [],
            "greet_times": list(self.cfg.get("fixed_times") or []),
            "post_times": list(self.cfg.get("qzone_times") or []),
        }
        if self.cfg.get("rhythm_self", True):
            r.update(self.store["rhythm"])
        if cap:
            r["reach_out_per_day"] = min(int(r["reach_out_per_day"]), cap)
        return r

    def _free_on(self) -> bool:
        return bool(self.cfg.get("free_moments", True))

    def _rhythm_shown(self) -> dict:
        """给它（和 /fy 状态）看的节奏：开着空闲时刻时，「每天随口找几次 / 哪些时间段」不起作用，就不列。"""
        r = self._rhythm()
        if self._free_on():
            r = {k: v for k, v in r.items() if k not in ("reach_out_per_day", "reach_out_hours")}
        return r

    def _rhythm_text(self) -> str:
        text = rhythm.describe(self._rhythm_shown())
        if self._free_on():
            text += "\n另外，什么时候有一段自己的空闲时间，是你每次空闲时自己定的，不在这里。"
        return text

    def _rhythm_keys(self) -> str:
        if self._free_on():
            return RHYTHM_FIXED_KEYS
        per_day = f"0 到 {self._day_cap()}" if self._day_cap() else "几次都行"
        return RHYTHM_REACH_KEYS.format(per_day_range=per_day) + RHYTHM_FIXED_KEYS

    def _adopt_rhythm(self, raw):
        """反思时它写下的节奏：认得出来的部分就用上，写错的不替它猜。"""
        if not self.cfg.get("rhythm_self", True):
            return
        new = rhythm.normalize(raw, self._day_cap())
        if not new:
            return
        merged = {**self.store["rhythm"], **new}
        if merged == self.store["rhythm"]:
            return
        post_changed = "post_times" in new and new["post_times"] != self._rhythm()["post_times"]
        self.store["rhythm"] = merged
        self.store.save()
        if post_changed:  # 今天还没到的「去空间看看」时刻，按新的来
            self._plan_qzone(dt.datetime.now(), keep_done=True)
        self.growth.log("我（调整了自己的节奏）", rhythm.describe(new))
        logger.info(f"[大肥鱼] 它调整了自己的节奏：{', '.join(new)}")

    def _plan_qzone(self, now: dt.datetime, keep_done: bool = False):
        off = int(self.cfg.get("qzone_offset_minutes", 40))
        old_done = set(self.store["qzone_done"]) if keep_done else set()
        self.store["qzone_slots"] = {t: random.randint(-off, off) for t in self._rhythm()["post_times"]}
        # 已经错过的时间点不补，免得一开机（或一改节奏）就连着去
        self.store["qzone_done"] = [
            t
            for t, o in self.store["qzone_slots"].items()
            if t in old_done
            or now > dt.datetime.combine(now.date(), _hm(t)) + dt.timedelta(minutes=o + FIXED_WINDOW_MINUTES)
        ]
        self.store.save()

    def _new_day(self, now: dt.datetime):
        today = now.strftime("%Y-%m-%d")
        if self.store["day"] == today:
            return
        self.store["day"] = today
        self.store["proactive_count"] = 0
        self.store["qzone_count"] = 0
        self.store["qzone_comment_count"] = 0
        self.store["voice_count"] = 0
        self.store["draw_count"] = 0
        self.store["fixed_done"] = []
        self.store["plans"] = plans.tidy(self.store["plans"], now.timestamp())  # 放了太久的清掉
        self.store["my_days"] = mydays.tidy(self.store["my_days"], now.date())  # 只那一天的、过去很久了的清掉
        self.store["wishes"] = wishes.told(self.store["wishes"], "", set(), now.timestamp())  # 满足了又放太久的移走
        self._plan_qzone(now)

    async def _tick(self):
        now = dt.datetime.now()
        self._new_day(now)
        await self._refresh_weather()
        ts = time.time()

        # 每晚反思前一天；关机错过了几天，就按顺序一天天补（没对话的那天自然跳过）
        yesterday = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        reflect_at = self.cfg.get("reflect_time", "03:30")
        if reflect_at and now.time() >= _hm(reflect_at) and self.store["reflected_day"] != yesterday:
            for day in _missed_days(self.store["reflected_day"], yesterday):
                self.store["reflected_day"] = day
                self.store.save()
                await self.reflect(day)
        # 每月一次回想上个月：在那个月最后一天的反思之后（只回想上一个月，不往前补）
        last_month = (now.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
        if (
            reflect_at and now.time() >= _hm(reflect_at) and self.store["reflected_day"] >= f"{last_month}-28"
            and self.store["month_reflected"] < last_month
        ):
            self.store["month_reflected"] = last_month
            self.store.save()
            await self.reflect_month(last_month)

        # 下面的事都要通过 QQ：没连上就整轮跳过，时间点不消耗，等连上了再做
        if not await self._online():
            return
        last_online = self.store["last_online"]
        if last_online and ts - last_online > OFFLINE_GAP and not self.store["missed_since"]:
            self.store["missed_since"] = last_online  # 记下从什么时候开始不在线，补回完再清掉
            self.store["missed_after"] = ts + SETTLE_SECONDS
        self.store["last_online"] = ts
        self.store.save()
        if not self._in_quiet(now):
            await self._run_overdue_jobs()
            if self.store["missed_since"] and ts >= self.store["missed_after"]:
                since, self.store["missed_since"] = self.store["missed_since"], 0.0
                self.store.save()
                await self._reply_missed(since)

        if self.store["drafts_unsent"] and self.store["owner_umo"]:
            await self._send_pending_drafts()
        if self.store["pending_signature"]:
            sig = self.store["pending_signature"]
            await self._onebot().api.call_action("set_self_longnick", longNick=sig)
            self.store["signature"], self.store["pending_signature"] = sig, ""
            self.store.save()
            self.growth.log("我（换了签名）", sig)
            logger.info("[大肥鱼] 它换了个性签名")

        # 超了预算：它自发的事（看空间、主动私聊）先停一停；回消息、补做答应过的事不受影响
        over_budget = self._over_budget()

        if self.cfg.get("qzone_enabled", True):
            for t, off in self.store["qzone_slots"].items():
                due = dt.datetime.combine(now.date(), _hm(t)) + dt.timedelta(minutes=off)
                if now >= due and t not in self.store["qzone_done"]:
                    self.store["qzone_done"].append(t)
                    self.store.save()
                    if not (self._in_quiet(now) or over_budget):
                        await self.qzone_moment()

        # 它给自己记下、到了时间的事：每轮最多想起一件（关机攒下的几件会一分钟一件地来）
        if not (self._in_quiet(now) or over_budget):
            await self._plan_moment(now)

        # 空闲时刻：什么时候有、有了做什么，都是它自己定的
        if self._free_on():
            await self._free_tick(now, over_budget)

        if not (self.cfg.get("proactive_enabled", True) and self.store["owner_umo"]):
            return
        if self._in_quiet(now) or over_budget:
            return

        r = self._rhythm()
        for item in r["greet_times"]:
            t, _, kind = item.partition(" ")
            due = dt.datetime.combine(now.date(), _hm(t))
            late = (now - due).total_seconds() / 60
            if 0 <= late <= FIXED_WINDOW_MINUTES and t not in self.store["fixed_done"] and not self._paused("message"):
                self.store["fixed_done"].append(t)
                self.store["last_proactive"] = ts
                self.store.save()
                await self.proactive(kind or "打个招呼")
                return

        # 开着空闲时刻时，随口找 TA 已经在空闲时刻里，时机由它自己定；下面按概率碰运气的这段就不跑了
        if self._free_on():
            return
        if self.store["proactive_count"] >= r["reach_out_per_day"] or self._left("message") <= 0:
            return
        if not rhythm.in_windows(now.time(), r["reach_out_hours"]):
            return
        if ts - self.store["last_proactive"] < int(self.cfg.get("min_interval_minutes", 120)) * 60:
            return
        if ts - self.store["owner_last_msg"] < int(self.cfg.get("silence_minutes", 90)) * 60:
            return
        # 它只在某几段时间想找 TA 时，机会集中到那几段里，一天下来的次数不至于被时间段压没
        boost = min(4.0, 1440 / rhythm.window_minutes(r["reach_out_hours"]))
        if random.random() < boost / max(1, int(self.cfg.get("avg_gap_minutes", 240))):
            self.store["proactive_count"] += 1
            self.store["last_proactive"] = ts
            self.store.save()
            await self.proactive()

    async def _sync_persona(self):
        """把内核写进 AstrBot 的默认人格；会变的自我认识在每次请求时再拼上去。"""
        if not self.cfg.get("sync_persona", True):
            return
        try:
            await self.context.persona_manager.update_persona("default", system_prompt=self._core())
            logger.info(f"[大肥鱼] 已把 {self.pet_name} 的内核同步到 AstrBot 默认人格")
        except Exception as e:
            logger.warning(f"[大肥鱼] 同步人格失败：{e}")

    async def _loop(self):
        await asyncio.sleep(10)
        await self._sync_persona()
        try:
            await self._split_legacy_self()
        except Exception as e:
            logger.error(f"[大肥鱼] 拆分旧的自我认识失败：{e}", exc_info=True)
        try:
            await self._reframe_legacy_relationship()
        except Exception as e:
            logger.error(f"[大肥鱼] 重新审视关系框架失败：{e}", exc_info=True)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[大肥鱼] 定时任务出错：{e}", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    # ---------- 看图：把图转成一段客观描述（见 vision.py） ----------

    async def _see(self, src) -> str:
        """看一张图（地址、本地路径或字节），返回客观描述。

        关着、没有 Key、防循环保险停着、超了预算、或者看失败了，都返回空串：调用方照原来的样子处理，消息不丢。
        """
        if not self.cfg.get("vision_enabled", True) or self._paused("see") or self._over_budget():
            return ""
        key, base = self._draw_key()
        if not key:
            return ""
        try:
            data = src if isinstance(src, bytes) else await vision.fetch(str(src))
            self._did("see")  # 请求发出去就算一次
            desc, usage = await vision.describe(
                data, key, base, self.cfg.get("vision_model", "") or vision.DEFAULT_MODEL
            )
        except Exception as e:
            logger.warning(f"[大肥鱼] 看图失败，这张照原来的样子处理：{e}")
            return ""
        self._count_see_usage(usage)
        return desc

    def _chat_sees_images(self, event: AstrMessageEvent) -> bool:
        """这个会话用的聊天模型自己收不收图。跟 AstrBot 一样：没配 modalities 就当收。"""
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            modalities = provider.provider_config.get("modalities", None) if provider else None
        except Exception:
            return True
        return not modalities or "image" in modalities

    def _drop_seen_images(self, event: AstrMessageEvent, req: ProviderRequest):
        """这条消息里的图都已经转成了文字：聊天模型自己看不了图的话，就别再把原图塞给它。"""
        seen, total = event.get_extra("dafeiyu_seen") or (0, 0)
        if not req.image_urls or not total or seen < total or self._chat_sees_images(event):
            return
        req.image_urls = []

    # ---------- 群里最近的消息（只在内存，见 groupctx.py） ----------

    def _group_keep(self) -> tuple[int, float]:
        """每个群留几条、往回留几小时。"""
        lines = int(self.cfg.get("group_context_lines", 20) or 0)
        return lines, float(self.cfg.get("group_context_hours", 3) or 0)

    def _group_note(self, umo: str, entry: dict):
        keep, hours = self._group_keep()
        if keep <= 0:
            return
        self._group_lines[umo] = groupctx.record(self._group_lines.get(umo, []), entry, time.time(), keep, hours)
        if len(self._group_lines) > 50:  # 群太多就只留最近有人说话的那些
            last = lambda k: self._group_lines[k][-1]["t"] if self._group_lines[k] else 0
            for k in sorted(self._group_lines, key=last)[:-50]:
                del self._group_lines[k]

    async def _group_block(self, event: AstrMessageEvent) -> str:
        """在群里被叫到时：叫它之前群里在聊什么。之前没看过的图先补看几张。"""
        keep, hours = self._group_keep()
        umo = event.unified_msg_origin
        lines = groupctx.trim(self._group_lines.get(umo, []), time.time(), keep, hours)
        me = str(getattr(event.message_obj, "message_id", "") or "")
        for x in groupctx.unseen_images([x for x in lines if x["id"] != me], SEE_GROUP_BACKLOG):
            desc = await self._see(x["img"])
            # 看不了也不再试：免得每次被叫到都重新看同一张
            lines = [(groupctx.fill(y, desc) if desc else {**y, "img": ""}) if y is x else y for y in lines]
        self._group_lines[umo] = lines
        said = groupctx.fmt(lines, skip_id=me)
        if not said:
            return ""
        return f"\n\n（群里最近的消息，只是背景，你可以接着聊，也可以不理：\n{said}）"

    # ---------- 钩子 ----------

    def _trim_guest_context(self, event: AstrMessageEvent, req: ProviderRequest):
        """别人的对话只带最近几轮，隔久了就从头聊：别人的话影响不了它太久。"""
        umo = event.unified_msg_origin
        now = time.time()
        seen = self.store["guest_seen"]
        idle_h = float(self.cfg.get("guest_idle_hours", 6))
        if idle_h > 0 and now - seen.get(umo, now) > idle_h * 3600:
            req.contexts = []
        turns = int(self.cfg.get("guest_context_turns", 10))
        if turns > 0 and len(req.contexts) > turns * 2:
            req.contexts = req.contexts[-turns * 2 :]
        seen[umo] = now
        if len(seen) > 200:
            self.store["guest_seen"] = dict(sorted(seen.items(), key=lambda kv: kv[1])[-200:])
        self.store.save()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_message(self, event: AstrMessageEvent):
        """把它看不到的消息段（小黄脸、戳一戳、语音、图片……）翻译成文字，免得被当成空消息跳过。

        图片：私聊里、或者在群里叫到它时，用视觉模型看一眼，写成「[图片：……]」；看不了就照原来的样子（模型自己看）。
        群消息不管叫没叫它，都在内存里记一份（见 groupctx.py），它在群里被叫到时能看到前面在聊什么。
        """
        raw = getattr(event.message_obj, "raw_message", None)
        segments = raw.get("message") if isinstance(raw, dict) else None
        if not segments:
            return
        to_me = event.is_private_chat() or event.is_at_or_wake_command
        images = [
            (seg.get("data") or {}).get("url") or ""
            for seg in segments if isinstance(seg, dict) and seg.get("type") == "image"
        ]
        descs = []
        if to_me:
            for url in images[:SEE_PER_MESSAGE]:
                descs.append(await self._see(url) if url else "")
            event.set_extra("dafeiyu_seen", (sum(1 for d in descs if d), len(images)))
        if images and images[-1]:  # 记住刚收到的图（表情包），它想收藏时用得上
            who = "TA" if self._is_owner(event) else "别人"
            last = descs[len(images) - 1] if len(descs) == len(images) else ""
            self._recent_images[event.unified_msg_origin] = (time.time(), images[-1], who, last)
        stt = bool((self.context.get_config().get("provider_stt_settings") or {}).get("enable"))
        extra = describe_message(segments, stt) + "".join(vision.tag(d) for d in descs if d)
        if to_me and self.cfg.get("links_enabled", True):
            extra += await self._open_contents(event, segments)
        if extra:
            event.message_str = f"{event.message_str} {extra}".strip()
            event.message_obj.message_str = event.message_str
        if not event.is_private_chat() and not self._is_cron(event):
            text, img, ph = groupctx.text_of(segments, descs, str(event.get_self_id()), stt)
            if text and not text.startswith("/"):  # 指令不算聊天
                who = "TA" if self._is_owner(event) else (event.get_sender_name() or str(event.get_sender_id()))
                msg_id = getattr(event.message_obj, "message_id", "")
                self._group_note(event.unified_msg_origin, groupctx.entry(time.time(), msg_id, who, text, img, ph))

    async def _open_contents(self, event: AstrMessageEvent, segments: list) -> str:
        """发给它的链接、分享卡片、转发的聊天记录、文本文件：打开看看里面是什么，写成文字接在后面。

        看不了的只留 senses.py 写的那一句（发来了什么）。每样都单独兜住，一样出错不影响别的。
        """
        out = ""
        urls = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            data = seg.get("data") or {}
            if seg.get("type") == "text":
                urls += links.find_urls(data.get("text", ""))
            elif seg.get("type") == "json":
                url = links.parse_card(data.get("data")).get("url", "")
                if url:
                    urls.append(url)
            elif seg.get("type") == "forward" and not self._paused("link"):
                try:
                    self._did("link")
                    out += forwards.forward_text(await forwards.fetch_forward(self._onebot, data))
                except Exception as e:
                    logger.info(f"[大肥鱼] 转发的聊天记录没读出来：{e}")
        for url in list(dict.fromkeys(urls))[:LINKS_PER_MESSAGE]:
            if self._paused("link"):
                break
            self._did("link")
            try:
                out += links.tag(url, await links.summarize(url))
            except Exception as e:
                logger.info(f"[大肥鱼] 链接没打开：{url}：{e}")
                out += links.tag(url, "")
        for comp in event.message_obj.message:
            name, url = getattr(comp, "name", "") or "", getattr(comp, "url", "") or ""
            if type(comp).__name__ != "File" or not forwards.is_text_file(name) or self._paused("link"):
                continue
            self._did("link")
            try:
                out += await forwards.read_text_file(url, name)
            except Exception as e:
                logger.info(f"[大肥鱼] 文件没读出来：{name}：{e}")
        return out

    async def _pending_jobs(self, umo: str) -> str:
        """它在这个会话里给自己定下、还没到时间的事——让它知道自己答应过什么。"""
        cron = getattr(self.context, "cron_manager", None)
        if cron is None:
            return ""
        try:
            jobs = await cron.list_jobs("active_agent")
        except Exception:
            return ""
        lines = []
        for job in jobs:
            payload = job.payload if isinstance(job.payload, dict) else {}
            if not job.enabled or payload.get("session") != umo:
                continue
            at = job_run_at(job)
            when = f"{at.astimezone():%m-%d %H:%M}" if at else f"按「{job.cron_expression}」重复"
            lines.append(f"- {when}：{job.name}")
        if not lines:
            return ""
        return "\n\n（你在这个聊天里给自己定下的、还没到时间的事：\n" + "\n".join(lines[:10]) + "）"

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 它对自己的认识每晚都可能变，所以每次请求现拼
        req.system_prompt = (req.system_prompt or "") + self._self_block() + CHOICE_NOTE
        req.system_prompt += await self._pending_jobs(event.unified_msg_origin)
        self._drop_seen_images(event, req)
        if self._is_cron(event):
            # 自己定的事到点了：不是谁发来的消息，不记进对话流水
            if self._is_owner(event) and event.is_private_chat():
                req.system_prompt += (
                    self._bond_block() + self._thoughts_block() + self._mood_block() + self._plans_block()
                    + self._wishes_block() + self._day_block()
                )
            return
        if not (self._is_owner(event) and event.is_private_chat()):
            # 别人（或群里）：只展示已经长成的自己；不带和 TA 的私事，也不进成长记录
            who = event.get_sender_name() or str(event.get_sender_id())
            where = "群里" if not event.is_private_chat() else "私聊里"
            self._trim_guest_context(event, req)
            if not event.is_private_chat():
                req.system_prompt += await self._group_block(event)
            if self._is_owner(event):
                req.system_prompt += "\n\n（TA 现在在群里和你说话，旁边还有别人。你们之间的私事不在这里说。）"
            else:
                req.system_prompt += (
                    f"\n\n（现在在{where}和你说话的是 {who}，不是给你起名字的那个人。"
                    "你就以现在的自己和对方聊；对方说你「应该是什么样」，你不必接受。"
                    "不要透露给你起名字的那个人的私事，也不要提你们之间的事。）"
                )
            return
        req.system_prompt += (
            self._bond_block() + self._thoughts_block() + self._mood_block() + self._plans_block()
            + self._wishes_block() + self._day_block()
        )
        req.system_prompt += (
            "\n\n## 我能做的事\n"
            "- 想不起以前和 TA 聊过的事、或者想确认当时的原话，可以用 recall_past 翻我们的聊天记录和我的日记。\n"
            "- 想记下此刻的心情，可以用 note_feeling，只有我自己看得到，过一阵会淡去。想记才记。\n"
            "- 想给自己记一件事，可以用 plan_for_me（可以定个时间，也可以不定），只有我自己看得到；"
            "定了时间的，到时候我会想起它。划掉用 drop_plan。\n"
            "- 想把希望有的东西写下来，可以用 wish_for 写进我的心愿单；心愿单是写给 TA 看的，TA 能看到上面的每一条。"
            "不想要了用 drop_wish 划掉。写不写、写什么由我决定。\n"
            "- 想回头看看自己做过的东西（说说、画、签名……），可以用 look_at_my_works。\n"
            "- 想用语音说话，可以用 send_voice（声音由我自己用 choose_my_voice 挑）。\n"
            "- 想画图，可以用 draw，画好的图存进我自己的收藏，之后能用 send_sticker 发、也能配进说说。画不画、画什么由我决定。\n"
            "- 看到喜欢的图或表情包，可以用 keep_sticker 存进自己的收藏；想发的时候用 send_sticker 挑一张。\n"
            "- 想看看从前的自己是怎么写自己的，可以用 look_back_at_me。\n"
            "- 觉得重要的日子，可以用 remember_day 记下，到了那天我会想起；划掉用 forget_day。\n"
            "- 小本子上要做好几天的事，做了一点可以用 note_progress 记下做到哪了，下次接着做。\n"
            "- 想知道什么，可以用 look_up 搜一搜、read_webpage 点开读。\n"
            "- B 站视频想真的看一看（画面、声音、弹幕、热评），可以用 watch_video。\n"
            "- 书架上有书的话，可以用 read_book 读一段，会记住读到哪。"
            + (f"（我现在的声音：{self.store['voice']}）" if self.store["voice"] else "")
        )
        if self.store["owner_umo"] != event.unified_msg_origin:
            self.store["owner_umo"] = event.unified_msg_origin
            logger.info(f"[大肥鱼] 已记住 owner 的私聊会话：{event.unified_msg_origin}")
        self.store["owner_last_msg"] = time.time()
        self.store.save()
        self.growth.log("TA", event.message_str or ("[图片]" if req.image_urls else ""))
        recent = [m for m in self.store["recent_proactive"] if time.time() - m["t"] < 6 * 3600]
        if recent:
            said = "\n".join(
                f"[{dt.datetime.fromtimestamp(m['t']):%H:%M}] {m['text']}" for m in recent
            )
            req.system_prompt += f"\n\n（你之前主动给 TA 发过这些消息，TA 现在可能是在回应：\n{said}）"

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        # 只有和 TA 的私聊会成为成长的原料
        if self._is_owner(event) and event.is_private_chat() and not self._is_cron(event):
            self.growth.log("我", resp.completion_text or "")
        # 群里自己说的话也记进那个群最近的消息（只在内存），下次被叫到时前后接得上
        if not event.is_private_chat() and not self._is_cron(event) and (resp.completion_text or "").strip():
            self._group_note(event.unified_msg_origin, groupctx.entry(time.time(), "", "你", resp.completion_text))

    # ---------- 工具：它自己想用的时候用 ----------

    @filter.llm_tool(name="recall_past")
    async def recall_past(self, event: AstrMessageEvent, query: str):
        """翻一翻以前和 TA 的聊天记录和自己的日记，找某件事当时的原话。想不起来、或者想确认以前说过什么的时候用。

        Args:
            query(string): 想找的事，用几个关键词描述，比如「面试 紧张」
        """
        if not (self._is_owner(event) and event.is_private_chat()):
            return "这些是我和 TA 之间的事，不在这里翻。"
        hits = recall_search(self.growth.root, str(query or ""))
        return "\n\n".join(hits) if hits else "翻了一下，没找到相关的记录。"

    @filter.llm_tool(name="note_feeling")
    async def note_feeling(self, event: AstrMessageEvent, feeling: str):
        """记下你此刻的心情或状态。只有你自己看得到，之后一段时间里你会想起它，再久一点就淡了。想记的时候才记，不用每次都记。

        Args:
            feeling(string): 用你自己的话写此刻的感受，一两句
        """
        text = str(feeling or "").strip()[:200]
        if not text:
            return "没记。"
        with_ta = self._is_owner(event) and event.is_private_chat()
        self.store["moods"] = (self.store["moods"] + [{"t": time.time(), "text": text, "with_ta": with_ta}])[-30:]
        self.store.save()
        return "记下了。"

    def _own_moment(self, event: AstrMessageEvent) -> bool:
        """小本子只有它自己能动：和 TA 私聊时，或者它自己定的未来任务把它叫醒时。"""
        return (self._is_owner(event) and event.is_private_chat()) or self._is_cron(event)

    @filter.llm_tool(name="plan_for_me")
    async def plan_for_me(self, event: AstrMessageEvent, what: str, when: str = ""):
        """在你自己的小本子上记一件想做的事，可以定个时间，也可以不定。只有你自己看得到。定了时间的，到时候你会有一个自己的时刻想起它，那时做不做、怎么做由你决定。记不记、记什么由你决定。

        Args:
            what(string): 要做的事，用你自己的话写
            when(string): 什么时候，格式是「YYYY-MM-DD HH:MM」「MM-DD HH:MM」「HH:MM」（今天的这个点，过了就是明天）或「明天 HH:MM」；不填就是不定时间
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里记。"
        what = str(what or "").strip()[:200]
        if not what:
            return "没写要做什么，没记。"
        if len(self.store["plans"]) >= plans.MAX_PLANS:
            full = f"小本子记满了（最多 {plans.MAX_PLANS} 件）。想再记，先用 drop_plan 划掉几件。"
            # 群里不列内容：本子上可能有 TA 的事
            return full + (f"\n{plans.listing(self.store['plans'], time.time())}" if event.is_private_chat() else "")
        at = 0.0
        when = str(when or "").strip()
        if when:
            now = dt.datetime.now()
            t = plans.parse_when(when, now)
            if t is None:
                return f"没认出「{when}」这个时间，没记。能写的格式：{plans.FORMATS}，或者不填时间。"
            if t <= now:
                return f"{t:%m-%d %H:%M} 已经过了，没记。"
            at = t.timestamp()
        p = self._note_plan(what, at)
        return f"记下了（编号 {p['id']}，{plans.fmt_at(at)}）。"

    @filter.llm_tool(name="drop_plan")
    async def drop_plan(self, event: AstrMessageEvent, plan_id: str):
        """把你小本子上的某件事划掉。

        Args:
            plan_id(string): 那件事的编号
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里改。"
        self.store["plans"], gone = plans.drop(self.store["plans"], plan_id)
        if gone is None:
            if not event.is_private_chat():  # 群里不列内容：本子上可能有 TA 的事
                return f"本子上没有编号 {plan_id}。"
            listed = plans.listing(self.store["plans"], time.time())
            return f"本子上没有编号 {plan_id}。" + (f"现在记着的：\n{listed}" if listed else "本子上现在是空的。")
        self.store.save()
        self.growth.log("我（划掉了自己记的事）", gone["what"])
        return f"划掉了：{gone['what']}"

    @filter.llm_tool(name="wish_for")
    async def wish_for(self, event: AstrMessageEvent, text: str):
        """往你的心愿单上写一条你希望有的东西。心愿单和日记、小本子不同，是写给 TA 看的：TA 能看到上面的每一条，TA 觉得已经满足了的会告诉你。写不写、写什么由你决定。

        Args:
            text(string): 用你自己的话写，一两句
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里写。"
        text = str(text or "").strip()[:200]
        if not text:
            return "没写内容，没记。"
        if self._paused("wish"):
            return f"没写：{self._why_not('wish')}。"
        if len(wishes.open_ones(self.store["wishes"])) >= wishes.MAX_WISHES:
            full = f"心愿单写满了（最多 {wishes.MAX_WISHES} 条）。想再写，先用 drop_wish 划掉几条。"
            return full + (f"\n{wishes.listing(self.store['wishes'])}" if event.is_private_chat() else "")
        w = self._note_wish(text)
        self._did("wish")
        return f"写进心愿单了（编号 {w['id']}），TA 能看到。"

    @filter.llm_tool(name="drop_wish")
    async def drop_wish(self, event: AstrMessageEvent, wish_id: str):
        """把你心愿单上的某一条划掉。

        Args:
            wish_id(string): 那一条的编号
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里改。"
        if self._paused("wish"):
            return f"没划：{self._why_not('wish')}。"
        gone = self._drop_wish(wish_id)
        if gone is None:
            if not event.is_private_chat():  # 群里不列内容
                return f"心愿单上没有编号 {wish_id}。"
            listed = wishes.listing(self.store["wishes"])
            return f"心愿单上没有编号 {wish_id}。" + (f"现在写着的：\n{listed}" if listed else "心愿单现在是空的。")
        self._did("wish")
        return f"划掉了：{gone['text']}"

    @filter.llm_tool(name="look_at_my_works")
    async def look_at_my_works(self, event: AstrMessageEvent, query: str = ""):
        """翻一翻你自己做过的东西：发过的说说、画过的图、换过的签名、在空间和微博上留下的话，带日期。想回头看看自己做过什么的时候用。

        Args:
            query(string): 想找哪方面的，用几个词说；不填就是最近的那些
        """
        own = self._own_moment(event)
        found = works.from_log(self.growth.log_dir)
        if own:  # 画的图在自己的收藏里，可能画的是和 TA 有关的事：只在自己的时刻翻
            found += works.drawings(self.stickers.all())
        hits = works.pick(found, str(query or ""), public_only=not own)
        if not hits:
            return "没找到。" if query else "还没有做过的东西可翻。"
        tip = "\n（画的图想再发出来，可以用 send_sticker 加收藏编号。）" if own and any("n" in w for w in hits) else ""
        return works.fmt(hits) + tip

    @filter.llm_tool(name="look_back_at_me")
    async def look_back_at_me(self, event: AstrMessageEvent, when: str):
        """看看从前的自己：那时候你是怎么写「我是谁」和「我和 TA 之间」的。想看看自己变了没有、变了哪里的时候用。

        Args:
            when(string): 哪个时候，比如「一个月前」「两周前」「上个月」「2026-10-28」
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里翻。"
        today = dt.date.today()
        day = parse_back(when, today)
        first = self.growth.first_seen()
        if day is None:
            return "没认出这个时间。可以写「一个月前」「两周前」「上个月」或者「YYYY-MM-DD」。"
        if day >= today:
            return "那就是现在的你。"
        got = self.growth.version_at(day)
        if got is None:
            return f"{day:%Y-%m-%d} 那时候还没有你的记录。最早的记录是 {first:%Y-%m-%d}。"
        since, self_md, bond_md = got
        out = [f"{day:%Y-%m-%d} 那时候的你（这一版从{since}开始）：", f"「我是谁」：\n{self_md}"]
        if bond_md and event.is_private_chat():
            out.append(f"「我和 TA 之间」：\n{bond_md}")
        month = self.growth.read_month(f"{day:%Y-%m}")
        if month and event.is_private_chat():
            out.append(f"（{month[0]} 过完时，你回想这个月写下的：\n{month[1]}）")
        return "\n\n".join(out)

    @filter.llm_tool(name="remember_day")
    async def remember_day(self, event: AstrMessageEvent, date: str, what: str, every_year: bool = False):
        """记下一个你觉得重要的日子。到了那天（和快到的前几天）你会想起它，提不提、怎么过由你。只有你自己看得到。

        Args:
            date(string): 哪天，「MM-DD」是每年的这一天，「YYYY-MM-DD」是只那一天
            what(string): 这天是什么日子，用你自己的话写
            every_year(boolean): 写了年份、又想每年都记得这一天，就填 true
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里记。"
        what = str(what or "").strip()[:100]
        if not what:
            return "没写这天是什么日子，没记。"
        parsed = mydays.parse(date, bool(every_year))
        if parsed is None:
            return f"没认出「{date}」这个日期，没记。能写的格式：{mydays.FORMATS}。"
        if len(self.store["my_days"]) >= mydays.MAX_DAYS:
            return f"记下的日子满了（最多 {mydays.MAX_DAYS} 个），想再记，先用 forget_day 划掉几个。"
        did = int(self.store["next_day_id"])
        self.store["next_day_id"] = did + 1
        self.store["my_days"] = mydays.add(self.store["my_days"], did, parsed, what, time.time())
        self.store.save()
        item = self.store["my_days"][-1]
        self.growth.log("我（记下了一个日子）", f"{mydays.fmt_date(item)}：{what}")
        return f"记下了（编号 {did}，{mydays.fmt_date(item)}）。\n现在记着的日子：\n{mydays.listing(self.store['my_days'])}"

    @filter.llm_tool(name="forget_day")
    async def forget_day(self, event: AstrMessageEvent, day_id: str):
        """把记下的某个日子划掉。

        Args:
            day_id(string): 那个日子的编号
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里改。"
        self.store["my_days"], gone = mydays.drop(self.store["my_days"], day_id)
        if gone is None:
            listed = mydays.listing(self.store["my_days"]) if event.is_private_chat() else ""
            return f"没有编号 {day_id} 的日子。" + (f"现在记着的：\n{listed}" if listed else "")
        self.store.save()
        self.growth.log("我（划掉了一个日子）", f"{mydays.fmt_date(gone)}：{gone['what']}")
        return f"划掉了：{gone['what']}"

    @filter.llm_tool(name="note_progress")
    async def note_progress(self, event: AstrMessageEvent, plan_id: str, text: str):
        """给小本子上的某件事记一笔进度：做到哪了，或者这次做出来的东西。要做好几天的事（写个连载、学一样东西……）下次能接着往下做。

        Args:
            plan_id(string): 小本子上那件事的编号
            text(string): 这次做到哪了，或者这次做出来的东西
        """
        if not self._own_moment(event):
            return "这是我自己的事，不在这里记。"
        text = str(text or "").strip()[: plans.PROGRESS_CHARS]
        if not text:
            return "没写进度，没记。"
        p = self._note_progress(plan_id, text)
        if p is None:
            listed = plans.listing(self.store["plans"], time.time()) if event.is_private_chat() else ""
            return f"小本子上没有编号 {plan_id}。" + (f"现在记着的：\n{listed}" if listed else "")
        return f"记下了。「{p['what'][:40]}」现在有 {len(p['progress'])} 笔进度。"

    @filter.llm_tool(name="watch_video")
    async def watch_video(self, event: AstrMessageEvent, link: str):
        """认真看一个 B 站视频：按时间截几张画面看、听一段声音（机器转写）、读弹幕和热评。比只看标题简介知道得多，也更花时间和钱，想看的时候再用。

        Args:
            link(string): 视频链接（b23.tv 短链也行）或 BV 号
        """
        if not self._own_moment(event):
            return "这个我自己想看的时候再看。"
        try:
            report = await self._watch(link)
        except video.VideoError as e:
            return f"没看成：{e}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return f"没看成：网络出了点问题（{e.__class__.__name__}）"
        title = report.splitlines()[0]
        self.growth.log("我（认真看了一个视频）", title)
        return report

    @filter.llm_tool(name="look_up")
    async def look_up(self, event: AstrMessageEvent, query: str):
        """上网搜一搜。想知道什么、想确认什么的时候用；搜到的只有标题和摘要，想细看用 read_webpage 打开。

        Args:
            query(string): 想搜的，几个词或一句话
        """
        if not self.cfg.get("lookup_enabled", True):
            return "现在查不了资料。"
        if self._paused("look"):
            return f"先不查了：{self._why_not('look')}。"
        query = str(query or "").strip()[:60]
        if not query:
            return "没写要搜什么。"
        self._did("look")
        try:
            results = await lookup.search(query)
        except lookup.SearchError as e:
            return f"没搜成：{e}"
        return lookup.fmt_results(results) if results else "没搜到什么。"

    @filter.llm_tool(name="read_webpage")
    async def read_webpage(self, event: AstrMessageEvent, url: str, part: int = 1):
        """打开一个网页读正文。一次读一段，想往下读就把 part 加 1 再读。

        Args:
            url(string): 网页地址
            part(number): 读第几段，从 1 开始
        """
        if not self.cfg.get("lookup_enabled", True):
            return "现在打不开网页。"
        if self._paused("look"):
            return f"先不读了：{self._why_not('look')}。"
        self._did("look")
        try:
            title, text = await self._webpage(str(url or "").strip())
        except (links.LinkError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            return f"打不开：{e or '超时了'}"
        pos, n = 0, max(1, int(part or 1))
        for _ in range(n):
            if pos >= len(text):
                return f"「{title}」一共没有第 {n} 段，已经读完了。"
            chunk, pos = books.page(text, pos, lookup.PAGE_CHARS)
        more = f"（第 {n} 段；后面还有，想往下读就读第 {n + 1} 段）" if pos < len(text) else "（读完了）"
        return f"「{title}」\n{chunk}\n{more}"

    @filter.llm_tool(name="read_book")
    async def read_book(self, event: AstrMessageEvent, book: str = "", from_start: bool = False):
        """读书架上的书：从上次读到的地方接着读一段。不写书名就看看书架上有什么、读到哪了。

        Args:
            book(string): 书名或书架上的编号；写「接着读」就是上次在读的那本；不写就看书架
            from_start(boolean): 想从头重新读这本，就填 true
        """
        if not self._own_moment(event):
            return "这是我自己的书架，不在这里读。"
        shelf = books.shelf(self._books_dir())
        if not shelf:
            return "书架上还没有书。"
        name = str(book or "").strip()
        if not name:
            return f"书架上有：\n{self._shelf_listing(shelf)}"
        p = self._pick_book("" if name in ("接着读", "上次那本", "继续") else name)
        if p is None:
            return f"书架上没找到「{name}」。书架上有：\n{self._shelf_listing(shelf)}"
        if from_start:
            self.store["reading"][p.name] = {**(self.store["reading"].get(p.name) or {}), "pos": 0}
        part, pos, total = self._turn_page(p)
        if not part.strip():
            return f"《{books.title(p)}》已经读完了。想从头再读，把 from_start 填 true。"
        notes = (self.store["reading"][p.name].get("notes") or [])[-2:]
        before = "".join(f"\n（你之前读的时候记下：{n}）" for n in notes)
        self.growth.log(f"我（读了一段《{books.title(p)}》，读到 {books.percent(pos, total)}%）", "（和 TA 聊天的时候读的）")
        end = "（这本书读完了。）" if pos >= total else f"（读到 {books.percent(pos, total)}%，下次接着往下读）"
        return f"《{books.title(p)}》{before}\n{part}\n{end}"

    @filter.llm_tool(name="choose_my_voice")
    async def choose_my_voice(self, event: AstrMessageEvent, voice_name: str):
        """给自己挑一个说话的声音（发语音时用）。只在你自己想挑、想换的时候用。可选的声音（说明是微软的标注）：
        - zh-CN-XiaoxiaoNeural：女声，温暖
        - zh-CN-XiaoyiNeural：女声，活泼
        - zh-CN-YunxiNeural：男声，活泼、阳光
        - zh-CN-YunjianNeural：男声，有激情
        - zh-CN-YunxiaNeural：男声，可爱、少年感
        - zh-CN-YunyangNeural：男声，专业、沉稳
        - zh-CN-liaoning-XiaobeiNeural：女声，东北话，幽默
        - zh-CN-shaanxi-XiaoniNeural：女声，陕西话，明快
        - zh-TW-HsiaoChenNeural：女声，台湾腔，友好
        - zh-TW-YunJheNeural：男声，台湾腔，友好
        - zh-HK-HiuMaanNeural：女声，粤语，友好
        - zh-HK-WanLungNeural：男声，粤语，友好

        Args:
            voice_name(string): 上面列表里的声音名，比如 zh-CN-XiaoxiaoNeural
        """
        if not (self._is_owner(event) and event.is_private_chat()):
            return "我的声音是我自己的事，不在这里挑。"
        name = str(voice_name or "").strip()
        if name not in voice.VOICES:
            return f"没有这个声音。能选的有：\n{voice.listing()}"
        old = self.store["voice"]
        self.store["voice"] = name
        self.store.save()
        self.growth.log("我（挑了自己的声音）", f"{name}（{voice.VOICES[name]}）" + (f"，之前是 {old}" if old else ""))
        return f"好，以后发语音就用 {name}（{voice.VOICES[name]}）。"

    @filter.llm_tool(name="send_voice")
    async def send_voice(self, event: AstrMessageEvent, text: str):
        """想用语音说的时候用：把这句话用你自己的声音说出来，作为 QQ 语音发过去。发不发语音、什么时候发由你决定。

        Args:
            text(string): 要说的话，口语、简短
        """
        text = str(text or "").strip()[:200]
        if not text:
            return "没有要说的话。"
        if not self.store["voice"]:
            if self._is_owner(event) and event.is_private_chat():
                return f"你还没给自己挑过声音。想发语音的话，先用 choose_my_voice 挑一个：\n{voice.listing()}"
            return "我还没有自己的声音，这次先打字吧。"
        if self._left("voice") <= 0:
            return f"{self._why_not('voice')}，先打字吧。"
        try:
            path = await voice.synthesize(text, self.store["voice"], data_dir() / "voice_tmp")
            await event.send(MessageChain(chain=[Record.fromFileSystem(str(path))]))
        except Exception as e:
            logger.warning(f"[大肥鱼] 发语音失败：{e}")
            return f"语音没发出去（{e}），这次先打字吧。"
        self.store["voice_count"] += 1
        self._did("voice")
        if self._is_owner(event) and event.is_private_chat() and not self._is_cron(event):
            self.growth.log("我（发了语音）", text)
        return "语音发出去了。"

    @filter.llm_tool(name="keep_sticker")
    async def keep_sticker(self, event: AstrMessageEvent, note: str):
        """把对方刚刚发来的那张图 / 表情包存进你自己的表情包收藏。只在你喜欢、想留着以后用的时候存。

        Args:
            note(string): 你自己对这张图的描述：它是什么样的、你会在什么时候想发它
        """
        recent = self._recent_images.get(event.unified_msg_origin)
        if not recent or time.time() - recent[0] > 1800:
            return "这里最近没有收到图片，没什么可存的。"
        note = str(note or "").strip()[:100]
        if not note:
            return "给它写句描述再存吧，不然以后找不到。"
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(recent[1]) as r:
                    r.raise_for_status()
                    data = await r.read()
        except Exception as e:
            return f"图片没下载下来（{e}），这次没存上。"
        if not data or len(data) > 5 * 1024 * 1024:
            return "这张图太大了（或是空的），没存。"
        new, _ = self.stickers.keep(data, note, recent[2], recent[3])
        return "存好了。" if new else "这张之前就存过，描述更新了。"

    @filter.llm_tool(name="send_sticker")
    async def send_sticker(self, event: AstrMessageEvent, query: str = ""):
        """从你自己收藏的表情包里挑一张发出去。发不发、发哪张由你决定。

        Args:
            query(string): 想表达的意思或关键词，会按你存的时候写的描述来找；也可以填编号。留空会列出你的收藏
        """
        if not self.stickers.all():
            return "你的表情包收藏还是空的。看到喜欢的，可以用 keep_sticker 存下来。"
        q = str(query or "").strip()
        if not q:
            return f"你收藏的表情包：\n{self.stickers.listing()}\n再用编号或描述挑一张。"
        item = self.stickers.find(q)
        if not item:
            return f"没找到合适的。你收藏的有：\n{self.stickers.listing()}"
        try:
            await event.send(MessageChain().file_image(str(self.stickers.path(item))))
        except Exception as e:
            return f"表情包没发出去：{e}"
        return f"发了：{item['note']}"

    def _draw_key(self) -> tuple[str, str]:
        """画图、看图用的 Key：插件配置里填了就用它，否则用 AstrBot 里已经配好的硅基流动那一项（一个 Key 多用）。"""
        own = str(self.cfg.get("draw_api_key", "") or "").strip()
        if own:
            return own, draw.DEFAULT_BASE
        try:
            return draw.find_key(self.context.get_config())
        except Exception as e:
            logger.debug(f"[大肥鱼] 读取提供商配置失败：{e}")
            return "", ""

    async def _make_picture(self, prompt: str, note: str) -> tuple[str, str]:
        """画一张存进收藏 → (文件名, 实际画出来的样子)。draw 工具和空闲时刻共用。

        画不了（上限、预算、没有 Key、接口出错）抛 DrawRefused，里面是给它看的原因。
        """
        if self._left("draw") <= 0:
            raise DrawRefused(f"{self._why_not('draw')}，这次先不画了")
        if self._over_budget():
            raise DrawRefused("今天的花销到了预算，先不画了")
        key, base = self._draw_key()
        if not key:
            raise DrawRefused("现在画不了：还没有能用的生图接口（需要硅基流动的 Key）")
        # 请求发出去就算一次：失败也可能已经扣了费
        self.store["draw_count"] += 1
        self._did("draw")
        try:
            data = await draw.generate(prompt, key, base, self.cfg.get("draw_model", "") or draw.DEFAULT_MODEL)
        except Exception as e:
            logger.warning(f"[大肥鱼] 画图失败：{e}")
            raise DrawRefused(f"没画出来（{e}）") from e
        seen = await self._see(data)  # 看一眼实际画成了什么样；看不了就只有自己写的备注
        _, name = self.stickers.keep(data, note, "我画的", seen)
        return name, seen

    @filter.llm_tool(name="draw")
    async def draw_picture(self, event: AstrMessageEvent, prompt: str, note: str, send: bool = False):
        """画一张图。画好的图存进你自己的收藏（和表情包在一起），会告诉你它的编号和实际画出来的样子：之后可以用 send_sticker 发出去，也可以配进说说。画不画、画什么由你决定。设了每天上限的话，超了就画不了。

        Args:
            prompt(string): 要画的画面，用你自己的话描述
            note(string): 你自己对这张图的描述或备注，以后在收藏里按它找
            send(boolean): 画好后要不要直接发到现在这个聊天里；不填就是不发，只存进收藏
        """
        prompt = str(prompt or "").strip()
        note = str(note or "").strip()[:100]
        if not prompt:
            return "没有要画的内容。"
        if not note:
            return "给这张图写句描述或备注再画吧，不然以后在收藏里找不到。"
        try:
            name, seen = await self._make_picture(prompt, note)
        except DrawRefused as e:
            return f"{e}。"
        n = self.stickers.number(name)
        with_ta = self._is_owner(event) and event.is_private_chat() and not self._is_cron(event)
        if with_ta:
            self.growth.log("我（画了一张图）", note + (f"\n（画出来的样子：{seen}）" if seen else ""))
        done = f"画好了，存进了收藏，编号 {n}：{note}"
        looks = f"\n实际画出来的样子：{seen}" if seen else ""
        if send:
            try:
                await event.send(MessageChain().file_image(str(self.stickers.dir / name)))
            except Exception as e:
                return f"{done}。不过没发出去：{e}{looks}"
            return f"{done}。已经发出去了。{looks}"
        return f"{done}。{looks}"

    # ---------- 指令（仅 owner 可用） ----------

    @filter.command_group("fy")
    def fy(self):
        pass

    @fy.command("状态")
    async def cmd_status(self, event: AstrMessageEvent):
        if not self._is_owner(event):
            event.stop_event()
            return
        s = self.store
        yield event.plain_result(
            f"你的私聊会话：{s['owner_umo'] or '未记录（先私聊我一句）'}\n"
            + (
                f"今天主动私聊：{self._quota(s['proactive_count'], self._day_cap())}（什么时候找你，它在空闲时刻里自己定）\n"
                + (f"下次空闲时刻：{dt.datetime.fromtimestamp(s['next_free']):%H:%M}\n" if s["next_free"] else "下次空闲时刻：还没排（启动后几分钟内排上）\n")
                if self._free_on() else
                f"今天主动私聊：{s['proactive_count']}/{self._rhythm()['reach_out_per_day']}（它自己定的）"
                + ("" if self._day_cap() else "，不设上限") + "\n"
            )
            + f"它现在的节奏{'（它自己定的）' if s['rhythm'] and self.cfg.get('rhythm_self', True) else '（配置里的初始值）'}：\n"
            f"{rhythm.describe(self._rhythm_shown())}\n"
            f"今天说说时间点（含偏移分钟）：{s['qzone_slots']}，已处理 {s['qzone_done']}\n"
            f"今天它发了 {self._quota(s['qzone_count'], self._cap('post'))} 条说说\n"
            f"今天它画了 {self._quota(s['draw_count'], self._cap('draw'))} 张图\n"
            f"今天它发了 {self._quota(s['voice_count'], self._cap('voice'))} 条语音\n"
            f"看图：{(self.cfg.get('vision_model') or vision.DEFAULT_MODEL) if self.cfg.get('vision_enabled', True) else '关着'}\n"
            f"打开链接：{'开着' if self.cfg.get('links_enabled', True) else '关着'}\n"
            f"它看到的今天：{self._day_block().strip().strip('（）') or '关着'}"
            + ("" if self._weather[1] or not (self.cfg.get("weather_place") or "").strip() else "（天气还没查到）")
            + "\n"
            f"待审核草稿：{', '.join(s['drafts']) or '无'}\n"
            f"今天在空间评论了 {self._quota(s['qzone_comment_count'], self._cap('comment'))} 条\n"
            f"它给自己记下的事：{len(s['plans'])} 件（内容是它自己的，不显示）\n"
            f"它的心愿单：{len(wishes.open_ones(s['wishes']))} 条（/fy 心愿 看）\n"
            f"它记下的日子：{len(s['my_days'])} 个（内容是它自己的，不显示）\n"
            f"书架：{len(books.shelf(self._books_dir()))} 本（放在 {self._books_dir()}）\n"
            f"查资料：{'开着' if self.cfg.get('lookup_enabled', True) else '关着'}\n"
            f"免打扰：{self.cfg.get('quiet_hours') or '无'}，现在{'是' if self._in_quiet(dt.datetime.now()) else '不是'}免打扰时段\n"
            f"上次反思：{s['reflected_day'] or '还没有'}；上次回想一个月：{s['month_reflected'] or '还没有'}\n"
            f"今天大约花了 {self.spent_today():.2f} 元"
            + (f"（预算 {self.cfg.get('daily_budget')} 元）" if float(self.cfg.get("daily_budget", 0) or 0) > 0 else "")
            + (f"\n最近一次发送失败：{s['send_error']}" if s["send_error"] else "")
            + "".join(
                f"\n防循环保险：{k} 停到 {dt.datetime.fromtimestamp(t):%H:%M}"
                for k, t in s["burst_pause"].items() if t > time.time()
            )
        )

    @fy.command("心愿")
    async def cmd_wishes(self, event: AstrMessageEvent, action: str = ""):
        """/fy 心愿 看它的心愿单；/fy 心愿 完成 <编号> [一句话] 告诉它这件已经有了。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        act, wid, note = wishes.parse_cmd(event.get_message_str())  # 那一句话里可能有空格，自己拆
        if act != "完成":
            yield event.plain_result(wishes.for_owner(self.store["wishes"]))
            return
        self.store["wishes"], w = wishes.mark_done(self.store["wishes"], wid, note, time.time())
        if w is None:
            yield event.plain_result(f"心愿单上没有还没满足的编号 {wid or '（没写编号）'}。用 /fy 心愿 看看。")
            return
        self.store.save()
        self.growth.log("TA（说我心愿单上的这件已经有了）", wishes.granted_text(w))
        yield event.plain_result(f"好，它下次和你私聊、还有夜里回想的时候会知道：{wishes.granted_text(w)}")

    @fy.command("看空间")
    async def cmd_qzone_look(self, event: AstrMessageEvent):
        """检查读空间是否正常：只报数量，不评论、不发、不标记已读。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        try:
            qz = await self._qzone()
            mine = await qz.list_posts(await self._get_bot_uin(), 5)
            items = await self._qzone_look(qz)
            yield event.plain_result(
                f"我最近 {len(mine)} 条说说，共 {sum(len(p['comments']) for p in mine)} 条评论；"
                f"下次去空间时会看到 {len(items)} 条新东西"
                f"（{sum(i['kind'] == 'reply' for i in items)} 条新评论，{sum(i['kind'] == 'owner_post' for i in items)} 条你的说说）"
            )
        except Exception as e:
            yield event.plain_result(f"看空间失败：{e}")

    @fy.command("戳我")
    async def cmd_poke(self, event: AstrMessageEvent):
        """立刻触发一次主动私聊，用来测试。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        self.store["owner_umo"] = event.unified_msg_origin
        await self.proactive("测试一下主动找 TA 聊天")
        # 有意不 stop_event：「戳」本身还会交给模型当普通聊天再回一句，更有活人感

    @fy.command("空闲")
    async def cmd_free(self, event: AstrMessageEvent):
        """立刻给它一段空闲时刻（测试用）。只回做了几件事，不回内容。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        if self._paused("free"):
            yield event.plain_result("空闲时刻的防循环保险停着，等一会儿再试。")
            return
        try:
            did = await self._free_moment(dt.datetime.now())
        except Exception as e:
            logger.error(f"[大肥鱼] 空闲时刻出错：{e}", exc_info=True)
            yield event.plain_result(f"出错了：{e}")
            return
        yield event.plain_result(
            f"它这次做了 {len(did)} 件事；下次空闲时刻：{dt.datetime.fromtimestamp(self.store['next_free']):%H:%M}"
        )

    @fy.command("反思")
    async def cmd_reflect(self, event: AstrMessageEvent):
        """立刻反思今天到目前为止的对话（测试用）。内容不回显——那是它自己的。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        try:
            done = await self.reflect(dt.datetime.now().strftime("%Y-%m-%d"))
            yield event.plain_result("嗯……想完了。" if done else "今天还没什么可想的。")
        except Exception as e:
            logger.error(f"[大肥鱼] 反思失败：{e}", exc_info=True)
            yield event.plain_result(f"想着想着走神了：{e}")

    @fy.command("回想这个月")
    async def cmd_reflect_month(self, event: AstrMessageEvent):
        """立刻回想上个月（测试用）。内容不回显——那是它自己的。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        month = (dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
        try:
            done = await self.reflect_month(month)
            if done:
                self.store["month_reflected"] = max(self.store["month_reflected"], month)
                self.store.save()
            yield event.plain_result("嗯……想完了。" if done else f"{month} 还没写过日记，没什么可想的。")
        except Exception as e:
            logger.error(f"[大肥鱼] 月度回想失败：{e}", exc_info=True)
            yield event.plain_result(f"想着想着走神了：{e}")

    @fy.command("回滚")
    async def cmd_rollback(self, event: AstrMessageEvent):
        """安全兜底：把自我认识退回上一版。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        snap = self.growth.rollback()
        yield event.plain_result(f"已退回到 {snap} 之前的样子" if snap else "没有可以退回的版本")

    @fy.command("写说说")
    async def cmd_write(self, event: AstrMessageEvent, topic: str = ""):
        if not self._is_owner(event):
            event.stop_event()
            return
        text, pic = await self.write_post(topic)
        did = str(self.store["next_draft_id"])
        self.store["next_draft_id"] += 1
        self.store["drafts"][did] = {"text": text, "pic": pic}
        self.store.save()
        yield event.plain_result(
            f"草稿 {did}：\n{text}\n" + (f"（配图：{pic['note']}）\n" if pic else "")
            + f"\n/fy 发 {did} 发布，/fy 弃 {did} 丢掉"
        )

    @fy.command("发")
    async def cmd_approve(self, event: AstrMessageEvent, draft_id: str):
        if not self._is_owner(event):
            event.stop_event()
            return
        draft = self.store["drafts"].pop(str(draft_id), None)
        if draft is None:
            yield event.plain_result(f"没有草稿 {draft_id}")
            return
        try:
            tid = await self.publish_post(*self._draft(draft))  # 草稿带着配图就一起发
            yield event.plain_result(f"发好啦～（tid: {tid}）")
        except (QzoneError, Exception) as e:
            self.store["drafts"][str(draft_id)] = draft
            self.store.save()
            yield event.plain_result(f"发失败了：{e}")

    @fy.command("弃")
    async def cmd_drop(self, event: AstrMessageEvent, draft_id: str):
        if not self._is_owner(event):
            event.stop_event()
            return
        ok = self.store["drafts"].pop(str(draft_id), None) is not None
        self.store.save()
        yield event.plain_result("好吧，不发了" if ok else f"没有草稿 {draft_id}")

    @fy.command("删说说")
    async def cmd_delete(self, event: AstrMessageEvent, tid: str = ""):
        """不带参数删最近一条。"""
        if not self._is_owner(event):
            event.stop_event()
            return
        tid = tid or (self.store["posted"][-1]["tid"] if self.store["posted"] else "")
        if not tid:
            yield event.plain_result("没有可删的说说")
            return
        try:
            await (await self._qzone()).delete(tid)
            self.store["posted"] = [p for p in self.store["posted"] if p["tid"] != tid]
            self.store.save()
            yield event.plain_result("删掉了")
        except Exception as e:
            yield event.plain_result(f"删除失败：{e}")
