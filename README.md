<div align="center">

<img src="launcher_ui/qai-icon.png" width="96" alt="QAI">

# QAI

**本地运行的 QQ AI 机器人控制台**

NapCat + NoneBot2 + OneBot v11  
不用装 Python，不用改配置文件，不用开命令行

[**下载 QAI 1.0.2**](https://github.com/Algorandius/QAI/releases/latest) ·
[使用教程](新手教程.md) ·
[问题反馈](https://github.com/Algorandius/QAI/issues)

![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D4?logo=windows)
![Version](https://img.shields.io/badge/version-1.0.2-6C5CE7)
![License](https://img.shields.io/badge/license-MIT-2ECC71)

</div>

---

## 它是什么

QAI 把 QQ 机器人需要的东西放进了一个桌面应用里：

- NapCat 的安装、启动和扫码登录
- NoneBot2 机器人的启动、停止和重启
- 模型厂商、API Key 和模型选择
- 人设、群绑定、记忆、好感度和表情库
- 聊天记录、运行状态和日志

打开一个窗口，就能完成从安装到聊天的全过程。

---

## 三步开始

### 1. 下载并打开

到 [Releases](https://github.com/Algorandius/QAI/releases/latest) 下载便携包，解压后双击 `QAI.exe`。

不需要安装 Python，也不需要单独下载 NapCat。

> Windows 可能提示“未知发布者”。点“更多信息 → 仍要运行”即可。当前版本没有购买代码签名证书。

### 2. 填模型

点右上角齿轮 **模型配置**：

1. 选择模型厂商
2. 选择模型
3. 填入对应的 API Key
4. 保存

支持智谱、DeepSeek、通义千问、Kimi、混元。每个厂商的 Key 分开保存。

### 3. 登录 QQ 并启动

1. 点 **安装 NapCat**，或选择已有的 NapCat 目录
2. 点 **启动 NapCat**
3. 在弹出的 QQ 登录界面扫码
4. 回到 QAI，点 **启动**

看到「机器人 运行中」和「控制台 已就绪」，就可以在群里 @ 它，或者私聊聊天了。

---

## 界面

| 页面 | 作用 |
| --- | --- |
| 概览 | 运行状态、统计、人设展示、最近对话 |
| 群与人设 | 每个群/私聊独立选择角色 |
| 人设库 | 新建、导入、编辑角色与参数 |
| 聊天记录 | 查看消息、图片、表情和发言角色 |
| 好感度 | 群友分数、排名和彩虹进度条 |
| 表情库 | 导入表情、标签、子库管理 |
| 模型配置 | 厂商、模型、API Key、常用参数 |

支持四套主题、自定义背景、卡片缩放/拖动、鼠标光晕和页面切换动效。

---

## 模型

| 模型 | 厂商 | 能力 |
| --- | --- | --- |
| GLM-4.7-Flash | 智谱 | 文本、工具、推理、免费 |
| GLM-5.3-Flash | 智谱 | 文本、识图、视频、工具、推理 |
| GLM-5.3 | 智谱 | 文本、工具、推理 |
| DeepSeek-V4.1-Flash | DeepSeek | 文本、识图、工具、推理 |
| DeepSeek-V4-Pro | DeepSeek | 文本、工具、推理 |
| qwen3.8-flash | 通义千问 | 文本、识图、视频、联网、工具、推理 |
| kimi-k3 | Kimi | 文本、识图、联网、工具、推理 |
| Hy3 | 混元 | 文本、工具、推理 |
| Hy4 preview | 混元 | 文本、识图、工具、推理 |

模型能力会显示在配置界面，识图模型才会出现图像相关参数。

识图模型可开启「识图联网核对」：图片涉及人物、地点、事件、商品等信息时，先联网查证，再结合图片识别结果回复。

---

## 人设与记忆

- 每个群/私聊可以绑定不同角色
- 每个角色有自己的参数、可用表情子库和对话记忆
- 切换角色会载入该角色在这个场景的记忆，切回可以恢复
- 长期记忆和好感度按场景共享
- 删除角色会清理对应绑定，不会自动删除聊天记录

人设目录格式很简单：

```text
personas/
└─ example/
   ├─ persona.txt
   └─ config.json
```

仓库自带一个原创示例人设「小满」，可以直接改成你自己的角色。

---

## 数据与隐私

QAI 默认只监听本机：

```text
机器人   127.0.0.1:8080
管理台   127.0.0.1:8081
控制中心 127.0.0.1:8082
NapCat   127.0.0.1:6099
```

以下数据只保存在本机：

- `data/`：对话记忆、长期记忆、心情、好感度、绑定关系
- `assets/stickers/`：表情图片
- `logs/`：运行日志
- `.env`：Token、模型 Key、本地配置

QAI 本身不上传这些数据。聊天内容会发送给你选择的模型厂商，具体以厂商的隐私政策为准。

---

## 常见问题

<details>
<summary><b>提示“未知发布者” / SmartScreen 拦截</b></summary>

当前 exe 没有代码签名。点“更多信息 → 仍要运行”。

如果公司电脑有安全策略，需要把 QAI 目录加入白名单。

</details>

<details>
<summary><b>NapCat 一键安装失败</b></summary>

常见报错是 `WinError 10054` 或连接超时，说明当前网络到 GitHub 的下载连接被重置。

处理方式：

1. 挂代理/VPN 后重新点「安装到 QAI 目录」
2. 或打开 [NapCat 官方下载页](https://github.com/NapNeko/NapCatQQ/releases)，手动下载 Windows Node 包
3. 解压后，把包含 `launcher-win10.bat` 的目录填到「NapCat 目录」

</details>

<details>
<summary><b>NapCat 启动后没有二维码</b></summary>

先确认 NapCat 窗口没有关闭。扫码登录必须由你本人完成。

如果 NapCat 目录不完整，重新点“安装 NapCat”，或选择已有的 `launcher-win10.bat` 所在目录。

</details>

<details>
<summary><b>机器人显示未连接</b></summary>

检查：

1. NapCat 是否已登录 QQ
2. QAI 的「机器人」和「控制台」是否都在运行
3. `.env` 里的 `ONEBOT_ACCESS_TOKEN` 是否和 NapCat 反向 WebSocket 的 Token 一致

QAI 启动器会读取 NapCat 的 `config/webui.json`，一般不需要手动填 Token。

</details>

<details>
<summary><b>模型报 401 / 403 / 余额不足</b></summary>

401/403 通常是 Key 填错、Key 与厂商不匹配，或模型没有开通。

余额不足、限流和地区限制由模型厂商返回，QAI 只负责显示错误。

</details>

<details>
<summary><b>端口被占用</b></summary>

默认使用 8080 / 8081 / 8082 / 6099。

先关闭旧的 QAI、NapCat 或 QQ 进程，再重新启动。不要同时开两份 QAI。

</details>

<details>
<summary><b>怎么彻底重置</b></summary>

关闭 QAI 后删除 `data/` 和 `logs/`，重新启动。

`.env` 里的 API Key 和配置不会自动删除。

</details>

---

## 从源码运行

只有开发或二次开发才需要这一节。普通用户直接用 Releases 里的便携包。

<details>
<summary><b>展开源码运行步骤</b></summary>

需要 Windows、Python 3.10+、Git。

```powershell
git clone https://github.com/Algorandius/QAI.git
cd QAI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env`，填入至少一个模型 Key，然后：

```powershell
python bot.py
```

另开一个窗口：

```powershell
python launcher.py
```

浏览器打开 `http://127.0.0.1:8081/` 可以只使用 Web 管理台。

源码结构：

```text
src/                机器人核心
src/plugins/        NoneBot 插件
launcher_ui/        控制中心界面
webpanel_ui/        Web 管理台
personas/           人设目录
```

</details>

---

## 开源说明

QAI 自身使用 [MIT License](LICENSE)。

第三方组件和 NapCat 的许可证说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

NapCat 不随本仓库分发，请从官方渠道获取并遵守其许可证。仓库中的示例人设为原创内容；公开提交时不要加入受版权保护的角色文本或头像。

---

<div align="center">

如果 QAI 对你有帮助，可以点一个 Star。

</div>
