import asyncio
import datetime as dt
import json
import random
import re
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register

from .growth import Growth
from .qzone import QzoneClient, QzoneError
from .store import Store, data_dir

PLUGIN_DIR = Path(__file__).parent
TICK_SECONDS = 60
FIXED_WINDOW_MINUTES = 30  # 固定问候错过多久之内还补发

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


def _hm(s: str) -> dt.time:
    h, m = s.strip().split(":")
    return dt.time(int(h), int(m))


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError(f"反思结果不是 JSON：{(text or '')[:200]}")
    return json.loads(m.group(0))


@register("astrbot_plugin_dafeiyu", "dafeiyu", "大肥鱼：会主动找你、会发说说、会自己长大的 QQ 伙伴", "0.3.0")
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
        return resp.completion_text or ""

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
            await self.context.send_message(umo, MessageChain().message(p))
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
        text = self._clean(await self._llm(prompt, self._system() + self._thoughts_block()))
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

    async def scheduled_post(self):
        """一个「想不想发条说说」的时刻：发不发、发什么都由它决定。"""
        if self.store["qzone_count"] >= int(self.cfg.get("qzone_daily_max", 2)):
            return  # 防封护栏，不是性格约束
        text = await self.write_post(optional=True)
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
        """QQ（NapCat）现在连着吗。没连上时，到点的事都先别做，等连上再说。"""
        try:
            await asyncio.wait_for(self._onebot().api.call_action("get_login_info"), 10)
            return True
        except Exception:
            return False

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

        # 每晚反思前一天
        yesterday = (now - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        reflect_at = self.cfg.get("reflect_time", "03:30")
        if reflect_at and now.time() >= _hm(reflect_at) and self.store["reflected_day"] != yesterday:
            self.store["reflected_day"] = yesterday
            self.store.save()
            await self.reflect(yesterday)

        # 下面的事都要通过 QQ：没连上就整轮跳过，时间点不消耗，等连上了再做
        if not await self._online():
            return
        if self.store["drafts_unsent"] and self.store["owner_umo"]:
            await self._send_pending_drafts()
        if self.store["pending_signature"]:
            sig = self.store["pending_signature"]
            await self._onebot().api.call_action("set_self_longnick", longNick=sig)
            self.store["signature"], self.store["pending_signature"] = sig, ""
            self.store.save()
            self.growth.log("我（换了签名）", sig)
            logger.info("[大肥鱼] 它换了个性签名")

        if self.cfg.get("qzone_enabled", True):
            for t, off in self.store["qzone_slots"].items():
                due = dt.datetime.combine(now.date(), _hm(t)) + dt.timedelta(minutes=off)
                if now >= due and t not in self.store["qzone_done"]:
                    self.store["qzone_done"].append(t)
                    self.store.save()
                    if not self._in_quiet(now):
                        await self.scheduled_post()

        if not (self.cfg.get("proactive_enabled", True) and self.store["owner_umo"]):
            return
        if self._in_quiet(now):
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

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 它对自己的认识每晚都可能变，所以每次请求现拼
        req.system_prompt = (req.system_prompt or "") + self._self_block()
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
        req.system_prompt += self._bond_block() + self._thoughts_block()
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
        if self._is_owner(event) and event.is_private_chat():
            self.growth.log("我", resp.completion_text or "")

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
            f"免打扰：{self.cfg.get('quiet_hours') or '无'}，现在{'是' if self._in_quiet(dt.datetime.now()) else '不是'}免打扰时段\n"
            f"上次反思：{s['reflected_day'] or '还没有'}"
        )

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
