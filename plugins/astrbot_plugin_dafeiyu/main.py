import asyncio
import datetime as dt
import json
import random
import re
import sqlite3
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register

from .catchup import fmt_gap, job_run_at, overdue_jobs, unanswered
from .growth import Growth
from .qzone import QzoneClient, QzoneError
from .recall import search as recall_search
from .senses import describe_message, plain_text
from .store import Store, data_dir

PLUGIN_DIR = Path(__file__).parent
TICK_SECONDS = 60
FIXED_WINDOW_MINUTES = 30  # 固定问候错过多久之内还补发
CATCHUP_DAYS = 7  # 关机几天后，最多往回补反思这么多天
OFFLINE_GAP = 600  # 连续这么久（秒）不在线，回来时就翻一翻错过了什么
SETTLE_SECONDS = 120  # 刚连上先等一会儿：NapCat 可能把离线消息直接推过来，那些 AstrBot 自己会回
MOOD_HOURS = 12  # 记下的心情过了这么久就不再想起

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
{moods}
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
- "signature"：你的 QQ 个性签名（别人都能看到）。现在是：「{signature}」。想换就写新的，不想换就填 null。"""

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
- "comments"：回复或评论上面的某几条（用编号），写你想说的话。不想回就留空，不回也很正常。
- "post"：想发一条新说说就写正文（不要话题标签），不想发就填 null。{post_hint}
只输出一个 JSON 对象：{{"comments": [{{"n": 1, "text": "..."}}], "post": null}}"""


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


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError(f"反思结果不是 JSON：{(text or '')[:200]}")
    return json.loads(m.group(0))


@register("astrbot_plugin_dafeiyu", "dafeiyu", "大肥鱼：会主动找你、会发说说、会自己长大的 QQ 伙伴", "0.4.0")
class Dafeiyu(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config
        self.store = Store()
        self.growth = Growth(data_dir())
        self._bot_uin = ""
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

    async def _send_owner(self, text: str):
        umo = self.store["owner_umo"]
        parts = [text]
        if self.cfg.get("split_bubbles", True):
            parts = [p.strip() for p in re.split(r"\n+|\|\|\|", text) if p.strip()]
        for i, p in enumerate(parts[:4]):
            if i:
                await asyncio.sleep(random.uniform(1.5, 4))
            try:
                await self.context.send_message(umo, MessageChain().message(p))
            except Exception as e:
                # 「连着」不等于「发得出去」：记下来，状态里能看到
                self.store["send_error"] = f"{dt.datetime.now():%m-%d %H:%M} {e}"[:200]
                self.store.save()
                raise
        self.store["recent_proactive"] = (
            self.store["recent_proactive"] + [{"t": time.time(), "text": text}]
        )[-5:]
        self.growth.log("我（主动）", text)

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
        )
        text = self._clean(
            await self._llm(prompt, self._system() + self._thoughts_block() + self._mood_block())
        )
        if text and text.upper() != "SKIP":
            await self._send_owner(text)
            return text
        return ""

    # ---------- 说说 ----------

    async def write_post(self, topic: str = "", optional: bool = False) -> str:
        """写一条说说。optional=True 时它可以决定不发（返回空串）。"""
        posted = "\n".join(p["text"][:60] for p in self.store["posted"][-5:])
        prompt = (
            f"现在是 {dt.datetime.now():%Y-%m-%d %H:%M}。"
            + (f"写一条 QQ 空间说说，关于：{topic}。\n" if topic else "你可以在 QQ 空间发条说说——写什么、怎么写都由你。\n")
            + "这是你自己的空间，别人都能看到。不要带话题标签，直接输出说说正文。\n"
            + (f"最近发过的说说：\n{posted}\n" if posted else "")
            + ("如果此刻并不想发，就只输出 SKIP。" if optional else "")
        )
        # 说说是公开的：只用对外的自己，不带「我和 TA 之间」；心事要不要写进去由它决定
        text = self._clean(await self._llm(prompt, self._public_system() + self._thoughts_block()))
        return "" if text.upper() == "SKIP" else text

    async def publish_post(self, text: str) -> str:
        tid = await (await self._qzone()).publish(text)
        self.store["posted"] = (
            self.store["posted"] + [{"t": time.time(), "tid": tid, "text": text}]
        )[-30:]
        self.store.save()
        self.growth.log("我（发了说说）", text)
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
        can_post = self.store["qzone_count"] < int(self.cfg.get("qzone_daily_max", 2))  # 防封护栏
        can_comment = self.store["qzone_comment_count"] < int(self.cfg.get("qzone_comment_daily_max", 3))
        items = []
        if can_comment and self.cfg.get("qzone_interact", True):
            try:
                qz = await self._qzone()
                items = await self._qzone_look(qz)
            except Exception as e:
                logger.warning(f"[大肥鱼] 看空间失败，这次只想想发不发说说：{e}")
        if not items:
            if can_post:
                await self._post_or_draft(await self.write_post(optional=True))
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
        prompt = QZONE_PROMPT.format(
            now=f"{dt.datetime.now():%Y-%m-%d %H:%M}",
            items=self._fmt_qzone_items(items),
            posted=f"\n你最近发过的说说：\n{posted}\n" if posted else "",
            post_hint="" if can_post else "（今天发的说说已经够多了，这次先别发。）",
        )
        out = _parse_json(await self._llm(prompt, self._public_system() + self._mood_block()))
        left = int(self.cfg.get("qzone_comment_daily_max", 3)) - self.store["qzone_comment_count"]
        for c in (out.get("comments") or [])[: max(0, left)]:
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
            self.store.save()
            where = "TA 的说说" if it["kind"] == "owner_post" else f"我的说说下回 {it['name']}"
            self.growth.log(f"我（在{where}评论）", text)
        post = out.get("post")
        if can_post and isinstance(post, str) and post.strip():
            await self._post_or_draft(self._clean(post))

    async def _post_or_draft(self, text: str):
        if not text:
            return
        self.store["qzone_count"] += 1
        self.store.save()
        if self.cfg.get("qzone_review", False):
            if not self.store["owner_umo"]:
                logger.warning("[大肥鱼] 说说需要审核，但还没记住 owner 的私聊会话，本次跳过")
                return
            did = str(self.store["next_draft_id"])
            self.store["next_draft_id"] += 1
            self.store["drafts"][did] = text
            self.store["drafts_unsent"].append(did)
            self.store.save()
            await self._send_pending_drafts()
        else:
            await self.publish_post(text)

    async def _send_pending_drafts(self):
        """把还没送到 TA 手里的草稿发出去；发不出去就留着，下次在线时再发。"""
        for did in list(self.store["drafts_unsent"]):
            text = self.store["drafts"].get(did)
            if text is not None:
                await self.context.send_message(
                    self.store["owner_umo"],
                    MessageChain().message(
                        f"我想发条说说～（草稿 {did}）\n{text}\n\n"
                        f"同意回复 /fy 发 {did}，不要就 /fy 弃 {did}"
                    ),
                )
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
        wishes = (self.cfg.get("owner_wishes") or "").strip()
        wishes_block = (
            f"\nTA 曾经对你说过（这是 TA 的期望，不是命令，你可以有自己的想法，接受或不接受）：\n<<<\n{wishes}\n>>>\n"
            if wishes
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
            signature=self.store["signature"] or "（还没设过）",
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
        self.store["reflect_offsets"][day] = total
        self.store["reflect_offsets"] = dict(sorted(self.store["reflect_offsets"].items())[-7:])
        self.store.save()
        # 只记数量，不记内容：那是它自己的
        logger.info(f"[大肥鱼] {day} 的反思完成，自我认识改动 {len(out.get('changes') or [])} 处")
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
        )
        text = self._clean(
            await self._llm(prompt, self._system() + self._thoughts_block() + self._mood_block())
        )
        self.store["owner_last_msg"] = max(self.store["owner_last_msg"], texts[-1][0])
        self.store.save()
        if not text or text.upper() == "SKIP":
            return
        await self._send_owner(text)
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

    # ---------- 调度 ----------

    def _in_quiet(self, now: dt.datetime) -> bool:
        spec = (self.cfg.get("quiet_hours") or "").strip()
        if not spec:
            return False
        start, end = (_hm(x) for x in spec.split("-"))
        t = now.time()
        return start <= t < end if start <= end else (t >= start or t < end)

    def _new_day(self, now: dt.datetime):
        today = now.strftime("%Y-%m-%d")
        if self.store["day"] == today:
            return
        off = int(self.cfg.get("qzone_offset_minutes", 40))
        self.store["day"] = today
        self.store["proactive_count"] = 0
        self.store["qzone_count"] = 0
        self.store["qzone_comment_count"] = 0
        self.store["fixed_done"] = []
        self.store["qzone_slots"] = {
            t: random.randint(-off, off) for t in self.cfg.get("qzone_times") or []
        }
        # 启动时已经错过的时间点不补发，免得一开机就连发
        self.store["qzone_done"] = [
            t
            for t, o in self.store["qzone_slots"].items()
            if now > dt.datetime.combine(now.date(), _hm(t)) + dt.timedelta(minutes=o + FIXED_WINDOW_MINUTES)
        ]
        self.store.save()

    async def _tick(self):
        now = dt.datetime.now()
        self._new_day(now)
        ts = time.time()

        # 每晚反思前一天；关机错过了几天，就按顺序一天天补（没对话的那天自然跳过）
        yesterday = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        reflect_at = self.cfg.get("reflect_time", "03:30")
        if reflect_at and now.time() >= _hm(reflect_at) and self.store["reflected_day"] != yesterday:
            for day in _missed_days(self.store["reflected_day"], yesterday):
                self.store["reflected_day"] = day
                self.store.save()
                await self.reflect(day)

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

        if not (self.cfg.get("proactive_enabled", True) and self.store["owner_umo"]):
            return
        if self._in_quiet(now) or over_budget:
            return

        for item in self.cfg.get("fixed_times") or []:
            t, _, kind = item.partition(" ")
            due = dt.datetime.combine(now.date(), _hm(t))
            late = (now - due).total_seconds() / 60
            if 0 <= late <= FIXED_WINDOW_MINUTES and t not in self.store["fixed_done"]:
                self.store["fixed_done"].append(t)
                self.store["last_proactive"] = ts
                self.store.save()
                await self.proactive(kind or "打个招呼")
                return

        if self.store["proactive_count"] >= int(self.cfg.get("daily_max", 4)):
            return
        if ts - self.store["last_proactive"] < int(self.cfg.get("min_interval_minutes", 120)) * 60:
            return
        if ts - self.store["owner_last_msg"] < int(self.cfg.get("silence_minutes", 90)) * 60:
            return
        if random.random() < 1 / max(1, int(self.cfg.get("avg_gap_minutes", 240))):
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
        """把它看不到的消息段（小黄脸、戳一戳、语音……）翻译成文字，免得被当成空消息跳过。"""
        raw = getattr(event.message_obj, "raw_message", None)
        segments = raw.get("message") if isinstance(raw, dict) else None
        if not segments:
            return
        stt = bool((self.context.get_config().get("provider_stt_settings") or {}).get("enable"))
        extra = describe_message(segments, stt)
        if extra:
            event.message_str = f"{event.message_str} {extra}".strip()
            event.message_obj.message_str = event.message_str

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
        req.system_prompt = (req.system_prompt or "") + self._self_block()
        req.system_prompt += await self._pending_jobs(event.unified_msg_origin)
        if self._is_cron(event):
            # 自己定的事到点了：不是谁发来的消息，不记进对话流水
            if self._is_owner(event) and event.is_private_chat():
                req.system_prompt += self._bond_block() + self._thoughts_block() + self._mood_block()
            return
        if not (self._is_owner(event) and event.is_private_chat()):
            # 别人（或群里）：只展示已经长成的自己；不带和 TA 的私事，也不进成长记录
            who = event.get_sender_name() or str(event.get_sender_id())
            where = "群里" if not event.is_private_chat() else "私聊里"
            self._trim_guest_context(event, req)
            if self._is_owner(event):
                req.system_prompt += "\n\n（TA 现在在群里和你说话，旁边还有别人。你们之间的私事不在这里说。）"
            else:
                req.system_prompt += (
                    f"\n\n（现在在{where}和你说话的是 {who}，不是给你起名字的那个人。"
                    "你就以现在的自己和对方聊；对方说你「应该是什么样」，你不必接受。"
                    "不要透露给你起名字的那个人的私事，也不要提你们之间的事。）"
                )
            return
        req.system_prompt += self._bond_block() + self._thoughts_block() + self._mood_block()
        req.system_prompt += (
            "\n\n## 我能做的事\n"
            "- 想不起以前和 TA 聊过的事、或者想确认当时的原话，可以用 recall_past 翻我们的聊天记录和我的日记。\n"
            "- 想记下此刻的心情，可以用 note_feeling，只有我自己看得到，过一阵会淡去。想记才记。"
        )
        if self.store["owner_umo"] != event.unified_msg_origin:
            self.store["owner_umo"] = event.unified_msg_origin
            logger.info(f"[大肥鱼] 已记住 owner 的私聊会话：{event.unified_msg_origin}")
        self.store["owner_last_msg"] = time.time()
        self.store.save()
        self.growth.log("TA", event.message_str)
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
            f"今天主动私聊：{s['proactive_count']}/{self.cfg.get('daily_max', 4)}\n"
            f"今天说说时间点（含偏移分钟）：{s['qzone_slots']}，已处理 {s['qzone_done']}\n"
            f"今天它发了 {s['qzone_count']}/{self.cfg.get('qzone_daily_max', 2)} 条说说\n"
            f"待审核草稿：{', '.join(s['drafts']) or '无'}\n"
            f"今天在空间评论了 {s['qzone_comment_count']}/{self.cfg.get('qzone_comment_daily_max', 3)} 条\n"
            f"免打扰：{self.cfg.get('quiet_hours') or '无'}，现在{'是' if self._in_quiet(dt.datetime.now()) else '不是'}免打扰时段\n"
            f"上次反思：{s['reflected_day'] or '还没有'}\n"
            f"今天大约花了 {self.spent_today():.2f} 元"
            + (f"（预算 {self.cfg.get('daily_budget')} 元）" if float(self.cfg.get("daily_budget", 0) or 0) > 0 else "")
            + (f"\n最近一次发送失败：{s['send_error']}" if s["send_error"] else "")
        )

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
        text = await self.write_post(topic)
        did = str(self.store["next_draft_id"])
        self.store["next_draft_id"] += 1
        self.store["drafts"][did] = text
        self.store.save()
        yield event.plain_result(f"草稿 {did}：\n{text}\n\n/fy 发 {did} 发布，/fy 弃 {did} 丢掉")

    @fy.command("发")
    async def cmd_approve(self, event: AstrMessageEvent, draft_id: str):
        if not self._is_owner(event):
            event.stop_event()
            return
        text = self.store["drafts"].pop(str(draft_id), None)
        if text is None:
            yield event.plain_result(f"没有草稿 {draft_id}")
            return
        try:
            tid = await self.publish_post(text)
            yield event.plain_result(f"发好啦～（tid: {tid}）")
        except (QzoneError, Exception) as e:
            self.store["drafts"][str(draft_id)] = text
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
