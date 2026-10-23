# QAI（QQ 群机器人 · NapCat + OneBot v11 + NoneBot2）

QAI 是一个本地运行的 QQ 机器人管理端：收发消息、@、表情、图片、引用回复、撤回、
群成员查询、禁言、入群欢迎、戳一戳回应和 AI 对话，都可以在桌面控制台里配置。

> **源码运行**：先按下方教程安装依赖，复制 `.env.example` 为 `.env`，用 `python bot.py`
> 启动机器人，用 `python launcher.py` 打开控制中心。

> **便携版**：GitHub Releases 提供打包好的 zip（含 Python 运行时和 WebView2 窗口依赖）。解压后双击
> `QAI.exe`，按首次配置向导填 API Key、选 NapCat 目录即可，不需要单独装 Python。
>
> **界面**：四套主题（幽静蓝 / 炫酷紫 / 清爽白 / 沉稳灰）、自定义背景色、卡片边缘拖拽缩放、
> 鼠标跟随光晕、卡片点击抖动开关、界面布局（拖动+边缘缩放）、中文常用 .env 参数快捷编辑、渐变扫光、页面切换和滚动视差。
>
> **模型（固定列表）**：GLM-4.7-Flash（免费）、GLM-5.3-Flash、GLM-5.3、DeepSeek-V4.1-Flash、
> DeepSeek-V4-Pro、qwen3.8-flash、kimi-k3、Hy4 preview、Hy3。模型选择界面带
> 识图 / 视频 / 原生联网 / 工具 / 推理 / 免费能力徽标，并按厂商分别保存 API Key。
>
> **NapCat**：首次配置可直接下载安装到 `QAI\napcat`（约 120MB），也可选已有目录；QQNT、UAC 和扫码登录仍需用户完成。

> **文档状态**：这份是搭建 / 接线教程；更短的首次使用流程见
> [`新手教程.md`](新手教程.md)。

---

## 一、你需要准备的东西

| 项目                                          | 说明                                                  |
| ------------------------------------------- | --------------------------------------------------- |
| 一台装了 **QQ 桌面端（QQNT ≥ 9.9.27）** 的 Windows 电脑 | NapCat 是「挂」在 QQ 客户端上工作的                             |
| 一个**专门给机器人用的 QQ 小号**                        | **不要使用主号**，存在风控风险                              |
| Node.js 18+ 或 Docker（可选）                    | 用 `NapCat.Shell.Windows.Node.zip` 这个包的话自带 Node，不用另装 |
| Python 3.10+                                | 跑机器人本体                                              |
| 一个大模型 API Key（可选）                           | 不配也能跑，只有 AI 对话用不了                                   |

---

## 二、第一步：装 NapCat 并登录

1. 打开 NapCat 的 GitHub Releases 页面：  
   `https://github.com/NapNeko/NapCatQQ/releases`
2. 下载 **`NapCat.Shell.Windows.Node.zip`**（约几十 MB，自带 Node 运行时）。
   > 不要下载名字带 `OneKey` 的包，它启动时会联网下载 QQ，内置链接经常失效报 404。
3. 解压到一个**纯英文、无空格**的目录，例如 `D:\napcat\`。
4. **右键 → 以管理员身份运行** C（Win10/Win11 用这个；  
   其他系统用 `launcher.bat`）。需要管理员权限是因为脚本要读注册表找 QQ 的安装路径。
5. 弹出 QQ 登录窗口 → 用你的**机器人小号扫码登录**
6. 登录成功后浏览器打开：`http://127.0.0.1:6099/webui`
   - 首次登录需要的 Token 会打印在**刚才那个黑窗口的日志里**（也有的版本默认是 `napcat`）
   - 进去后先把默认 Token 改掉
7. **NapCat 的窗口不能关**，关了机器人就掉线。

> 登录不上怎么办：新注册的 QQ 号、长期不用的号大概率被风控拦。  
> 先在手机 QQ 上正常聊几天，再回来扫码。

---

## 三、第二步：配置 NapCat 连到机器人

在 WebUI 里：**网络配置 → 新建 WebSocket 客户端（反向 WebSocket）**，填下面这些：

| 配置项    | 值                                                             |
| ------ | ------------------------------------------------------------- |
| 名称     | `NoneBot2`（随便起）                                               |
| URL    | `ws://127.0.0.1:8080/onebot/v11/ws`                           |
| 消息格式   | **Array**                                                     |
| Token  | 自己设置一个值，要和 `.env` 里的 `ONEBOT_ACCESS_TOKEN` 一致 |
| 心跳间隔   | 30000                                                         |
| 重连间隔   | 5000                                                          |
| 上报自身消息 | 关闭                                                            |

也可以直接导入项目里的 `napcat_reverse_ws.example.json`（复制其中的 `network` 块）。

**Token 两边必须完全一致，不一致会一直连不上。**

---

## 四、第三步：启动机器人

在本项目目录下打开终端：

```bat
run.bat
```

或者手动执行：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

看到这几行就说明启动成功：

```
[SUCCESS] nonebot | Succeeded to load plugin "basic" ...
[SUCCESS] nonebot | Succeeded to load plugin "ai_chat" ...
[INFO] uvicorn | Uvicorn running on http://127.0.0.1:8080
```

然后 NapCat 那边会自动连上来，机器人日志会多一行：

```
[SUCCESS] nonebot | OneBot V11 123456 | Bot connected
```

---

## 五、第四步：不登录 QQ 也能自检（强烈建议先做）

项目里带了两个测试工具，**不用真的发 QQ 消息**就能验证链路。

### 1. 链路自检

保持机器人运行，另开一个终端：

```bat
python tools/fake_client.py
```

它会伪装成一个协议端连上来，发 `/ping`、`/help`、`/me`、以及一条 `@机器人 你好`，  
然后打印机器人实际调用了哪些接口。看到 `[✓] 链路自检通过` 就说明收发全通。

### 2. 用假模型跑通 AI 对话（不花一分钱）

```bat
:: 终端 A：启动假模型
python tools/mock_llm.py

:: 终端 B：把机器人指向假模型
set LLM_API_KEY=mock
set LLM_BASE_URL=http://127.0.0.1:9099/v1
python bot.py
```

再跑 `python tools/fake_client.py`，就能看到机器人真的调了模型、真的回了话。  
确认没问题后，再把 `.env` 里的 `LLM_API_KEY` 换成真实的 Key 重启即可。

---

## 六、把机器人加进群

把机器人小号拉进目标群，然后在群里直接发：

```
/help
```

就能看到全部指令。**首次体验建议先做两件事：**

1. 在 `.env` 里把 `ALLOWED_GROUPS` 填成你的测试群号（多个用逗号分隔），别让它在大群里乱回。
2. 在 `.env` 里把 `BOT_ADMIN_QQ` 填成你自己的 QQ 号，这样才能用 `/ban`、`/groups` 等管理指令。

改完 `.env` **必须重启机器人**才生效。

---

## 七、指令一览

| 指令                | 作用                      | 演示的 OneBot 能力              |
| ----------------- | ----------------------- | -------------------------- |
| `/help`           | 指令菜单                    | —                          |
| `/ping`           | 存活 + 消息往返延迟             | 基础收发                       |
| `/echo <文本>`      | 原样回显                    | 文本消息                       |
| `/me`             | 查你的群名片、角色、入群时间          | `get_group_member_info`    |
| `/at <QQ> [文本]`   | @ 某人                    | `MessageSegment.at`        |
| `/face`           | 随机 QQ 表情                | `MessageSegment.face`      |
| `/img <URL 或文件名>` | 发图片（本地图放 `assets/`）     | `MessageSegment.image`     |
| `/reply <文本>`     | 引用你的消息回复                | `MessageSegment.reply`     |
| `/recall`         | 撤回机器人上一条消息；回复某条消息再发则撤那条 | `delete_msg`               |
| `/members`        | 群成员列表（前 15 人）           | `get_group_member_list`    |
| `/groups`         | 机器人所在群列表（仅管理员）          | `get_group_list`           |
| `/ban @某人 10`     | 禁言 10 分钟（上限 60）         | `set_group_ban`            |
| `/unban @某人`      | 解除禁言                    | `set_group_ban`            |
| `/status`         | 运行时长、收发计数、配置是否生效        | —                          |
| `/aireset`        | 清空本群对话记忆                | —                          |
| `/人格 [名字]`      | 列出 / 切换人设（保留对话记忆） | —                          |
| `/接话 开\|关`      | 自主接话开关                    | —                          |
| `/好感`            | 自己 + 本群好感度                | —                          |
| `/记忆`            | 查看 / 删除本场景长期记忆            | —                          |
| `/清理记忆`          | 立即清理过期对话 / 日志             | —                          |
| `/心情`（`/mood`）   | 当前心情                       | —                          |
| `/表情包`           | 随机发一个收藏表情包               | `fetch_custom_face`        |
| `/建表情库`          | 拉收藏 → 下载 → 视觉自动打标签        | `fetch_custom_face`        |
| 群里 **@机器人 + 内容**  | AI 对话                   | 大模型调用                      |
| **私聊直接说话**        | AI 对话                   | 大模型调用                      |
| 戳一戳机器人            | 自动回应                    | `notice` 事件                |
| 有人进群              | 自动欢迎                    | `GroupIncreaseNoticeEvent` |

---

## 八、目录结构

```
QAI/
├── bot.py                 入口：起反向 WebSocket 服务，等 NapCat 连上来
├── .env.example           配置模板（复制为 .env 后填写）
├── requirements.txt
├── run.bat                源码方式启动机器人
├── 启动QAI.bat            启动桌面控制中心
├── 启动QAI.vbs            无黑窗口启动控制中心
├── 停止QAI.bat            结束控制中心
├── launcher.py            桌面控制中心后端（进程/状态/NapCat/首次配置/8082）
├── qai_window.py          pywebview 原生窗口入口
├── launcher_ui/           桌面控制中心外壳（QAI 封面/状态栏 + 内嵌 8081 控制台）
├── launcher_ui/models.js  多厂商/模型目录与能力徽标
├── launcher_ui/theme.css  四套主题变量 + 动效
├── launcher_ui/theme.js   主题切换 / 鼠标跟随 / 卡片抖动 / iframe 同步
├── webpanel_ui/theme.css  管理台主题（与 launcher 同一份）
├── webpanel_ui/theme.js   管理台主题/动效（与 launcher 同一份）
├── napcat_reverse_ws.example.json   NapCat 网络配置模板
├── README.md / 新手教程.md
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── personas/              ★ 人设（每套 persona.txt + config.json）
│   └── example/           原创示例人设
├── assets/                表情库目录（运行时创建，用户数据不入库）
├── src/
│   ├── config.py          读 .env 的小工具
│   ├── stats.py           运行统计
│   ├── memory.py          记忆 / 心情 / 好感度 / 压缩 / 日志 / TTL 清理
│   ├── longterm.py        长期记忆（偏好 / 约定 / 重要经历）
│   ├── maintenance.py     自动清理调度（对话 / 长期记忆 / 日志）
│   ├── personas.py        多套人设 + 按群绑定 + 参数解析
│   ├── image_search.py    搜图
│   ├── sticker_lib.py     本地表情库
│   ├── web_tools.py       DeepSeek Search / Fetch
│   └── plugins/
│       ├── basic.py       基础指令 + 事件监听
│       ├── ai_chat.py     AI 对话核心（上下文、冷却、工具、拟人分句）
│       ├── webpanel.py    Web 控制台后端（静态文件 + JSON API）
│       ├── msglog.py      收消息诊断日志
│       └── lifecycle.py   掉线回归播报
├── webpanel_ui/           控制台前端（index.html / app.css / app.js）
└── tools/
    ├── fake_client.py     链路自检（不需要真 QQ）
    ├── mock_llm.py        本地假模型（不需要真 API Key）
    └── check_llm.py       模型连通性诊断
```

---

## 九、常见问题排查

| 现象                                                                  | 原因 / 解决                                                          |
| ------------------------------------------------------------------- | ---------------------------------------------------------------- |
| NapCat 日志里一直重连失败                                                    | Token 与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 不一致；或机器人没启动               |
| 机器人收不到消息                                                            | NapCat 没登录；或群不在 `ALLOWED_GROUPS` 里（非空时会拦住其他群）                    |
| 指令没反应                                                               | 命令前缀必须是 `/` 或 `!`；`.env` 里 `COMMAND_START` 被改过                   |
| `/ban` 提示失败                                                         | 机器人自己也必须是管理员，且不能禁管理员                                             |
| `/recall` 失败                                                        | 只能撤 2 分钟内的消息，且机器人要是管理员                                           |
| AI 不回                                                               | 检查 `.env` 的 `LLM_API_KEY` 是否填了、`LLM_BASE_URL` 结尾不要多带 `/v1` 之外的路径 |
| 端口 8080 被占用                                                         | 改 `.env` 里的 `PORT`，同时改 NapCat 里 URL 的端口                          |
| 本机开了代理导致连不上                                                         | 给终端设置 `NO_PROXY=127.0.0.1,localhost`                             |
| 启动 NapCat 弹窗 `Cannot find module '...\resources\app\loadNapCat.js'` | 见下方「附：NapCat 注入失败修复」                                             |
| **改了 `.env` 或改了代码，重启后却没生效**    | 旧进程没退、占着 8080，新起的实例一启动就失败（你会以为"重启过了"）。**必须先彻底关掉旧黑窗口再启动**。确认：`netstat -ano \| findstr :8080` 看谁在监听，再用 `taskkill /F /PID <pid>` 杀掉 |
| 机器人看不懂表情包 / 引用 / 图片              | 看 `logs/inbound.log`，确认 NapCat 实际发来的消息段是什么类型                                             |

---

## 附：NapCat 注入失败修复（`Cannot find module ... loadNapCat.js`）

**现象**：以管理员运行 `launcher-win10.bat` 后弹出 JS 错误框：

```
Error: Cannot find module 'D:\QQ\Versions\9.9.31-49738\resources\app\loadNapCat.js'
```

**原因**：这个报错说明 **Hook 已经生效了**（所以 QQ 才去找 `loadNapCat.js`），  
但 `loadNapCat.js` 没被放进 QQ 的 app 目录。

用**网页安装器**装的 QQ（`Versions\config.json` 里 `"channel": "QQweb"`）目录结构是：

```
D:\QQ\QQ.exe
D:\QQ\Versions\<版本号>\resources\app\      ← 真正的 app 目录
```

而**标准安装**才是：

```
D:\QQ\resources\app\                        ← 这一层在你机器上不存在
```

NapCat 的注入器按标准结构去找，自然找不到 → 静默失败。（已实测：目录本身可写，不是权限问题。）

**修复**：把 `loadNapCat.js` 手动放到真正的 app 目录。

1. 复制 NapCat 目录下的 `loadNapCat.js` 到 `D:\QQ\Versions\<版本号>\resources\app\`
2. 建议把它改成**自包含**版本，不依赖环境变量：

```js
const { pathToFileURL } = require('url');
const DEFAULT_MAIN = 'E:/ChatBot/napcat/napcat.mjs';   // 改成你的 napcat.mjs 路径
(async () => {
  const mainPath = process.env.NAPCAT_MAIN_PATH || DEFAULT_MAIN;
  await import(pathToFileURL(mainPath).href);
})();
```

1. 再以管理员运行 `launcher-win10.bat` 即可。

> 项目里的 `reinject.bat`（放在 NapCat 目录下）会自动定位 `Versions\*\resources\app`  
> 并重新注入。**每次 QQ 自动更新后版本号目录会变，需要重新跑一次。**

**注意**：不要改 `package.json` 的 `main` 字段。保持原样才不影响你日常正常启动 QQ；  
Hook 只在通过 `launcher-win10.bat` 启动时才接管入口。

**另外两点提醒**：

- 如果 QQ 正在运行，先完全退出再运行 launcher，否则注入会打架。
- 如果复制完文件「立刻消失」，是杀毒软件（360/火绒/电脑管家）在删它，  
  把 QQ 安装目录和 NapCat 目录加进白名单即可。

---

## 十、防风控注意事项（社区路线必读）

走的是协议端方案，本质是让程序代替你登录 QQ，**违反 QQ 用户协议，有冻结风险**。降低风险的几条硬规则：

1. **只用闲置小号**，绝不用主号；小号实名、绑盾、有正常聊天记录。
2. **只做被动应答**，不要把机器人拿去群发、批量加好友、批量拉群、转发名片——这些是秒封行为。
3. **控制频率**：`.env` 里的 `AI_COOLDOWN_SEC`（默认 5 秒）不要调太小；  
   `AI_HUMANIZE=true` 会模拟真人分句发送，别关掉。
4. **群白名单**一定要填，只在自己的小群里跑。
5. 别频繁换机器、换 IP 登录同一个号；NapCat 的登录态文件做好备份，别每次都重新扫码。
6. 发现「发消息失败率突然升高」或要求短信验证，立刻停掉，等几天再试，不要硬扛重试。

> 另外：让 AI 冒充真人长期在群里聊天属于欺骗行为。建议群公告里说明这是机器人，  
> 不要用它去做诱导、引流、刷屏之类的事。

---

## 十一、已经修掉的两个坑（改代码时别踩回去）

1. **群白名单判断写反**：`if not ALLOWED_GROUPS or ...: return` 会导致白名单为空时  
   机器人什么都不回。正确写法是 `if ALLOWED_GROUPS and gid not in ALLOWED_GROUPS: return`。
2. **判断「有没有 @ 我」不能用 `event.message`**：NoneBot 适配器会把 @机器人的消息段  
   从 `event.message` 里**删掉**（原始内容在 `event.original_message`），  
   同时置 `event.to_me = True`。所以必须用 `event.is_tome()` 判断。

---

## 十二、能力说明

- **连续对话 50 轮 + 跨重启记忆**：`AI_HISTORY_TURNS=50`；对话记忆落盘到
  `data/state.json`，机器人重启后自动恢复。
- **历史压缩**：`AI_COMPRESS=true` 时，把最旧的对话压缩成摘要卷进"长期记忆"
  （注入 system prompt），保留最近 `AI_COMPRESS_KEEP_TURNS` 轮原文。省 token 又不丢上下文；
  设 `AI_COMPRESS=false` 则 50 轮原文全发（更贵）。
- **心情系统**：`AI_MOOD=true`。根据对方语气（夸/骂/谢/打招呼）微调机器人心情
  （-10~+10），并注入"当前心情"提示词影响语气；`/mood` 可查看当前心情。
- **主动发表情**：`AI_STICKER=true`，按 `AI_STICKER_PROB` 概率 + `AI_STICKER_COOLDOWN`
  冷却，根据心情选 QQ 经典表情补一句。
- **掉线回归播报**：NapCat 断开后重连，机器人会在 `AI_WELCOME_GROUPS`（缺省回退
  `ALLOWED_GROUPS`）配置的群里发送提示。
- **Web 控制台**：浏览器打开 `http://127.0.0.1:8081/`，包含概览、群与人设、人设库
  （上传/新增人物、编辑正文与 config）、聊天记录筛选、好感度排行、表情库、设置（白名单/管理员/.env 打码）。
  前端在 `webpanel_ui/`，改完刷新即可；后端 `src/plugins/webpanel.py`。开关 `WEB_PANEL`、端口 `WEB_PANEL_PORT`。

> 改 `.env` / 代码后，双击 `run.bat` 重启机器人即生效（它会自动清理旧进程）。

---

## 许可证

QAI 自身使用 [MIT License](LICENSE)。

第三方组件和 NapCat 的许可证说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

NapCat 不随本仓库分发，请从官方渠道获取并遵守其许可证。仓库中的 `personas/example` 为原创示例人设；公开提交时不要加入受版权保护的角色文本或头像。

