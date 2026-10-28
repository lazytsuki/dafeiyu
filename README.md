# 大肥鱼 🐋

用 QQ **小号**养的一条 AI 大肥鱼：会主动私聊你，会发 QQ 空间说说，会在每天的相处里慢慢长成它自己。

**「大肥鱼」是物种，名字是你起的。** 这个仓库是统一的大肥鱼；你养的那一条叫什么、长成了什么样、写过什么日记，都只存在你本机的运行数据里（`runtime/`，已被 .gitignore 排除），和 API Key 一样私有。

## 它怎么长大

它不是一开始就被设定好的。插件里只有一份极简的**内核**（`core.md`）：名字、刚来到这个世界、像真人一样说话、诚实与边界——没有任何性格形容词。

代码里用「TA」指给它起名字的那个人（配置里的 owner），不预设你们是什么关系——关系和称呼都由相处长出来。

| 层 | 是什么 | 谁写 | 谁能「看到」 |
|---|---|---|---|
| 内核 | 名字、诚实、边界、「你会成长」 | 仓库里的 `core.md` | 所有人 |
| 我是谁 `self.md` | 性格、说话的样子、喜欢和在意的东西 | **它自己**，每晚微调 | 所有人（对外展示的它） |
| 我和 TA 之间 `bond.md` | 关系与称呼、TA 的偏好、记得的事、梗、心事 | **它自己**，每晚微调 | 只在和 TA 私聊时想起 |
| 对话流水 | 当天和 TA 的私聊 | 自动记录 | 只用于反思 |

**只有 TA 能塑造它。** 别人加了小号也能和它聊天、问问题，但那只是它以现在的样子对外相处：别人的对话不记录、不参与反思，别人说「你应该是怎样的」它不必接受，也不会提起它和 TA 之间的事。群聊里即使 TA 在场也按对外处理。

别人在一段对话里还是会被带着走一点（这是正常聊天需要的上下文），所以和别人聊时只带最近 10 轮，隔 6 小时没聊就从头开始（`guest_context_turns` / `guest_idle_hours`）——别人的话影响不了它太久，更留不进它的人格。

每晚（默认 03:30）它会回想前一天和 TA 的对话：写一篇自己的日记、小幅改写两份认识（新特点先用「好像」，反复出现才确定；一次最多改 3 处）、留下几句还没说出口的话——说不说、什么时候说、怎么说，由它自己决定。

日记是它自己的，不提供查看指令。唯一的兜底是 `/fy 回滚`：万一被哪天的对话带偏，把自我认识退回上一版。

想给你养的那一条一份不同的内核，就把 `core.md` 复制到 `runtime\astrbot\data\plugin_data\astrbot_plugin_dafeiyu\core.md` 再改——插件会优先用它，它只在本机和你的私库里，仓库里的通用模板不受影响。

电脑关机错过了反思时间也不要紧：开机后它会把漏掉的每一天按顺序补上（最多往回 7 天）。

你想对它说的话可以写进插件配置「你想对它说的话」——它会在反思时读到，但那是期望，不是命令。

## 长期记忆与语音（硅基流动，一个 Key 两用）

1. `scripts\install-plugins.ps1 -AstrBotRoot runtime\astrbot` 装上 LivingMemory。
2. 面板「服务提供商」加两项（api_base 都是 `https://api.siliconflow.cn/v1`，Key 填同一个）：
   - Embedding：`OpenAI Embedding`，ID `siliconflow_embedding`，模型 `BAAI/bge-m3`，维度 1024；
   - 语音转文字：`Whisper(API)`，ID `siliconflow_stt`，模型 `FunAudioLLM/SenseVoiceSmall`，然后在「语音转文字」设置里启用它。
3. LivingMemory 配置：Embedding 填 `siliconflow_embedding`；**记忆白名单打开，只填 TA 的私聊会话**（如 `napcat:FriendMessage:你的QQ号`）——别人的对话不会变成记忆，和「只有 TA 能塑造它」一致；作用域选 `session`；可以打开主动记忆写入工具，想记住什么由它决定。

LivingMemory 记的是事实（「TA 下周面试」），`self.md` / `bond.md` 是它对自己、对你们关系的认识，`recall_past` 翻的是原话——三者各管一层。

## 给它的脚手架

下面这些只是让它「能做到」，不告诉它该是什么样、该怎么用：

- **说到做到**：它用 AstrBot 的「未来任务」答应过的事（「明早 8:40 来找你」），每次聊天时都会想起还有哪些没到时间。电脑关着错过了，开机后会告诉它晚了多久，做不做、怎么说由它决定。（AstrBot 自己对迟到 5 分钟以上的任务会直接丢掉。）
- **上线补回**：关机或 QQ 掉线超过 10 分钟，回来后它会翻一翻和 TA 的私聊，把那段时间 TA 发来、它还没看到的消息拿给它看。只补 TA 的；别人的私聊不补。
- **翻原话**：`recall_past` 工具，在和 TA 的对话流水和它自己的日记里找某件事当时的原话（只在和 TA 私聊时能用）。`bond.md` 只记得重要的事，细节靠这个找回来。
- **心情**：`note_feeling` 工具，它想记的时候记一句此刻的心情。只有它自己看得到，之后 12 小时里会想起（附带「多久以前」），再久就淡了；和 TA 聊天时记下的会在当晚反思时读到。没有预设的情绪种类，也没有数值。
- **空间互动**：到了「想不想发条说说」的时刻，它会先看看自己说说下的新评论、TA 最近三天的说说，再决定回不回、评不评、发不发（每天评论有上限，`qzone_interact` 可关）。TA 的说说和评论会进它的对话流水，别人的评论不会。
- **看得见表情**：小黄脸、戳一戳、骰子、猜拳会翻译成文字；语音没开语音识别时，它会知道「对方发了段语音、自己听不了」，而不是什么都没收到。图片和商城表情由模型自己看（模型要支持图片）。
- **花销预算**：`daily_budget` 设了以后，当天花销估算超了，它自发的事（看空间、主动私聊、逛 B 站微博）先停；回消息、补做答应过的事不受影响。
- **发得出去才算在线**：用 NapCat 的 `get_status` 判断 QQ 是否真的在线（被挤下线时登录信息还拿得到，消息却发不出去）；主动发消息失败会记下来，`/fy 状态` 里能看到。

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
| `plugins/astrbot_plugin_dafeiyu/qzone.py` | QQ 空间：发 / 删说说、读说说和评论、发评论 |
| `plugins/astrbot_plugin_dafeiyu/catchup.py` | 上线补回：错过的未来任务、没看到的消息 |
| `plugins/astrbot_plugin_dafeiyu/recall.py` | 翻原话：在对话流水和日记里找 |
| `plugins/astrbot_plugin_dafeiyu/senses.py` | 把表情、戳一戳、语音翻译成它能看到的文字 |
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
   - `/fy 看空间`（检查读空间是否正常：只报数量，不评论、不标记已读）

## 开机自启

1. 新建 `runtime\local.bat`（本机私有，不进仓库）：
   ```bat
   set "QQ_PATH=D:\你的\QQ.exe"
   set "BOT_QQ=小号QQ号"
   ```
2. `scripts\start-dafeiyu.vbs` 后台无窗口启动（已在运行的会跳过）；想看实时输出就双击 `start-dafeiyu.bat`。`scripts\stop-dafeiyu.bat` 停止（不影响你自己开的 QQ）。
3. 在任务计划程序里建一个「登录时」触发、延迟 30 秒、不限运行时长的任务，执行 `wscript.exe "C:\dafeiyu\scripts\start-dafeiyu.vbs"`。
4. 后台运行时看日志：AstrBot 配置里开 `log_file_enable`（`runtime\astrbot\data\logs\astrbot.log`）；NapCat 配置 `napcat_<小号>.json` 里开 `fileLog`（`runtime\napcat-shell\logs\`）。

**重启后要扫码？** NapCat 的免扫码凭证隔得久了（比如关机过夜）可能失效。可以自己双击 `scripts\set-qq-password.bat`，输入小号密码（不显示，只保存 MD5 到 `runtime\secrets.bat`，不进 git、不进备份）：免扫码失败时 NapCat 会先用它回退登录，二维码作为最后兜底。第一次密码登录可能要在 NapCat 面板（http://127.0.0.1:6099）完成验证码或新设备验证；密码登录也比扫码更容易触发风控，不想用就删掉 `secrets.bat`。

**说说、签名、主动私聊都是它的自由。** 每天有几个「想不想发条说说」的时刻（`qzone_times`），到点时由它决定发不发、写什么，默认不经审核直接发；每晚反思时它也可以决定换不换个性签名；到了固定问候的时刻，它也可以选择不说。唯一的硬限制是防封护栏（每天最多几条说说、主动私聊的频率），那是为了小号不被风控，不是对它的约束。想把关就打开 `qzone_review`。

QQ 没连上时，插件不做任何要通过 QQ 的事（主动私聊、发说说），到点的时间点会保留到连上为止；没送到的说说草稿会在连上后补发。

资源参考（实测）：AstrBot 约 270 MB 内存，NapCat 挂的 QQ 约 470 MB，空闲时 CPU 接近 0；磁盘约 1 GB（AstrBot 环境 500 MB + 运行数据 + 小号 QQ 数据）。

## 备份到私库

你养的那一条（成长数据、日记、名字、配置）不进这个公开仓库，单独备份到你自己的**私有**仓库：

1. 在 GitHub 建一个私有仓库，地址写进 `runtime\local.bat`：`set "BACKUP_REMOTE=https://github.com/<你>/<私库>.git"`
2. `scripts\backup.bat`（或 `python scripts/backup.py`）把插件数据、插件配置、AstrBot 主配置、`local.bat` 同步到 `runtime\backup` 并推送；没有变化就不提交。API Key / 密码 / token 一律打码，QQ 登录态和聊天数据库不备份。
3. 任务计划程序里建一个每天 04:00（在 03:30 反思之后）、勾选「错过后尽快运行」的任务，执行 `wscript.exe "C:\dafeiyu\scripts\run-hidden.vbs" backup.bat`。日志在 `runtime\backup.log`。

恢复：停掉服务，按私库 README 把各目录复制回 `runtime\`，打码的密钥重新填上，再启动。

## 防封建议

- 主动私聊默认每天随机最多 4 次，外加两条固定问候；说说默认每天 2 条且需要你审核，时间随机偏移。别调得太频繁。
- 新号先正常养几天，再开自动发说说。
- 同一个号不要在两台机器上同时挂 NapCat。

## 路线图

- [x] 主动私聊（定时问候 + 随机想你 + 免打扰）、说说草稿审核与发布
- [x] 本机跑通（Windows + uv + NapCat Shell）
- [x] 第一步：极简内核 + 每晚反思（日记、自我认识、心事）
- [x] 说到做到、上线补回、发得出去才算在线
- [x] 翻原话（对话流水 + 日记）、自己记的心情、空间评论互动、花销预算、看得见表情
- [x] 第二步：接长期记忆（LivingMemory 管事实，自我认识管「我是谁」）
- [x] 听得懂语音（硅基流动 SenseVoiceSmall；开了语音转文字，插件就不再提示「听不了」）
- [ ] 第三步：成长反过来影响行为（作息、主动的时机、想发的说说）
- [ ] 主动私聊偶尔发语音、表情包；说说配图
- [ ] 掉线需要扫码时把二维码推到手机
- [ ] 迁移到云服务器
