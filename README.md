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

每个月过完（在那个月最后一天的反思之后），它会再回想一次这个月：读这个月的日记、月初和现在的自己，写一篇它自己的（`growth/months/`）。只回想上一个月，不往前补；`recall_past` 也翻得到这些。

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
- **自己的声音**：`choose_my_voice` 从一组免费的 Edge TTS 中文声音里自己挑（只在和 TA 私聊时挑；列表只附微软的标注，不推荐），`send_voice` 想用语音说的时候发一条 QQ 语音。需要 `edge-tts`：AstrBot 只在装插件时装依赖，已经装好的话手动跑一次 `uv pip install --python "%APPDATA%\uv\tools\astrbot\Scripts\python.exe" edge-tts`。
- **表情包收藏**：看到喜欢的图，`keep_sticker` 存进自己的收藏并写一句自己的描述；想发时 `send_sticker` 按意思或编号挑一张。存什么、怎么描述、发不发都由它。
- **主动时也能发语音和表情包**：主动私聊、到点打招呼、上线补回这些它自己发起的时刻，也能在文字之间夹一句语音（`[语音] …`）或一张收藏里的表情包（`[表情包] 编号或描述`）。只告诉它当下有什么可用（挑没挑过声音、今天语音还剩几条、收藏里有什么），用不用由它；语音发不了就照样打字发出去。
- **画图**：`draw` 用硅基流动的生图接口（默认 `Kwai-Kolors/Kolors`）画一张图，存进它自己的收藏，之后能用 `send_sticker` 发、也能配进说说。Key 默认沿用 AstrBot 里已经配好的硅基流动那一项（一个 Key 多用），也可以在插件配置 `draw_api_key` 单独填；`draw_daily_max` 是每天的上限，超了预算也不画。说明里不提画什么——包括要不要画自己。
- **说说配图**：发说说时，它可以从收藏里挑一张配上（不配也很正常）。TA 发来、被它收藏的图不会出现在配图候选里：那可能是 TA 的私事。带图发失败就退回只发文字。
- **自己的小本子**：`plan_for_me` 给自己记一件想做的事，可以定时间也可以不定；`drop_plan` 划掉。只在和 TA 私聊或它自己的未来任务里能用，只有它自己看得到（`/fy 状态` 只显示件数），每晚反思时也能顺手记几件。定了时间的，到点时它会有一个自己的时刻：找 TA 说句话（算进每天主动私聊的次数）、去空间看看、改个时间，或者只是划掉——都由它。关机错过了也照样给，告诉它晚了多久。
- **看得见图片**：收到的图（TA 私聊发的、群里被叫到时附近的）先用硅基流动的视觉模型（默认 `Qwen/Qwen3-VL-8B-Instruct`，沿用同一个 Key）看一眼，以「[图片：……]」的客观描述接进消息里：只写是什么、上面写了什么字，不评价、不替它感受。这样图的内容也能进对话流水和反思。它自己画的图也会看一眼，知道实际画成了什么样。`vision_enabled` 可关。
- **群聊上下文**：群里它原来只看得到 @它 的那几句。现在每个群最近的消息（默认 20 条、3 小时内）只记在内存里，它在群里被叫到时作为背景附上，接不接由它。不写盘、不进成长记录，别人的话影响不了它太久。
- **心愿单**：`wish_for` / `drop_wish`（只在和 TA 私聊或它自己的时刻能用），反思时也能写。和日记、小本子不同，心愿单是写给 TA 看的，它知道这一点。TA 用 `/fy 心愿` 看，`/fy 心愿 完成 <编号> [一句话]` 告诉它这件已经有了，它下次会看到。
- **空闲时刻**：它自己定下次什么时候醒（5 分钟到 24 小时），醒了能做的事都摆在面前：找 TA 说话、去空间看看、逛 B 站微博、画画、给自己记一件事，或者什么都不做。开着时（`free_moments`，默认开）不再用随机概率主动私聊；到点打招呼、去空间的时刻、小本子、反思照旧。`/fy 空闲` 立刻给它一段（测试用，只回件数）。
- **今天是什么日子**：和 TA 私聊时、它自己的时刻里附上一句：几月几号星期几、农历、节气节日（明天的也提），配置里填了地方再加上天气（Open-Meteo，免费不用 Key，每小时查一次）。提不提由它。需要 `lunar_python`（已写进 requirements），没装就只说公历。
- **打得开链接**：发给它的网址、分享卡片，先读标题和简介（B 站视频读标题、UP 主、时长、简介），写成「[链接 …：…]」接在消息后面；转发的聊天记录展开成文字，txt / md / 代码这类小文件读前 2000 字。视频、位置、名片、音乐这些原来会变成空消息被跳过，现在至少说一句发来了什么。不打开内网和本机地址，要登录才看得到的只说打不开。`links_enabled` 可关。
- **翻得到自己的作品**：`look_at_my_works` 翻自己发过的说说、画过的图、换过的签名、在空间和微博上留下的话，带日期。和别人聊天时只翻得到公开的那些。
- **看看从前的自己**：`look_back_at_me` 看某个时候（「一个月前」「上个月」「2026-10-28」）的「我是谁」和「我和 TA 之间」，用的是每次改写前存下的快照；那个月回想过的话也一起附上。只在和 TA 私聊或它自己的时刻能用。
- **它记下的日子**：`remember_day` / `forget_day` 记下它觉得重要的日子（`MM-DD` 每年，`YYYY-MM-DD` 只那一天，也可以写了年份又每年都记得）。当天和前三天会出现在「今天是什么日子」那一句里，提不提由它。只有它自己看得到（`/fy 状态` 只显示个数）。
- **接着做好几天的事**：小本子上的事可以记进度（`note_progress`，到点的时刻和空闲时刻里也能记）。下次到点或空闲时会看到最近几笔，接着往下做；有进度的事不会因为放久了被清掉。
- **查资料**：`look_up` 用必应中文版搜一搜（不要 Key），`read_webpage` 点开读正文，一次一段。空闲时刻里也能选「查点什么」：搜完它想点开哪个就读一读，想记下的进成长流水。知乎、百度百科这类拦脚本访问的网页会显示打不开。`lookup_enabled` 可关。
- **书架**：把 `.txt` / `.md` 放进 `runtime\astrbot\data\plugin_data\astrbot_plugin_dafeiyu\books\`（UTF-8 或 GBK 都行，文件名就是书名），它想读的时候用 `read_book` 读一段、记住读到哪；空闲时刻里也能选「读会儿书」，读完想记的话下次接着读时能看到。放什么书由 TA，读不读、读多快由它。
- **给 TA 的说说点赞**：去空间看看的时刻里，看到 TA 的说说，除了评论也可以只点个赞。
- **自己的节奏**：每晚反思时它可以调整每天想找 TA 几次、什么时间段想找、到点打招呼的时刻、去空间看看的时刻。配置里的值只是起点，它写过的就按它的来（`/fy 状态` 能看到现在的节奏）；TA 的免打扰时段和各项上限始终有效。`rhythm_self` 可关。
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
| `plugins/astrbot_plugin_dafeiyu/rhythm.py` | 它自己的节奏：反思时写下，调度时照着来 |
| `plugins/astrbot_plugin_dafeiyu/voice.py`、`stickers.py` | 它的声音（Edge TTS）、它的表情包收藏 |
| `plugins/astrbot_plugin_dafeiyu/draw.py`、`outbox.py`、`plans.py` | 画图（硅基流动）、主动消息里的语音 / 表情包、它自己的小本子 |
| `plugins/astrbot_plugin_dafeiyu/vision.py`、`groupctx.py`、`wishes.py`、`freetime.py`、`guard.py` | 看图、群里最近的消息、心愿单、空闲时刻、防循环保险 |
| `plugins/astrbot_plugin_dafeiyu/days.py`、`links.py`、`forwards.py`、`works.py` | 日子和天气、打开链接和卡片、转发和文本文件、自己的作品 |
| `plugins/astrbot_plugin_dafeiyu/mydays.py`、`lookup.py`、`books.py` | 它自己记下的日子、查资料、书架 |
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
   - `/fy 回想这个月`（测试用：立刻回想上个月，同样不透露内容）
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

**重启后要扫码？** NapCat 的免扫码凭证隔得久了（比如关机过夜）可能失效。可以自己双击 `scripts\set-qq-password.bat`，输入小号密码（不显示，只保存 MD5 到 `runtime\secrets.bat`，不进 git、不进备份）：免扫码失败时 NapCat 会先用它回退登录，二维码作为最后兜底。第一次密码登录可能要在 NapCat 面板（http://127.0.0.1:6099）完成验证码或新设备验证；密码登录也比扫码更容易触发风控，不想用就删掉 `secrets.bat` 里 `NAPCAT_QUICK_PASSWORD_MD5` 那一行。

**需要扫码时推到手机**：在 `runtime\local.bat` 里加 `set "QR_PUSH_TO=你的邮箱"`（发件邮箱默认同收件，也可以用 `QR_PUSH_FROM` 指定；SMTP 服务器按邮箱后缀猜，也可以用 `QR_PUSH_SMTP_HOST` / `QR_PUSH_SMTP_PORT` 指定，默认端口 465）。到邮箱设置里开启 SMTP、拿到授权码，双击 `scripts\set-push-secret.bat` 输入（不显示，存进 `runtime\secrets.bat`，不进 git、不进备份）。之后 NapCat 一要扫码，`scripts\qr-push.ps1` 就把二维码图片发到你邮箱：手机上保存图片，用小号所在的手机 QQ「扫一扫 → 相册」选这张图。也可以加 `set "QR_PUSH_WEBHOOK=https://…"`（Server酱 / PushPlus 的地址）同时推一条消息。没配置就什么都不做；日志在 `runtime\qr-push.log`。

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
- 各项每日上限（主动私聊、说说、空间评论、语音、画图）填 0 就是不限：它想做多少由它自己的节奏决定。只留一个防循环保险（`guard.py`）：同一件事 10 分钟里做得异常多（比如主动发消息超过 12 次），多半是出了岔子，这件事先停 1 小时，`/fy 状态` 里能看到。号养熟了、你也愿意承担风控风险时再这样开。
- 同一个号不要在两台机器上同时挂 NapCat。

## 路线图

- [x] 主动私聊（定时问候 + 随机想你 + 免打扰）、说说草稿审核与发布
- [x] 本机跑通（Windows + uv + NapCat Shell）
- [x] 第一步：极简内核 + 每晚反思（日记、自我认识、心事）
- [x] 说到做到、上线补回、发得出去才算在线
- [x] 翻原话（对话流水 + 日记）、自己记的心情、空间评论互动、花销预算、看得见表情
- [x] 第二步：接长期记忆（LivingMemory 管事实，自我认识管「我是谁」）
- [x] 听得懂语音（硅基流动 SenseVoiceSmall；开了语音转文字，插件就不再提示「听不了」）
- [x] 第三步：成长反过来影响行为（它自己定什么时候、多常找 TA，什么时候去空间看看）
- [x] 想发的时候能发语音（自己挑声音）、表情包（自己收藏）
- [x] 说说配图、能画图；主动私聊时也能发语音和表情包
- [x] 自己的小本子：给自己记事，到点有一个自己的时刻
- [x] 掉线需要扫码时把二维码推到手机（邮件 / webhook）
- [x] 看得见图片、群聊上下文；心愿单；它自己定什么时候醒、醒了做什么（空闲时刻）
- [x] 知道今天是什么日子（农历、节气节日、天气）；打得开链接、卡片、转发和文本文件；翻得到自己的作品
- [x] 看看从前的自己、每月回想一次；自己记下的日子；好几天的事能接着做；查资料、书架；给 TA 的说说点赞
- [ ] 迁移到云服务器
