"""把你养的这条大肥鱼备份到一个私有 git 仓库。

备份：插件数据（成长：自我认识、日记、对话流水、快照、心事）、各插件配置、
      AstrBot 主配置（打码）、runtime/local.bat。
不备份：API Key / 密码 / token（统一打码）、QQ 登录态、AstrBot 聊天数据库（data_v4.db）。
注意：插件自己的数据库照样在 plugin_data 里，比如 livingmemory 的 conversations.db 存着它记下的对话。

插件数据里的 SQLite 数据库不按文件复制（机器人开着时直接复制可能拿到半截的），
而是用 SQLite 自带的在线备份导出成单个 .db，-wal/-shm/-journal 不备份。

私库地址写在 runtime/local.bat：set "BACKUP_REMOTE=https://github.com/<你>/<私库>.git"
用法：python scripts/backup.py        （没有改动就不提交）
"""

import datetime as dt
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
DATA = RUNTIME / "astrbot" / "data"
DEST = RUNTIME / "backup"
SQLITE_HEADER = b"SQLite format 3\x00"
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
SECRET_KEY = re.compile(r"key|token|secret|password|passwd|jwt|cookie|credential", re.I)
README = """# 私有备份

由公开仓库的 `scripts/backup.py` 自动生成，请勿手改。

| 目录 | 内容 | 恢复到 |
|---|---|---|
| `plugin_data/` | 各插件数据（成长数据在 astrbot_plugin_dafeiyu/growth） | `runtime/astrbot/data/plugin_data/` |
| `config/` | 各插件配置（密钥已打码） | `runtime/astrbot/data/config/`（打码处需重新填写） |
| `astrbot/cmd_config.redacted.json` | AstrBot 主配置（密钥已打码，仅供对照） | 按需手动合并 |
| `machine/local.bat` | 本机参数 | `runtime/local.bat` |

这里只放数据。私有扩展的代码放在 `runtime/ext`，用它自己的仓库管理。

恢复前先停掉服务（scripts/stop-dafeiyu.bat），复制回去后再启动。
"""


def local_vars() -> dict:
    p = RUNTIME / "local.bat"
    out = {}
    if p.exists():
        for m in re.finditer(r'set\s+"([A-Z_]+)=([^"]*)"', p.read_text("utf-8", errors="ignore")):
            out[m.group(1)] = m.group(2)
    return out


def _is_secret_value(v) -> bool:
    if isinstance(v, str):
        return bool(v)
    return isinstance(v, list) and any(isinstance(i, str) and i for i in v)  # 比如多个 key 的列表


def redact(obj):
    if isinstance(obj, dict):
        return {
            k: "<redacted>" if SECRET_KEY.search(k) and _is_secret_value(v) else redact(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def copy_json_redacted(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(src.read_text("utf-8-sig"))
    dst.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2), "utf-8")


def sqlite_head(p: Path) -> bytes:
    # 按文件头认 SQLite，不看扩展名；不是 SQLite 就返回空
    try:
        with open(p, "rb") as f:
            head = f.read(100)
    except OSError:
        return b""
    return head if head[:16] == SQLITE_HEADER and len(head) == 100 else b""


def copy_data_file(src, dst):
    head = sqlite_head(Path(src))
    if not head:
        return shutil.copy2(src, dst)
    # 只读打开，用 SQLite 的在线备份拿一份一致的快照（还没合并进主文件的 WAL 内容也会带上）
    uri = f"{Path(src).resolve().as_uri()}?mode=ro"
    if head[18] == 2 and not Path(f"{src}-wal").exists():
        # WAL 库但旁边没有 -wal：现在没人开着它。只读打开也会在原目录留下空的 -wal/-shm，所以按不可变文件读
        uri += "&immutable=1"
    source = sqlite3.connect(uri, uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")  # 副本不用 WAL，就是一个自包含的 .db
    finally:
        target.close()
        source.close()
    return dst


def skip_sidecars(_dir, names):
    return [n for n in names if n.endswith(SQLITE_SIDECARS)]


def git(*args, check=True) -> subprocess.CompletedProcess:
    exe = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
    return subprocess.run([exe, "-C", str(DEST), *args], check=check, capture_output=True, text=True, encoding="utf-8")


def ensure_repo(remote: str):
    DEST.mkdir(parents=True, exist_ok=True)
    if not (DEST / ".git").exists():
        git("init", "-b", "main")
        git("remote", "add", "origin", remote)
        git("pull", "origin", "main", check=False)  # 私库已有内容时先取下来
    else:
        git("remote", "set-url", "origin", remote)


def sync():
    # 镜像式同步：先清掉旧副本（只保留 .git），再整份复制，删掉的文件也会同步删除
    for p in DEST.iterdir():
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (DEST / "README.md").write_text(README, "utf-8")
    if (DATA / "plugin_data").exists():
        shutil.copytree(DATA / "plugin_data", DEST / "plugin_data", ignore=skip_sidecars, copy_function=copy_data_file)
    for cfg in (DATA / "config").glob("*.json"):
        copy_json_redacted(cfg, DEST / "config" / cfg.name)
    if (DATA / "cmd_config.json").exists():
        copy_json_redacted(DATA / "cmd_config.json", DEST / "astrbot" / "cmd_config.redacted.json")
    if (RUNTIME / "local.bat").exists():
        (DEST / "machine").mkdir(exist_ok=True)
        # 万一有人把密码之类写进了 local.bat，也打码（密码本该放 secrets.bat，它不备份）；webhook 地址里带着 key，同样打码
        text = (RUNTIME / "local.bat").read_text("utf-8", errors="ignore")
        text = re.sub(r'(set\s+"?\w*(?:PASSWORD|TOKEN|SECRET|KEY|WEBHOOK)\w*=)[^"\r\n]*', r"\1<redacted>", text, flags=re.I)
        (DEST / "machine" / "local.bat").write_text(text, "utf-8")


def main() -> int:
    remote = local_vars().get("BACKUP_REMOTE", "")
    if not remote:
        print("runtime/local.bat 里没有设置 BACKUP_REMOTE，跳过备份")
        return 1
    ensure_repo(remote)
    sync()
    git("add", "-A")
    if not git("status", "--porcelain").stdout.strip():
        print("没有变化")
        return 0
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    git("-c", "user.name=dafeiyu-backup", "-c", "user.email=backup@localhost", "commit", "-q", "-m", f"备份 {stamp}")
    push = git("push", "-u", "origin", "main", check=False)
    if push.returncode != 0:
        print(push.stderr)
        return push.returncode
    print(f"已备份 {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
