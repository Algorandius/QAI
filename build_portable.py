"""构建便携版 QAI Control Center。

用法：
    .venv\\Scripts\\python.exe build_portable.py

产物：
    dist\\QAI-Portable\\               （可直接运行）
    dist\\QAI-Portable-YYYYMMDD.zip    （发给别人）
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT.parent / "dist"
BUILD = DIST / "QAI-Portable"
ZIP_PATH = DIST / f"QAI-Portable-{time.strftime('%Y%m%d')}.zip"

# 在线更新用的包名跟着 version.py 走
try:
    from version import VERSION as APP_VERSION
except Exception:
    APP_VERSION = "0.0.0"
UPDATE_ZIP_PATH = DIST / f"QAI-{APP_VERSION}-update.zip"
SETUP_EXE = DIST / "QAI-Setup.exe"

# 构建用的基础 Python：优先环境变量，其次开发机上的旧默认位置。
# 换机器时只要设一次 BUILD_BASE_PYTHON，不用回来改代码。
LEGACY_BASE_PYTHON = Path(r"C:\Users\fy688\.workbuddy\binaries\python\versions\3.13.12")
LOCK_FILE = ROOT / "requirements.lock"

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache")


def resolve_base_python() -> Path:
    env_value = os.getenv("BUILD_BASE_PYTHON", "").strip()
    if env_value:
        return Path(env_value)
    if LEGACY_BASE_PYTHON.is_dir():
        return LEGACY_BASE_PYTHON
    for guess in (
        Path(r"C:\Python313"),
        Path(r"C:\Program Files\Python313"),
        Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python313",
    ):
        if (guess / "python.exe").is_file():
            return guess
    return LEGACY_BASE_PYTHON


BASE_PYTHON = resolve_base_python()


def find_csc() -> Path | None:
    """找 csc.exe：不同 Windows 版本目录名不一样，别写死。"""
    fixed = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    if fixed.is_file():
        return fixed
    roots = (
        Path(r"C:\Windows\Microsoft.NET\Framework64"),
        Path(r"C:\Windows\Microsoft.NET\Framework"),
    )
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            found += [p / "csc.exe" for p in root.iterdir() if (p / "csc.exe").is_file()]
        except Exception:
            continue
    return sorted(found, reverse=True)[0] if found else None


# 发布包只带这一套原创示例人设。
# personas/ 下其它目录是作者自己的角色，只留在本机，不能跟着 zip 发出去。
SHIPPED_PERSONAS = ("example",)
DEFAULT_PERSONA = SHIPPED_PERSONAS[0]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def _run_cmd(args: list[str], timeout: int = 180) -> tuple[int, str]:
    """跑命令并取回输出。

    不用管道（capture_output）：受限账户 / 安全软件下创建匿名管道会直接
    WinError 5，重定向到临时文件才稳。
    """
    fd, path = tempfile.mkstemp(prefix="qai-build-", suffix=".txt")
    rc = 1
    try:
        with os.fdopen(fd, "wb") as fh:
            try:
                proc = subprocess.run(
                    args, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout
                )
                rc = proc.returncode
            except Exception as e:
                return 1, str(e)
        try:
            return rc, Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return rc, ""
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


def verify_dependency_lock() -> None:
    """对照 requirements.lock 检查当前 venv 的版本，缺锁就提示先生成。"""
    if not LOCK_FILE.is_file():
        log("没有 requirements.lock，跳过依赖版本校验（建议先 pip freeze > requirements.lock）")
        return
    from importlib import metadata

    wanted: dict[str, str] = {}
    for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        wanted[name.strip()] = version.strip()
    drift: list[str] = []
    for name, expect in wanted.items():
        try:
            actual = metadata.version(name)
        except Exception:
            drift.append(f"{name} 未安装（锁要求 {expect}）")
            continue
        if actual != expect:
            drift.append(f"{name} 实际 {actual}，锁要求 {expect}")
    if drift:
        for item in drift:
            log(f"  ! {item}")
        raise SystemExit(
            "依赖版本和 requirements.lock 不一致，构建出来的包和锁定的版本不同。\n"
            "要么 pip install -r requirements.lock 对齐，要么重新生成 requirements.lock。"
        )
    log(f"依赖锁校验通过（{len(wanted)} 个包）")


def copy_runtime() -> Path:
    runtime = BUILD / "runtime" / "python"
    if not BASE_PYTHON.is_dir():
        raise SystemExit(
            f"找不到基础 Python：{BASE_PYTHON}\n"
            "请设置环境变量 BUILD_BASE_PYTHON 指向一个便携版 Python 3.13 目录，例如：\n"
            "  $env:BUILD_BASE_PYTHON = 'C:\\Python313'"
        )
    log(f"复制 Python 运行时：{BASE_PYTHON}")
    shutil.copytree(BASE_PYTHON, runtime, ignore=IGNORE, dirs_exist_ok=True)
    # 把项目 venv 里的依赖覆盖进运行时
    site = runtime / "Lib" / "site-packages"
    src_site = ROOT / ".venv" / "Lib" / "site-packages"
    log("叠加项目依赖 site-packages")
    shutil.copytree(src_site, site, ignore=IGNORE, dirs_exist_ok=True)
    # 运行时不需要 pip/脚本目录，减小体积
    for name in ("Scripts", str(Path("Lib") / "ensurepip")):
        p = runtime / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for pat in ("pip", "pip-*", "setuptools", "setuptools-*", "wheel", "wheel-*"):
        for p in site.glob(pat):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    check_code = "import nonebot, fastapi, dotenv, websockets, webview; print('ok')"
    if os.getenv("BUILD_CHECK_CLR", "1") == "1":
        check_code = "import nonebot, fastapi, dotenv, websockets, webview, clr; print('ok')"
    check = subprocess.run(
        [str(runtime / "python.exe"), "-c", check_code],
        cwd=str(BUILD), capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise SystemExit(f"运行时自检失败：\n{check.stdout}\n{check.stderr}")
    log("运行时自检通过")
    return runtime


def copy_personas() -> None:
    """只打包 SHIPPED_PERSONAS，作者自己的角色留在本机。"""
    log(f"复制人设（仅 {'、'.join(SHIPPED_PERSONAS)}）")
    dst = BUILD / "personas"
    dst.mkdir(parents=True, exist_ok=True)
    for name in SHIPPED_PERSONAS:
        src = ROOT / "personas" / name
        if not (src / "persona.txt").is_file():
            raise SystemExit(f"缺少随包人设：personas/{name}/persona.txt")
        shutil.copytree(src, dst / name, ignore=IGNORE, dirs_exist_ok=True)
    readme = ROOT / "personas" / "README.md"
    if readme.is_file():
        shutil.copy2(readme, dst / "README.md")


def copy_app(runtime: Path) -> None:
    log("复制应用文件")
    files = [
        "launcher.py", "qai_window.py", "bot.py", "stop.py", "updater.py",
        "version.py", "requirements.txt", "新手教程.md",
    ]
    for name in files:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, BUILD / name)
    for name in ("src", "webpanel_ui", "launcher_ui"):
        shutil.copytree(ROOT / name, BUILD / name, ignore=IGNORE, dirs_exist_ok=True)
    copy_personas()
    for name in ("停止QAI.bat", "重启QAI.bat", "stop.py"):
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, BUILD / name)
    (BUILD / "data").mkdir(exist_ok=True)
    (BUILD / "assets").mkdir(exist_ok=True)
    (BUILD / "logs").mkdir(exist_ok=True)
    (BUILD / "data" / "current_persona.json").write_text(
        '{"id": "' + DEFAULT_PERSONA + '"}', encoding="utf-8"
    )
    (BUILD / "data" / "scene_persona.json").write_text("{}", encoding="utf-8")

    # 便携版专用启动脚本（走 runtime，不用 .venv）
    (BUILD / "启动QAI.bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "runtime\\python\\python.exe launcher.py\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    (BUILD / "启动QAI.vbs").write_text(
        'Option Explicit\r\n'
        'Dim fso, sh, dir\r\n'
        'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
        'Set sh = CreateObject("WScript.Shell")\r\n'
        'dir = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
        'sh.CurrentDirectory = dir\r\n'
        'sh.Run """" & dir & "\\runtime\\python\\pythonw.exe"" launcher.py", 0, False\r\n',
        encoding="utf-8",
    )
    (BUILD / "创建QAI桌面快捷方式.bat").write_text(
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \"$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\QAI.lnk'); $s.TargetPath='%CD%\\QAI.exe'; $s.WorkingDirectory='%CD%'; $s.IconLocation='%CD%\\QAI.exe,0'; $s.Save()\"\r\n"
        "echo Desktop shortcut created.\r\n"
        "pause\r\n",
        encoding="utf-8",
    )


# 变量名里带这些词的一律当成密钥，构建时清空。
# 注意 LLM_KEY_ZHIPU 这类名字 "_KEY" 在中间，所以不能只判断结尾。
SECRET_NAME_RE = re.compile(r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD)(?:_|$)")

# 人设单独选厂商时要用的 Key 槽位（跟着包一起发，保持为空）
PROVIDER_KEY_SLOTS = (
    "LLM_KEY_ZHIPU",
    "LLM_KEY_DEEPSEEK",
    "LLM_KEY_QWEN",
    "LLM_KEY_KIMI",
    "LLM_KEY_HUNYUAN",
)

PROVIDER_KEY_BLOCK = """
# ===== 各厂商 API Key（人设单独选模型时用；不填则回落到 LLM_API_KEY）=====
# 也可以在控制中心「模型配置 → 各厂商 Key」里填，不用手改这里
""" + "".join(f"{name}=\n" for name in PROVIDER_KEY_SLOTS)

# 在线更新源相关（可选）。跟着包发，用户连不上 GitHub 时能自己换源。
UPDATE_KEY_SLOTS = (
    "QAI_UPDATE_MANIFEST",
    "QAI_UPDATE_MIRRORS",
)
UPDATE_BLOCK = """
# ===== 在线更新源（可选，留空即用内置默认）=====
# 连不上 GitHub 时有两种办法：
#   1) QAI_UPDATE_MANIFEST 指向一个 JSON 清单，完全绕开 GitHub API
#      形如 {"version":"1.1.2","notes":"...","page":"...",
#            "update":{"name":"QAI-1.1.2-update.zip","url":"https://...","size":0,"sha256_url":"..."}}
#   2) QAI_UPDATE_MIRRORS 填加速前缀，多个用英文逗号分隔
#      例如：https://gh-proxy.com/,https://github.chenc.dev/
# 另外界面上还有「从本地文件更新」，不联网也能更新。
""" + "".join(f"{name}=\n" for name in UPDATE_KEY_SLOTS)

# 构建时强制覆盖的键（非密钥类）。tools/check_env_keys.py 也读这份，
# 保证「代码里读的键」和「打包出来的键」不会对不上。
SANITIZE_DEFAULTS = {
    "BOT_ADMIN_QQ": "",
    "ALLOWED_GROUPS": "",
    "NAPCAT_DIR": "",
    "NAPCAT_AUTO_START": "false",
    "LAUNCHER_PORT": "8082",
    "LAUNCHER_AUTO_START": "true",
    "NAPCAT_PORT": "6099",
    # 人设按 .env 的默认模型回落；留空则由 LLM_BASE_URL 推断
    "LLM_PROVIDER": "",
    # 关窗口行为：ask = 每次询问（默认），exit = 全停，background = 只关界面
    "QAI_CLOSE_BEHAVIOR": "ask",
}


def sanitize_env() -> None:
    """生成发布用的 .env：清掉一切密钥，只留可公开的默认值。"""
    log("生成脱敏 .env（不含真实 API Key）")
    text = (ROOT / ".env").read_text(encoding="utf-8")
    # 每次构建重新生成 OneBot Token：以前是把作者本机的 Token 原样发出去，
    # 所有下载 QAI 的人共用一个固定值。它只用于本机 127.0.0.1，但仍然不该复用。
    updates = dict(SANITIZE_DEFAULTS)
    updates["ONEBOT_ACCESS_TOKEN"] = "qai-" + secrets.token_hex(12)
    lines = text.splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if not m:
            continue
        name = m.group(1)
        if name in updates:
            lines[i] = f"{name}={updates[name]}"
            seen.add(name)
        elif SECRET_NAME_RE.search(name):
            # 通配清空：LLM_KEY_*、PIXABAY_API_KEY、UNSPLASH_ACCESS_KEY、
            # *_TOKEN、*_SECRET …… 以后新增的密钥变量也不会漏
            lines[i] = f"{name}="
            seen.add(name)
    for name, value in updates.items():
        if name not in seen:
            lines.append(f"{name}={value}")
    out = "\n".join(lines).rstrip() + "\n"
    if "sk-" in out:
        # 注释里也别留下示例 Key
        out = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", out)
    # 补上各厂商 Key 的说明和空槽位，方便用户手改 .env
    if "LLM_KEY_ZHIPU=" not in out:
        out += PROVIDER_KEY_BLOCK
    if "QAI_UPDATE_MANIFEST=" not in out:
        out += UPDATE_BLOCK
    (BUILD / ".env").write_text(out, encoding="utf-8")
    (BUILD / ".env.example").write_text(out, encoding="utf-8")


def make_guide() -> None:
    guide = """QAI CHATBOT · 桌面控制中心（便携版）
=====================================

一、先准备（必须）
1. Windows 10/11 电脑，安装 QQNT 客户端。
2. 一个专门给机器人用的 QQ 小号（不要用主号）。
3. 任一支持 OpenAI 兼容接口的模型 API Key（DeepSeek/OpenAI/Gemini/OpenRouter/Qwen 等）。
4. NapCat 可在 QAI 的「首次配置」里一键下载安装到 QAI\\napcat，无需先手动装。

二、第一次使用
1. 把整个文件夹解压到纯英文、可写路径，例如 D:\\QAI（不要放 Program Files/中文路径）。
2. 双击 QAI.exe。
3. 第一次会弹「首次配置」：
   - 选模型厂商和模型（会显示识图/视频/原生联网/工具/推理/本地等能力徽标）；
   - 填对应 API Key（QAI 是本地前端，Key 只写本机 .env，直连厂商）；
   - NapCat 点「安装到 QAI 目录」自动下载到 QAI\\napcat（约 120MB），或点「自动检测」选已有目录；
   - 管理员 QQ、群白名单可留空（白名单留空 = 所有群都响应）；
   - 点「保存并启动」。
4. 切到顶部「NapCat」标签，点「启动 NapCat」→ 在 UAC 弹窗确认 → 登录 QQ（扫码）。
5. 顶部「连接」变绿后，管理台即可使用。

三、常见问题
- 双击没反应：看 logs\\launcher.log；或用 启动QAI.bat 看报错。
- Windows 提示未知发布者：这是未签名程序，选「更多信息 → 仍要运行」。
- NapCat 起不来：确认 QQNT 已安装、NapCat 目录正确；NapCat 需要管理员权限。
- NapCat 一键安装失败：检查网络能否访问 GitHub；也可以手动下载后点「自动检测」选目录。
- WebUI 空白：点右上角「新窗口」，会用浏览器打开带 token 的 WebUI。
- 群白名单：管理台 → 群与人设 → 群白名单，保存后需要重启机器人。
- API Key 泄露：不要把这个文件夹直接发给别人；.env 里有你的 Key。
- 数据都在本机 data\\ 下，删除对应文件即可清空。

四、端口
- 8080：机器人反向 WebSocket（NapCat 连这里）
- 8081：管理控制台
- 8082：QAI 桌面控制中心
- 6099：NapCat WebUI
只监听 127.0.0.1，不要做端口转发。

五、NapCat 安装
https://github.com/NapNeko/NapCatQQ/releases
下载 NapCat.Shell.Windows.Node.zip，按 README 注入 QQ；启动脚本一般是 launcher-win10.bat。
"""
    (BUILD / "首次使用.txt").write_text(guide, encoding="utf-8")


def verify_build() -> None:
    """构建后自检：私人人设、真实 Key 都不许出现在发布包里。"""
    log("自检发布内容")
    pdir = BUILD / "personas"
    shipped = {p.name for p in pdir.iterdir() if p.is_dir()} if pdir.is_dir() else set()
    leaked = sorted(shipped - set(SHIPPED_PERSONAS))
    if leaked:
        raise SystemExit(
            f"发布包里出现了不该打包的人设：{leaked}（只允许 {list(SHIPPED_PERSONAS)}）"
        )
    if not shipped:
        raise SystemExit("发布包里没有任何人设")
    state = BUILD / "data" / "current_persona.json"
    if not state.is_file() or DEFAULT_PERSONA not in state.read_text(encoding="utf-8"):
        raise SystemExit(f"默认人设没有指向 {DEFAULT_PERSONA}")
    env_text = (BUILD / ".env").read_text(encoding="utf-8")
    # ONEBOT_ACCESS_TOKEN 要随包发给用户去和 NapCat 配对，本来就非空，不算泄漏。
    allowed = {"ONEBOT_ACCESS_TOKEN"}
    values: dict[str, str] = {}
    for line in env_text.splitlines():
        name, _, value = line.strip().partition("=")
        if not name or name.startswith("#"):
            continue
        values[name] = value
        if value and name not in allowed and SECRET_NAME_RE.search(name):
            raise SystemExit(f"发布包的 .env 里 {name} 不是空的，打包前先清掉")
    if not values.get("ONEBOT_ACCESS_TOKEN"):
        raise SystemExit("发布包的 .env 里 ONEBOT_ACCESS_TOKEN 是空的，NapCat 会连不上")
    missing = [name for name in PROVIDER_KEY_SLOTS if name not in values]
    if missing:
        raise SystemExit(f"发布包的 .env 里缺少厂商 Key 槽位：{missing}")
    missing = [name for name in UPDATE_KEY_SLOTS if name not in values]
    if missing:
        raise SystemExit(f"发布包的 .env 里缺少更新源槽位：{missing}")
    log(
        f"自检通过：人设 {sorted(shipped)}，默认 {DEFAULT_PERSONA}，"
        f"密钥已清空，OneBot Token 每次构建随机生成"
    )


def build_exe(runtime: Path) -> None:
    log("编译便携版入口 exe")
    csc = find_csc()
    if not csc:
        raise SystemExit(
            "找不到 csc.exe（.NET Framework 编译器）。\n"
            "请确认 C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe 存在；"
            "只在 Windows 上可用。"
        )
    out = BUILD / "QAI.exe"
    icon = ROOT / "launcher_ui" / "app.ico"
    cmd = [
        str(csc), "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+",
        f"/win32icon:{icon}", f"/out:{out}", str(ROOT / "launcher_entry.cs"),
    ]
    rc, out_text = _run_cmd(cmd)
    if rc != 0:
        raise SystemExit(f"exe 编译失败：\n{out_text[:800]}")
    if not out.is_file():
        raise SystemExit("exe 未生成")
    log(f"exe 生成：{out} ({out.stat().st_size} 字节)")


def make_zip() -> None:
    log(f"打包 zip：{ZIP_PATH}")
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in BUILD.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(BUILD.parent))
    log(f"zip 完成：{ZIP_PATH} ({ZIP_PATH.stat().st_size / 1024 / 1024:.1f} MB)")


def make_update_zip() -> None:
    """生成在线更新包：只有程序文件，没有 runtime/data/.env，通常几百 KB。

    名单直接取自 updater.py，保证「更新包装什么」和「更新器允许改什么」一致。
    """
    log(f"打包更新包：{UPDATE_ZIP_PATH}")
    try:
        import updater
    except Exception as e:
        log(f"跳过更新包（updater.py 不可用：{e}）")
        return
    if UPDATE_ZIP_PATH.exists():
        UPDATE_ZIP_PATH.unlink()
    count = 0
    with zipfile.ZipFile(UPDATE_ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in updater.PROGRAM_FILES:
            src = BUILD / rel
            if src.is_file():
                z.write(src, rel)
                count += 1
        for name in updater.PROGRAM_DIRS:
            base = BUILD / name
            if not base.is_dir():
                continue
            for src in sorted(base.rglob("*")):
                if not src.is_file():
                    continue
                if "__pycache__" in src.parts or src.suffix in (".pyc", ".pyo"):
                    continue
                z.write(src, src.relative_to(BUILD))
                count += 1
        for rel in updater.PERSONA_FILES:
            src = BUILD / rel
            if src.is_file():
                z.write(src, rel)
                count += 1
    size_kb = UPDATE_ZIP_PATH.stat().st_size / 1024
    log(f"更新包完成：{UPDATE_ZIP_PATH} ({size_kb:.0f} KB, {count} 个文件)")


def build_installer() -> None:
    """编译「一键安装器」：用户下载这个小 exe 双击即可，不用解压 zip。"""
    log("编译一键安装器")
    csc = find_csc()
    if not csc:
        log("找不到 csc.exe，跳过安装器")
        return
    src = ROOT / "installer.cs"
    if not src.is_file():
        log("缺少 installer.cs，跳过安装器")
        return
    icon = ROOT / "launcher_ui" / "app.ico"
    cmd = [
        str(csc), "/nologo", "/target:winexe", "/optimize+",
        f"/win32icon:{icon}",
        "/r:System.Windows.Forms.dll",
        "/r:System.Drawing.dll",
        "/r:System.IO.Compression.FileSystem.dll",
        f"/out:{SETUP_EXE}",
        str(src),
    ]
    rc, out = _run_cmd(cmd)
    if rc != 0 or not SETUP_EXE.is_file():
        log(f"安装器编译失败：{out[:400]}")
        return
    log(f"安装器生成：{SETUP_EXE} ({SETUP_EXE.stat().st_size // 1024} KB)")


def main() -> int:
    verify_dependency_lock()
    if BUILD.exists():
        log(f"清理旧构建：{BUILD}")
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True, exist_ok=True)
    runtime = copy_runtime()
    copy_app(runtime)
    if LOCK_FILE.is_file():
        shutil.copy2(LOCK_FILE, BUILD / "requirements.lock")
    sanitize_env()
    make_guide()
    verify_build()
    build_exe(runtime)
    make_zip()
    make_update_zip()
    build_installer()
    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
