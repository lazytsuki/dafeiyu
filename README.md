# 大肥鱼 🐋

用 QQ **小号**养的一条 AI 大肥鱼：会主动私聊你，会发 QQ 空间说说，会在每天的相处里慢慢长成它自己。

**「大肥鱼」是物种，名字是你起的。** 这个仓库是统一的大肥鱼；你养的那一条叫什么、长成了什么样、写过什么日记，都只存在你本机的运行数据里（`runtime/`，已被 .gitignore 排除），和 API Key 一样私有。

## 它怎么长大

它不是一开始就被设定好的。插件里只有一份极简的**内核**（`core.md`）：名字、刚来到这个世界、像真人一样说话、诚实与边界——没有任何性格形容词。

| 层 | 是什么 | 谁写 | 多久变 |
|---|---|---|---|
| 内核 | 名字、诚实、边界、「你会成长」 | 仓库里的 `core.md` | 几乎不变 |
| 自我认识 | 性格、说话的样子、在意的事、和主人的关系、梗 | **它自己** | 每晚反思时微调 |
| 对话流水 | 当天和主人的聊天 | 自动记录 | 每天 |

每晚（默认 03:30）它会回想前一天：写一篇自己的日记、小幅改写对自己的认识（新特点先用「好像」，反复出现才确定；一次最多改 3 处）、留下几句还没说出口的话——说不说、什么时候说、怎么说，由它自己决定。

日记是它自己的，不提供查看指令。唯一的兜底是 `/fy 回滚`：万一被哪天的对话带偏，把自我认识退回上一版。

你想对它说的话可以写进插件配置「你想对它说的话」——它会在反思时读到，但那是期望，不是命令。

## 架构

```
你的大号 ⇄ QQ ⇄ NapCat（挂小号） ⇄ AstrBot（框架+面板） ⇄ DeepSeek
                                        │
                                        └─ astrbot_plugin_dafeiyu（本仓库）：内核、成长、主动私聊、说说
```

| 路径 | 内容 |
|---|---|
| `plugins/astrbot_plugin_dafeiyu/main.py` | 调度、钩子、指令 |
| `plugins/astrbot_plugin_dafeiyu/core.md` | 内核（`{name}` 会换成你起的名字） |
| `plugins/astrbot_plugin_dafeiyu/growth.py` | 自我认识、日记、对话流水、快照 |
| `plugins/astrbot_plugin_dafeiyu/qzone.py` | QQ 空间说说接口 |
| `plugins.lock.json` | 社区插件清单（长期记忆等，路线图第二步再接） |
| `scripts/` | Windows 安装/启动脚本 |
| `deploy/docker-compose.yml`、`docs/云服务器方案.md` | 以后上云用 |

## 本机部署（Windows）

准备：一个 QQ 小号（别用重要的号）、电脑上装好 QQ、一个 [DeepSeek API Key](https://platform.deepseek.com/)。

1. **装 AstrBot**（uv 会自带 Python）：
   ```powershell
   winget install astral-sh.uv
   uv tool install astrbot --python 3.12
   ```
   在 `runtime\astrbot` 里初始化并启动（不用 `astrbot.exe`，它在部分环境会报 `failed to canonicalize script path`）：
   ```powershell
   cd runtime\astrbot
   & "$env:APPDATA\uv\tools\astrbot\Scripts\python.exe" -m astrbot.cli init -y
   & "$env:APPDATA\uv\tools\astrbot\Scripts\python.exe" -m astrbot.cli run
   ```
   首次启动会打印面板初始密码，打开 http://localhost:6185 登录后改掉。
2. **链接插件**：在 `runtime\astrbot\data\plugins` 下建一个指向 `plugins\astrbot_plugin_dafeiyu` 的目录联接（`scripts\link-plugin.ps1`；系统禁止运行 .ps1 的话手动 `New-Item -ItemType Junction`）。
3. **装 NapCat**：从 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 `NapCat.Shell.zip` 解压到 `runtime\napcat-shell`，然后：
   ```powershell
   scripts\start-napcat.bat D:\你的\QQ.exe
   ```
   用小号手机 QQ 扫码。登录过一次后可以免扫码：`scripts\start-napcat.bat D:\你的\QQ.exe 小号QQ号`。
4. **把两者连起来**（都只监听本机）：
   - AstrBot `data/cmd_config.json` 的 `platform` 加一个 `aiocqhttp`，`ws_reverse_host` 填 `127.0.0.1`、端口 `6199`、设一个 token；
   - NapCat `config/onebot11_<小号>.json` 的 `websocketClients` 加 `ws://127.0.0.1:6199/ws`，token 相同。
5. **接模型**：面板 → 模型提供商 → DeepSeek，填 Key。
6. **插件配置**：填「主人的 QQ 号」（你的大号）和「你给它起的名字」，把大号加进 AstrBot 管理员。
7. **试一下**：大号私聊小号说句话，然后：
   - `/fy 状态`、`/fy 戳我`（马上主动找你一次）
   - `/fy 写说说 [主题]` → `/fy 发 1` 或 `/fy 弃 1`
   - `/fy 反思`（测试用：立刻反思今天，只回一句，不透露内容）

## 防封建议

- 主动私聊默认每天随机最多 4 次，外加两条固定问候；说说默认每天 2 条且需要你审核，时间随机偏移。别调得太频繁。
- 新号先正常养几天，再开自动发说说。
- 同一个号不要在两台机器上同时挂 NapCat。

## 路线图

- [x] 主动私聊（定时问候 + 随机想你 + 免打扰）、说说草稿审核与发布
- [x] 本机跑通（Windows + uv + NapCat Shell）
- [x] 第一步：极简内核 + 每晚反思（日记、自我认识、心事）
- [ ] 第二步：接长期记忆（LivingMemory 管事实，自我认识管「我是谁」）
- [ ] 第三步：成长反过来影响行为（作息、主动的时机、想发的说说）
- [ ] 主动私聊偶尔发语音、表情包；说说配图
- [ ] 迁移到云服务器
