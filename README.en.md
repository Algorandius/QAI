<div align="center">

<img src="launcher_ui/qai-icon.png" width="96" alt="QAI">

# QAI

**A desktop console for running a QQ AI chatbot locally**

NapCat + NoneBot2 + OneBot v11  
No Python install, no config files to hand-edit, no terminal needed

[**Download QAI 1.4.2**](https://github.com/Algorandius/QAI/releases/latest) ·
[Tutorial (Chinese)](新手教程.md) ·
[Report an issue](https://github.com/Algorandius/QAI/issues)

![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D4?logo=windows)
![Version](https://img.shields.io/badge/version-1.4.2-6C5CE7)
![License](https://img.shields.io/badge/license-MIT-2ECC71)

[简体中文](README.md) · English

</div>

---

## What it is

QAI bundles everything a QQ chatbot needs into one desktop app:

- Installing NapCat, launching it, and scanning the login QR code
- Starting, stopping and restarting the NoneBot2 bot
- Choosing a model provider, entering an API key, picking a model
- Personas, per-group binding, memory, affinity scores, a sticker library
- Chat logs, live status and logs

One window covers the whole path from install to chatting.

## What's new in 1.4.2

- **Phone access** — the **Phone** page in the web panel shows a URL and a QR code. Open it on your phone over the same Wi-Fi and manage the bot from there. Always on, no setup required.
- **Remote login** — access from another device requires a password; the session lasts 7 days. Only a scrypt hash is stored, and the password can be reset from the desktop.
- **Mobile layout** — narrow screens get a single-column layout with a hamburger menu on the top left that opens the full sidebar.
- **Works outside the LAN** — pair with Tailscale to reach the machine from anywhere, no public IP needed.

## What's new in 1.3.1

- **Diaries** — one per group *and* per persona, written in that persona's own voice. Generated daily at 23:30 and browsable from the web panel.
- **Sticker battles** — `/开启斗图模式` makes the bot reply with a sticker whenever someone posts one. No model calls, no cost, and it never repeats the same image back to back.
- **Auto-collect stickers** — stickers posted in chat are downloaded into the local library and tagged automatically.
- **Message enhancements** — read receipts, typing indicator, emoji reactions (👍😂👏), history backfill, merged-forward history export.
- **Commands page** — the web panel lists all 30 commands, searchable and copyable. It parses the plugin source, so it never goes stale.
- **Vision fixed** — a config-reading bug had effectively disabled image understanding along with 24 other default-on features. It actually looks at images now.

## Getting started in three steps

### 1. Download and run

Grab **`QAI-Setup-1.4.2.exe`** (about 21 MB) from [Releases](https://github.com/Algorandius/QAI/releases/latest). Double-click, pick a folder, and the installer handles the rest: verify → extract → create a desktop shortcut → launch.

The full package is embedded in the installer, so **installation needs no network access** — you can hand the single file to someone else and it just works.

Alternatively, download `QAI-<version>-Windows-x64.zip` and run `QAI.exe` after extracting.

No Python installation required, and NapCat does not need to be downloaded separately.

> Windows may warn about an unknown publisher. Click "More info → Run anyway". The current build is not code-signed.
> The UI and this documentation are in Chinese; English documentation is being added gradually.

### 2. Configure a model

Open **模型配置** (Model settings) from the gear icon in the top-right:

1. Pick a provider
2. Pick a model
3. Paste the matching API key
4. Save

Supported providers: Zhipu GLM, DeepSeek, Alibaba Qwen, Moonshot Kimi, Tencent Hunyuan. Keys are stored per provider, so switching models does not lose the others.

Keys live only in the local `.env` file. QAI has no backend server and never uploads them.

### 3. Log in to QQ and start

1. Under **NapCat**, click "install to QAI folder". If GitHub is unreachable, use the Quark netdisk link and extract manually.
2. Click "Start NapCat", confirm the UAC prompt, and scan the QR code with your phone.
3. Once logged in, open **Model settings → NapCat** and click "one-click connect".
4. Approve the UAC prompt. If NapCat asks you to log in again, scan once more.
5. Back in QAI, click **启动** (Start).

Four indicators should turn green:

- Bot: running
- Console: ready
- NapCat: started
- Connection: connected

From there, @ the bot in a group or message it privately.

## Daily use

### Diaries

Each group × persona pair gets its own diary, written from that day's real conversations. Fewer than four messages in a day and it skips rather than inventing content.

```
/日记          Show the three most recent entries
/日记 写       Write today's entry immediately
/日记 删       Delete entries
```

Browse everything under **日记** in the web panel.

### Stickers and battles

```
/开启斗图模式     Start replying with stickers in this group
/关闭斗图模式     Stop
/收图            Collect the sticker you replied to, or pull from QQ favourites
```

Stickers others post are collected automatically (up to 60 per hour).

### Commands

The **指令** page lists every command with aliases and descriptions, searchable and one-click copyable. `/help` in chat works too. The leading slash can also be `!` or `！`.

## Data and privacy

Everything runs locally. Nothing is uploaded to a QAI server.

| Path | Contents |
| --- | --- |
| `data/state.json` | Recent conversations and summaries |
| `data/chatlog.jsonl` | Full chat log |
| `data/diary.json` | Diaries |
| `data/longterm.json` | Long-term memory |
| `personas/` | Personas |
| `assets/stickers/` | Sticker library |
| `.env` | API keys and settings |

Chat content is sent to whichever model provider you selected, subject to that provider's privacy policy. Export and import under **Model settings → data backup** — handy before moving machines.

## Closing QAI

Closing the window asks what you want:

- **Background** — the window closes, the bot and launcher keep running. A tray icon lets you reopen the window by double-clicking it.
- **Quit** — stop the bot, stop the console, exit the launcher, release ports 8080/8081/8082.

Tick "remember my choice" to skip the prompt; change it any time under **Settings → close behaviour**.

NapCat runs as a separate process and is not stopped by closing QAI. To take QQ offline as well, click "Stop NapCat" first.

Only use `停止QAI.bat` if the window is frozen.

## FAQ

<details>
<summary><b>"Unknown publisher" / SmartScreen warning</b></summary>

The executable is not code-signed. Click "More info → Run anyway".

On corporate machines you may need to whitelist the QAI folder.
</details>

<details>
<summary><b>NapCat path is wrong / auto-detect finds nothing</b></summary>

Click "auto-detect" first — QAI scans its own folder, Downloads, Desktop, Documents and drive roots. If that fails, point it at the folder that **contains `launcher-win10.bat`**, not the parent folder and not the `.bat` file itself.

Getting it slightly wrong is fine: saving corrects it to the right level.
</details>

<details>
<summary><b>The bot ignores images</b></summary>

Image understanding requires a vision-capable model. Check under **Model settings** that the selected model is marked for images and that `AI_VISION` is on.
</details>

<details>
<summary><b>Nothing shows up in the panel</b></summary>

Check the four status indicators first. If the connection is not green, use "one-click connect" under NapCat. If the bot is not running, click Start.
</details>

## Running from source

Only needed for development. Regular users should use the installer.

<details>
<summary><b>Show source setup</b></summary>

Requires Windows, Python 3.10+ and Git.

```powershell
git clone https://github.com/Algorandius/QAI.git
cd QAI
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill in at least one model key in `.env`, then:

```powershell
python bot.py
```

In another terminal:

```powershell
python launcher.py
```

Opening `http://127.0.0.1:8081/` in a browser gives you the web panel on its own.

Layout:

```text
src/                Bot core
src/plugins/        NoneBot plugins
launcher_ui/        Desktop console UI
webpanel_ui/        Web panel
personas/           Personas
```

See [开发文档.md](开发文档.md) for API, auth, build and release details (Chinese).

</details>

## License

MIT.

---

## Notes on scope

- Windows 10 / 11, 64-bit only.
- QQ login always requires you to scan the QR code yourself; QAI cannot log in for you.
- Automating a QQ account is contrary to the QQ terms of service. Use a secondary account, not your main one.
- NapCat and NoneBot2 are third-party projects; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
