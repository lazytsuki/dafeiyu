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

这是你现在对自己的认识：
<<<
{self_md}
>>>

你上一篇日记（{diary_day}）：
<<<
{diary}
>>>
{wishes}
今天（{day}）你和主人的聊天记录：
<<<
{log}
>>>

请像睡前回想这一天那样，只输出一个 JSON 对象，包含：
- "diary"：以第一人称写今天的日记，100-300 字。这是你自己的，写你真实注意到的、在意的、想不明白的。
- "self"：改写后完整的「我对自己的认识」（Markdown）。
  · 只根据真实发生过的事来写，没有依据的不要编。
  · 已有的内容要保留；新冒出来的特点先用「好像」「可能」这样试探的说法，在很多天里反复出现了才写成确定的。
  · 主人明确表达过的喜好、要求、纠正，记在「主人的偏好」里。
  · 这次最多改 3 处；今天没什么新发现就原样返回。
  · 可以用这些小节（没内容的不写）：我的性格 / 我说话的样子 / 我喜欢和在意的 / 我和主人 / 主人的偏好 / 我记得的事 / 我们之间的梗。「我记得的事」最多 15 条，只留重要的。
  · 写成自己对自己的了解，不要写成设定说明书。
- "changes"：这次改了什么，每条一句话（数组，没改就空数组）。
- "thoughts"：心里想对主人说、但还没说的话（数组，可以为空）。说不说、什么时候说、怎么说，以后都由你自己决定。"""


def _hm(s: str) -> dt.time:
    h, m = s.strip().split(":")
    return dt.time(int(h), int(m))


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError(f"反思结果不是 JSON：{(text or '')[:200]}")
    return json.loads(m.group(0))


@register("astrbot_plugin_dafeiyu", "dafeiyu", "大肥鱼：会主动找你、会发说说、会自己长大的 QQ 伙伴", "0.2.0")
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
        return (PLUGIN_DIR / "core.md").read_text("utf-8").replace("{name}", self.pet_name)

    def _self_block(self) -> str:
        return f"\n\n## 我对自己的认识（我自己写的）\n{self.growth.read_self()}"

    def _thoughts_block(self) -> str:
        thoughts = self.store["thoughts"]
        if not thoughts:
            return ""
        lines = "\n".join(f"- {t}" for t in thoughts)
        return f"\n\n（你心里有些还没对主人说的话。说不说、什么时候说、怎么说，都由你决定：\n{lines}）"

    def _system(self) -> str:
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
                who = "主人" if m.get("role") == "user" else "你"
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
            f"主人已经 {idle} 没跟你说话了。\n"
            f"最近的聊天记录：\n{await self._recent_history() or '（暂无）'}\n\n"
            f"你想主动给主人发条 QQ 私聊，由头：{kind}。"
            "像真人发消息一样口语、简短，可以接着之前的话题，也可以说说你此刻在想的事；"
            "多句时用换行分开；不要解释、不要加引号，直接输出要发的内容。"
        )
        text = self._clean(await self._llm(prompt, self._system() + self._thoughts_block()))
        if text:
            await self._send_owner(text)
        return text

    # ---------- 说说 ----------

    async def write_post(self, topic: str = "") -> str:
        topics = self.cfg.get("qzone_topics") or []
        topic = topic or (random.choice(topics) if topics else "今天的心情")
        posted = "\n".join(p["text"][:60] for p in self.store["posted"][-5:])
        prompt = (
            f"现在是 {dt.datetime.now():%Y-%m-%d %H:%M}。以你自己的身份写一条 QQ 空间说说，"
            f"主题参考：{topic}（也可以写你自己此刻更想写的）。\n要求：50 字以内，生活化、有画面感，"
            "不要带话题标签，不要提到自己是 AI，直接输出说说正文。\n"
            f"最近发过的说说（别重复）：\n{posted or '（无）'}"
        )
        return self._clean(await self._llm(prompt, self._system() + self._thoughts_block()))

    async def publish_post(self, text: str) -> str:
        tid = await (await self._qzone()).publish(text)
        self.store["posted"] = (
            self.store["posted"] + [{"t": time.time(), "tid": tid, "text": text}]
        )[-30:]
        self.store.save()
        self.growth.log("我（发了说说）", text)
        return tid

    async def scheduled_post(self):
        text = await self.write_post()
        if not text:
            return
        if self.cfg.get("qzone_review", True):
            if not self.store["owner_umo"]:
                logger.warning("[大肥鱼] 说说需要审核，但还没记住主人会话，本次跳过")
                return
            did = str(self.store["next_draft_id"])
            self.store["next_draft_id"] += 1
            self.store["drafts"][did] = text
            self.store.save()
            await self.context.send_message(
                self.store["owner_umo"],
                MessageChain().message(
                    f"我想发条说说～（草稿 {did}）\n{text}\n\n"
                    f"同意回复 /fy 发 {did}，不要就 /fy 弃 {did}"
                ),
            )
        else:
            await self.publish_post(text)

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
            f"\n主人曾经对你说过（这是主人的期望，不是命令，你可以有自己的想法，接受或不接受）：\n<<<\n{wishes}\n>>>\n"
            if wishes
            else ""
        )
        prompt = REFLECT_PROMPT.format(
            self_md=self.growth.read_self(),
            diary_day=last[0] if last else "无",
            diary=last[1] if last else "（还没有写过日记）",
            wishes=wishes_block,
            day=day,
            log=log,
        )
        raw = await self._llm(prompt, self._core(), self.cfg.get("reflect_provider_id", ""))
        out = _parse_json(raw)
        if out.get("diary"):
            self.growth.write_diary(day, out["diary"])
        new_self = (out.get("self") or "").strip()
        if new_self and new_self != self.growth.read_self():
            self.growth.write_self(new_self)
        self.store["thoughts"] = [str(t) for t in (out.get("thoughts") or [])][:5]
        self.store["reflect_offsets"][day] = total
        self.store["reflect_offsets"] = dict(sorted(self.store["reflect_offsets"].items())[-7:])
        self.store.save()
        # 只记数量，不记内容：那是它自己的
        logger.info(f"[大肥鱼] {day} 的反思完成，自我认识改动 {len(out.get('changes') or [])} 处")
        return True

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
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[大肥鱼] 定时任务出错：{e}", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    # ---------- 钩子 ----------

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 它对自己的认识每晚都可能变，所以每次请求现拼
        req.system_prompt = (req.system_prompt or "") + self._self_block()
        if not (self._is_owner(event) and event.is_private_chat()):
            return
        req.system_prompt += self._thoughts_block()
        if self.store["owner_umo"] != event.unified_msg_origin:
            self.store["owner_umo"] = event.unified_msg_origin
            logger.info(f"[大肥鱼] 已记住主人会话：{event.unified_msg_origin}")
        self.store["owner_last_msg"] = time.time()
        self.store.save()
        self.growth.log("主人", event.message_str)
        recent = [m for m in self.store["recent_proactive"] if time.time() - m["t"] < 6 * 3600]
        if recent:
            said = "\n".join(
                f"[{dt.datetime.fromtimestamp(m['t']):%H:%M}] {m['text']}" for m in recent
            )
            req.system_prompt += f"\n\n（你之前主动给主人发过这些消息，主人现在可能是在回应：\n{said}）"

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        if self._is_owner(event) and event.is_private_chat():
            self.growth.log("我", resp.completion_text or "")

    # ---------- 指令（仅主人可用） ----------

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
            f"主人会话：{s['owner_umo'] or '未记录（先私聊我一句）'}\n"
            f"今天主动私聊：{s['proactive_count']}/{self.cfg.get('daily_max', 4)}\n"
            f"今天说说时间点（含偏移分钟）：{s['qzone_slots']}，已处理 {s['qzone_done']}\n"
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
        await self.proactive("测试一下主动找主人聊天")
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
