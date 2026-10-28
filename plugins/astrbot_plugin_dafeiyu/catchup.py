"""上线补回：关机或掉线回来以后，找出它错过的事。

两类：
- 它自己定下、但因为不在线没能按时做的事（AstrBot 的「未来任务」迟到 5 分钟以上就被调度器直接丢掉，
  数据库里却一直显示 scheduled）；
- 它不在线时 TA 发来、它还没看到的私聊。
找出来以后怎么办——补不补、怎么说——都交给它自己。
"""

import datetime as dt

LATE_SECONDS = 360  # 调度器宽限 5 分钟，再多给 1 分钟余量


def job_run_at(job) -> dt.datetime | None:
    """一次性任务原定的时间（带时区）；不是一次性任务或没有时间就返回 None。"""
    payload = getattr(job, "payload", None)
    if not getattr(job, "run_once", False) or not isinstance(payload, dict) or not payload.get("run_at"):
        return None
    try:
        at = dt.datetime.fromisoformat(str(payload["run_at"]))
    except ValueError:
        return None
    return at if at.tzinfo else at.astimezone()  # 没带时区就当本机时间


def overdue_jobs(jobs, now: dt.datetime, handled: set[str]) -> list:
    """该做却没做的一次性任务：启用着、从没跑过、原定时间已经过了宽限期。"""
    out = []
    for job in jobs:
        at = job_run_at(job)
        if at is None or not job.enabled or job.last_run_at is not None or job.job_id in handled:
            continue
        if getattr(job, "status", "") == "running":
            continue
        if (now - at).total_seconds() > LATE_SECONDS:
            out.append(job)
    return sorted(out, key=job_run_at)


def unanswered(messages: list, owner_id: str, bot_id: str, since: float) -> list[dict]:
    """私聊记录里 since 之后 TA 发来、而它之后还没说过话的消息（从早到晚）。"""
    msgs = sorted((m for m in messages if isinstance(m, dict)), key=lambda m: m.get("time", 0))
    last_bot = max(
        (m.get("time", 0) for m in msgs if str((m.get("sender") or {}).get("user_id")) == str(bot_id)),
        default=0,
    )
    return [
        m
        for m in msgs
        if str((m.get("sender") or {}).get("user_id")) == str(owner_id)
        and m.get("time", 0) > max(since, last_bot)
    ]


def fmt_gap(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} 分钟"
    if minutes < 48 * 60:
        return f"{minutes // 60} 小时" + (f" {minutes % 60} 分钟" if minutes % 60 else "")
    return f"{minutes // 1440} 天"
