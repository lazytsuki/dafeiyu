import asyncio
import datetime as dt
import json
import random
import re
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

from .qzone import QzoneClient, QzoneError
from .store import Store

PLUGIN_DIR = Path(__file__).parent
TICK_SECONDS = 60
FIXED_WINDOW_MINUTES = 30  # 固定问候错过多久之内还补发


def _hm(s: str) -> dt.time:
    h, m = s.strip().split(":")
    return dt.time(int(h), int(m))


def _clean(text: str) -> str:
    text = text.strip().strip("“”\"'")
    return re.sub(r"^(大肥鱼|我)[:：]\s*", "", text)


@register("astrbot_plugin_dafeiyu", "dafeiyu", "大肥鱼：主动私聊主人 + 自动发说说", "0.1.0")
class Dafeiyu(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config
        self.store = Store()
        self._bot_uin = ""
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        self._task.cancel()

    # ---------- 基础能力 ----------

    def _persona(self) -> str:
        override = (self.cfg.get("persona_override") or "").strip()
        if override:
            return override
        return (PLUGIN_DIR / "persona.md").read_text("utf-8")

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

    async def _llm(self, prompt: str) -> str:
        umo = self.store["owner_umo"] or None
        provider = self.context.get_using_provider(umo=umo)
        if provider is None:
            raise RuntimeError("AstrBot 里还没有启用聊天模型")
        resp = await provider.text_chat(
            prompt=prompt, system_prompt=self._persona(), contexts=[]
        )
        return _clean(resp.completion_text or "")

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

    # ---------- 主动私聊 ----------

    async def proactive(self, kind: str = "随便聊聊") -> str:
        now = dt.datetime.now()
        idle_min = int((time.time() - self.store["owner_last_msg"]) / 60)
        idle = f"{idle_min // 60} 小时 {idle_min % 60} 分钟" if self.store["owner_last_msg"] else "很久"
        prompt = (
            f"现在是 {now:%Y-%m-%d %H:%M}（{'一二三四五六日'[now.weekday()]}），"
            f"主人已经 {idle} 没跟你说话了。\n"
            f"最近的聊天记录：\n{await self._recent_history() or '（暂无）'}\n\n"
            f"请你主动给主人发一条 QQ 私聊消息，目的：{kind}。"
            "要求：像真人发消息一样口语、简短（一到三句），可以接着之前的话题，"
            "也可以分享你此刻在做的事或想到的东西；多句时用换行分开；"
            "不要解释、不要加引号，直接输出要发的内容。"
        )
        text = await self._llm(prompt)
        if text:
            await self._send_owner(text)
        return text

    # ---------- 说说 ----------

    async def write_post(self, topic: str = "") -> str:
        topics = self.cfg.get("qzone_topics") or []
        topic = topic or (random.choice(topics) if topics else "今天的心情")
        posted = "\n".join(p["text"][:60] for p in self.store["posted"][-5:])
        prompt = (
            f"现在是 {dt.datetime.now():%Y-%m-%d %H:%M}。请以你自己的身份写一条 QQ 空间说说，"
            f"主题参考：{topic}。\n要求：50 字以内，生活化、有画面感，可以带 1-2 个 emoji，"
            "不要带话题标签，不要提到自己是 AI，直接输出说说正文。\n"
            f"最近发过的说说（别重复）：\n{posted or '（无）'}"
        )
        return await self._llm(prompt)

    async def publish_post(self, text: str) -> str:
        tid = await (await self._qzone()).publish(text)
        self.store["posted"] = (
            self.store["posted"] + [{"t": time.time(), "tid": tid, "text": text}]
        )[-30:]
        self.store.save()
        return tid

    async def scheduled_post(self):
        text = await self.write_post()
        if not text:
            return
        if self.cfg.get("qzone_review", True) and self.store["owner_umo"]:
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
        self.store["qzone_done"] = []
        self.store["qzone_slots"] = {
            t: random.randint(-off, off) for t in self.cfg.get("qzone_times") or []
        }
        self.store.save()

    async def _tick(self):
        now = dt.datetime.now()
        self._new_day(now)
        ts = time.time()

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

    async def _loop(self):
        await asyncio.sleep(10)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[大肥鱼] 定时任务出错：{e}", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    # ---------- 钩子：记住主人、让模型知道自己刚主动说过什么 ----------

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not (self._is_owner(event) and event.is_private_chat()):
            return
        if self.store["owner_umo"] != event.unified_msg_origin:
            self.store["owner_umo"] = event.unified_msg_origin
            logger.info(f"[大肥鱼] 已记住主人会话：{event.unified_msg_origin}")
        self.store["owner_last_msg"] = time.time()
        self.store.save()
        recent = [m for m in self.store["recent_proactive"] if time.time() - m["t"] < 6 * 3600]
        if recent:
            said = "\n".join(
                f"[{dt.datetime.fromtimestamp(m['t']):%H:%M}] {m['text']}" for m in recent
            )
            req.system_prompt += f"\n\n（你之前主动给主人发过这些消息，主人现在可能是在回应：\n{said}）"

    # ---------- 指令（仅主人可用） ----------

    @filter.command_group("fy")
    def fy(self):
        pass

    @fy.command("状态")
    async def cmd_status(self, event: AstrMessageEvent):
        if not self._is_owner(event):
            return
        s = self.store
        yield event.plain_result(
            f"主人会话：{s['owner_umo'] or '未记录（先私聊我一句）'}\n"
            f"今天主动私聊：{s['proactive_count']}/{self.cfg.get('daily_max', 4)}\n"
            f"今天说说时间点（含偏移分钟）：{s['qzone_slots']}，已处理 {s['qzone_done']}\n"
            f"待审核草稿：{', '.join(s['drafts']) or '无'}\n"
            f"免打扰：{self.cfg.get('quiet_hours') or '无'}，现在{'是' if self._in_quiet(dt.datetime.now()) else '不是'}免打扰时段"
        )

    @fy.command("戳我")
    async def cmd_poke(self, event: AstrMessageEvent):
        """立刻触发一次主动私聊，用来测试。"""
        if not self._is_owner(event):
            return
        self.store["owner_umo"] = event.unified_msg_origin
        await self.proactive("测试一下主动找主人聊天")

    @fy.command("写说说")
    async def cmd_write(self, event: AstrMessageEvent, topic: str = ""):
        if not self._is_owner(event):
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
            return
        ok = self.store["drafts"].pop(str(draft_id), None) is not None
        self.store.save()
        yield event.plain_result("好吧，不发了" if ok else f"没有草稿 {draft_id}")

    @fy.command("删说说")
    async def cmd_delete(self, event: AstrMessageEvent, tid: str = ""):
        """不带参数删最近一条。"""
        if not self._is_owner(event):
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
