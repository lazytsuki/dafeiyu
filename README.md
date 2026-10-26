# 大肥鱼 🐋

用 QQ **小号**养的一条 AI 大肥鱼。它会主动私聊你，会发 QQ 空间说说，记得你说过的事，还能发语音和表情包。

## 架构

```
你的大号 ⇄ QQ ⇄ NapCat（挂小号） ⇄ AstrBot（框架+面板） ⇄ DeepSeek
                                        │
                                        ├─ astrbot_plugin_dafeiyu（本仓库）：主动私聊、发说说、人设
                                        ├─ LivingMemory（社区）：长期记忆
                                        └─ AstrBot 自带 TTS / 表情包插件：语音、表情
```

只有我们个性化的部分放在 `plugins/astrbot_plugin_dafeiyu/` 自己写。成熟功能用社区插件，版本锁在 `plugins.lock.json` 里。

## 目录

| 路径 | 内容 |
|---|---|
| `plugins/astrbot_plugin_dafeiyu/` | 核心插件（`main.py` 调度和指令，`qzone.py` 说说接口，`persona.md` 人设） |
| `plugins.lock.json` | 社区插件清单 |
| `scripts/` | Windows 安装脚本 |
| `deploy/docker-compose.yml` | 以后上云用 |
| `docs/云服务器方案.md` | 云服务器选型 |

## 本机部署（Windows 过渡方案）

准备：一个 QQ 小号（别用重要的号），一个 [DeepSeek API Key](https://platform.deepseek.com/)，长期记忆另外需要一个 [硅基流动](https://siliconflow.cn/) Key（免费的 bge-m3 嵌入模型）。

1. **装 AstrBot**：下载 [AstrBotLauncher](https://github.com/AstrBotDevs/AstrBotLauncher/releases/latest)，解压到比如 `D:\AstrBot`，双击 bat 安装并启动。浏览器打开 http://localhost:6185 进面板，第一次进去先改掉默认密码。
2. **装 NapCat**：在 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 `NapCat.Shell.Windows.OneKey.zip`，解压后运行 `NapCatInstaller.exe`，再用手机 QQ（小号）扫码登录。
3. **把两者连起来**：
   - AstrBot 面板 → 消息平台 → 添加「OneBot v11 (aiocqhttp)」，反向 WebSocket 端口填 `6199`；
   - NapCat 面板（http://localhost:6099）→ 网络配置 → 新建「WebSocket 客户端」，地址 `ws://127.0.0.1:6199/ws`。
4. **接模型**：AstrBot → 服务提供商 → 添加 DeepSeek（`deepseek-chat`），填 API Key，设为默认。
5. **装大肥鱼插件**：
   ```powershell
   .\scripts\link-plugin.ps1 -AstrBotRoot D:\AstrBot
   ```
   然后在面板里重载插件，打开插件配置，填上「主人的 QQ 号」（你的大号）。
6. **装记忆插件**（需要先装 git）：
   ```powershell
   .\scripts\install-plugins.ps1 -AstrBotRoot D:\AstrBot
   ```
   在 AstrBot 里加一个嵌入模型提供商（硅基流动，`BAAI/bge-m3`），再到 LivingMemory 的配置里选上它。
7. **语音**：服务提供商 → 文字转语音 → 添加 Edge TTS（免费），在配置里开启 TTS。
8. **试一下**：用大号私聊小号随便说一句，让插件记住你的会话。然后发：
   - `/fy 状态`：看看状态
   - `/fy 戳我`：马上触发一次主动私聊
   - `/fy 写说说 今天下雨`：生成一条说说草稿，再用 `/fy 发 1` 发布
   - `/fy 删说说`：删掉刚发的那条

## 防封建议

- 主动私聊默认每天随机最多 4 次，外加早安和晚安两条；说说默认每天 2 条，时间会随机偏移。别调得太频繁。
- 新号先正常养几天：加好友、聊聊天，再开自动发说说。
- 同一个号不要在两台机器上同时挂 NapCat。

## 路线图

- [x] v0.1 主动私聊（定时问候 + 随机想你 + 免打扰）、说说草稿审核与发布、人设
- [ ] 本机实际跑通，按使用体验调参数和提示词
- [ ] 主动私聊偶尔发语音、发表情包
- [ ] 说说配图（AI 生图或本地图库）
- [ ] 迁移到云服务器
