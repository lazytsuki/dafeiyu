"""它自己的小本子：给自己记下想做的事，可以定个时间，也可以不定。

和 AstrBot 的「未来任务」不一样：那是在某个会话里到点把它叫醒；这里只是它给自己记的事，
到了时间，插件给它一个属于自己的时刻（见 main.py 的 _plan_moment），做不做、怎么做由它决定。

每条：{"id": 编号, "at": 定的时间戳（0 = 没定时间）, "what": 它自己写的, "made": 记下的时间戳,
       "progress": [{"t": 时间戳, "text": 它写的进度}]（做了好几天的事才有，见 add_progress）}
这里只放纯逻辑（解析时间、增删、整理、列出来），存取和护栏在 main.py。
"""

import datetime as dt
import re

MAX_PROGRESS = 30  # 每件事最多留这么多条进度（早的丢掉；进度同时记在成长流水里，翻得回来）
PROGRESS_CHARS = 800  # 一条进度最长这么多字
MAX_PLANS = 100  # 不是限制，是防出错刷满的保险；满了要先划掉几条才能再记（不替它丢）
STALE_DAYS = 30  # 定的时间过了这么久、或者没定时间又放了这么久，就自动清掉
FORMATS = "「YYYY-MM-DD HH:MM」「MM-DD HH:MM」「HH:MM」「明天 HH:MM」"

_T = r"([01]?\d|2[0-3]):([0-5]\d)"
_FULL = re.compile(rf"^(\d{{4}})-(\d{{1,2}})-(\d{{1,2}})\s*{_T}$")
_MD = re.compile(rf"^(\d{{1,2}})-(\d{{1,2}})\s*{_T}$")
_REL = re.compile(rf"^(今天|明天|后天)\s*{_T}$")
_HM = re.compile(rf"^{_T}$")


def parse_when(text: str, now: dt.datetime) -> dt.datetime | None:
    """把它写的时间认成具体时刻；认不出来（或日期不存在）返回 None，不替它猜。

    「HH:MM」是今天的这个点，已经过了就是明天；其余格式按字面算，过没过由调用方判断。
    """
    s = str(text or "").strip().replace("：", ":").replace("/", "-").replace("T", " ")
    s = re.sub(r"\s+", " ", s)
    try:
        m = _FULL.match(s)
        if m:
            y, mo, d, h, mi = map(int, m.groups())
            return dt.datetime(y, mo, d, h, mi)
        m = _MD.match(s)
        if m:
            mo, d, h, mi = map(int, m.groups())
            return dt.datetime(now.year, mo, d, h, mi)
        m = _REL.match(s)
        if m:
            days = {"今天": 0, "明天": 1, "后天": 2}[m.group(1)]
            day = now.date() + dt.timedelta(days=days)
            return dt.datetime.combine(day, dt.time(int(m.group(2)), int(m.group(3))))
        m = _HM.match(s)
        if m:
            at = dt.datetime.combine(now.date(), dt.time(int(m.group(1)), int(m.group(2))))
            return at if at > now else at + dt.timedelta(days=1)
    except ValueError:  # 比如 2 月 30 日
        return None
    return None


def fmt_at(ts: float) -> str:
    if not ts:
        return "没定时间"
    t = dt.datetime.fromtimestamp(ts)
    return f"{t:%m-%d}（{'一二三四五六日'[t.weekday()]}）{t:%H:%M}"


def add(plans: list, pid: int, what: str, at: float, made: float, progress: list | None = None) -> list:
    p = {"id": int(pid), "at": float(at or 0), "what": what, "made": made}
    if progress:
        p["progress"] = progress
    return plans + [p]


def _id(pid) -> int | None:
    try:
        return int(str(pid).strip().lstrip("#[").rstrip("]"))
    except ValueError:
        return None


def add_progress(plans: list, pid, text: str, t: float) -> tuple[list, dict | None]:
    """给某件事记一笔进度，返回 (新的本子, 那件事)；没有这件事就原样返回和 None。"""
    pid = _id(pid)
    out, hit = [], None
    for p in plans:
        if p["id"] == pid:
            p = {**p, "progress": (p.get("progress") or [])[-(MAX_PROGRESS - 1):] + [{"t": t, "text": text}]}
            hit = p
        out.append(p)
    return out, hit


def progress_text(p: dict, n: int = 5) -> str:
    """最近几笔进度，给它接着往下做的时候看。"""
    notes = (p.get("progress") or [])[-n:]
    if not notes:
        return ""
    more = len(p["progress"]) - len(notes)
    head = f"（更早还有 {more} 笔）\n" if more else ""
    return head + "\n".join(f"[{dt.datetime.fromtimestamp(x['t']):%m-%d %H:%M}] {x['text']}" for x in notes)


def drop(plans: list, pid) -> tuple[list, dict | None]:
    """划掉某一条，返回 (剩下的, 划掉的那条)；没有这一条就原样返回和 None。"""
    pid = _id(pid)
    gone = next((p for p in plans if p["id"] == pid), None)
    return [p for p in plans if p["id"] != pid], gone


def _last(p: dict) -> float:
    """这件事最近一次有动静的时间：定的时间、记下的时间、最近一笔进度，取最晚的。"""
    notes = p.get("progress") or []
    return max(p.get("at") or 0, p.get("made") or 0, notes[-1]["t"] if notes else 0)


def tidy(plans: list, now_ts: float) -> list:
    """清掉放了太久的：STALE_DAYS 天里既没到时间、也没记过进度的。"""
    stale = STALE_DAYS * 86400
    return [p for p in plans if now_ts - (_last(p) or now_ts) < stale]


def due(plans: list, now_ts: float) -> list:
    """定了时间、而且时间到了的（最早的在前）。"""
    return sorted((p for p in plans if p.get("at") and p["at"] <= now_ts), key=lambda p: p["at"])


def listing(plans: list, now_ts: float) -> str:
    """给它自己看的本子：定了时间的按时间排，没定时间的排在后面。"""
    ordered = sorted(plans, key=lambda p: (not p.get("at"), p.get("at") or p.get("made") or 0))
    lines = []
    for p in ordered:
        mark = "（到时间了）" if p.get("at") and p["at"] <= now_ts else ""
        notes = p.get("progress") or []
        prog = (
            f"（记过 {len(notes)} 笔进度，最近一笔 {dt.datetime.fromtimestamp(notes[-1]['t']):%m-%d}："
            f"{notes[-1]['text'][:40]}{'…' if len(notes[-1]['text']) > 40 else ''}）"
            if notes else ""
        )
        lines.append(f"- [{p['id']}] {fmt_at(p.get('at'))}{mark}：{p['what']}{prog}")
    return "\n".join(lines)


def from_reflect(raw, now: dt.datetime, limit: int = 5) -> list[tuple[str, float]]:
    """反思里写下的新安排 → [(要做的事, 时间戳或 0)]。

    时间认不出来、或者已经过了，就当没定时间记下：事情本身不丢，也不替它猜时间。
    """
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            what, when = str(item.get("what") or "").strip(), item.get("at")
        else:
            what, when = str(item or "").strip(), None
        if not what:
            continue
        at = parse_when(when, now) if isinstance(when, str) and when.strip() else None
        out.append((what[:200], at.timestamp() if at and at > now else 0.0))
    return out[:limit]
