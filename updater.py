"""QAI 更新应用器：只替换程序文件，绝不碰用户数据。

为什么要单独一个进程：
更新时 launcher.py、QAI.exe、runtime\\python 都可能正在被占用，
所以由 launcher 先把更新的 zip 解压到暂存目录，再启动本脚本，然后自己退出；
本脚本等启动器端口释放后再落地文件，最后把 QAI 重新拉起来。

安全规则（写死在 PROGRAM_PATHS 里，不在名单内的一律不动）：
  - 永不替换：data/  logs/  assets/  runtime/  napcat/  .env*  QAI.exe  *.lnk
  - personas/ 只覆盖 example 和 README，用户自己建的角色不会被删
  - 替换前把旧文件备份到 data/_update_backup/<时间戳>/

用法（一般由启动器调用，也可手动排错）：
    python updater.py --staging <解压目录> --port 8082 --from-version 1.1.0 --to-version 1.1.1
    python updater.py --staging <目录> --root <另一个安装目录> --no-restart --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 允许被更新覆盖的文件（相对安装目录）
PROGRAM_FILES: tuple[str, ...] = (
    "launcher.py",
    "qai_window.py",
    "bot.py",
    "stop.py",
    "updater.py",
    "version.py",
    "requirements.txt",
    "requirements.lock",
    "新手教程.md",
    "README.md",
    "首次使用.txt",
    "停止QAI.bat",
    "重启QAI.bat",
    "启动QAI.bat",
    "启动QAI.vbs",
    "创建QAI桌面快捷方式.bat",
)

# 允许整体覆盖的目录
PROGRAM_DIRS: tuple[str, ...] = (
    "src",
    "launcher_ui",
    "webpanel_ui",
    "tools",
)

# personas/ 下只更新官方示例，用户自己的角色不碰
PERSONA_FILES: tuple[str, ...] = (
    "personas/example/persona.txt",
    "personas/example/config.json",
    "personas/README.md",
)

# 绝不允许被更新触及的顶层名字（双保险，即使误入名单也会被拦）
PROTECTED_TOP = {
    "data", "logs", "assets", "runtime", "napcat", ".venv", ".git",
    "__pycache__", "dist", "release", "build",
}
PROTECTED_NAMES = {".env", ".env.example", "QAI.exe"}


def _is_protected(rel: Path) -> bool:
    if rel.parts and rel.parts[0].lower() in PROTECTED_TOP:
        return True
    if any(part in PROTECTED_NAMES for part in rel.parts):
        return True
    return rel.suffix == ".lnk"


def collect_plan(staging: Path) -> list[tuple[Path, Path]]:
    """返回 [(源文件, 目标相对路径)]。"""
    plan: list[tuple[Path, Path]] = []
    for name in PROGRAM_FILES:
        src = staging / name
        if src.is_file():
            plan.append((src, Path(name)))
    for name in PROGRAM_DIRS:
        src_dir = staging / name
        if not src_dir.is_dir():
            continue
        for src in src_dir.rglob("*"):
            if not src.is_file():
                continue
            if "__pycache__" in src.parts or src.suffix in (".pyc", ".pyo"):
                continue
            plan.append((src, src.relative_to(staging)))
    for name in PERSONA_FILES:
        src = staging / name
        if src.is_file():
            plan.append((src, Path(name)))
    return [(src, rel) for src, rel in plan if not _is_protected(rel)]


def validate_staging(staging: Path) -> str:
    """暂存目录必须像一份 QAI 程序，否则不更新。返回空串表示通过。"""
    if not staging.is_dir():
        return f"暂存目录不存在：{staging}"
    for must in ("launcher.py", "version.py"):
        if not (staging / must).is_file():
            return f"暂存目录里缺少 {must}，不像完整的 QAI 程序，已中止"
    if not (staging / "src").is_dir():
        return "暂存目录里缺少 src/，已中止"
    return precheck_python(staging)


def precheck_python(program_root: Path) -> str:
    """落地前先确认新代码至少能被解析。

    只检查「文件在不在」是不够的：一个解压不完整、或手工做坏的包，
    会让 QAI 更新完直接起不来，而且用户完全不知道发生了什么。
    返回空串表示通过。
    """
    import ast

    checked = 0
    for src, rel in collect_plan(program_root):
        if src.suffix != ".py":
            continue
        try:
            ast.parse(src.read_text(encoding="utf-8"), str(rel))
        except SyntaxError as e:
            return f"{rel} 语法错误（第 {e.lineno} 行）：{e.msg}"
        except Exception as e:
            return f"{rel} 无法读取：{e}"
        checked += 1
    if checked == 0:
        return "更新包里没有任何 .py 文件，不像完整的 QAI 程序"
    return ""


def restore_backup(backup: Path | None, root: Path) -> int:
    """更新失败时把备份盖回去，尽量恢复成更新前的状态。"""
    if not backup or not backup.is_dir():
        return 0
    restored = 0
    for src in sorted(backup.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(backup)
        try:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            restored += 1
        except Exception:
            continue
    return restored


def read_version(root: Path) -> str:
    try:
        text = (root / "version.py").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("VERSION"):
                return line.split("=", 1)[1].strip().strip("'\"")
    except Exception:
        pass
    return ""


def backup_files(root: Path, rels: list[Path]) -> Path | None:
    """把即将被覆盖的旧文件备份到 data/_update_backup/<时间戳>/。"""
    if not rels:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = root / "data" / "_update_backup" / stamp
    try:
        for rel in rels:
            src = root / rel
            if not src.is_file():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        return dest
    except Exception:
        return None


def apply_update(root: Path, staging: Path, dry_run: bool = False) -> dict:
    plan = collect_plan(staging)
    if not plan:
        return {"ok": False, "error": "暂存目录里没有可更新的文件"}
    rels = [rel for _, rel in plan]
    copied = 0
    errors: list[str] = []
    backup = None
    restored = 0
    if not dry_run:
        backup = backup_files(root, rels)
        for src, rel in plan:
            target = root / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                copied += 1
            except Exception as e:
                errors.append(f"{rel}: {e}")
                break          # 一失败就停，别继续把安装目录改成半新半旧
        if errors:
            # 回滚：把备份盖回去，让 QAI 至少还能跑更新前的版本
            restored = restore_backup(backup, root)
    return {
        "ok": not errors,
        "planned": len(plan),
        "copied": copied if not dry_run else 0,
        "dry_run": dry_run,
        "backup": str(backup) if backup else "",
        "restored": restored,
        "errors": errors[:10],
    }


def wait_port_free(port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                time.sleep(0.4)
        except OSError:
            return True
    return False


def restart_app(root: Path) -> None:
    exe = root / "QAI.exe"
    if exe.is_file():
        subprocess.Popen(
            [str(exe)], cwd=str(root), close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return
    vbs = root / "启动QAI.vbs"
    if vbs.is_file():
        subprocess.Popen(["wscript.exe", str(vbs)], cwd=str(root), close_fds=True)
        return
    bat = root / "启动QAI.bat"
    if bat.is_file():
        subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(root), close_fds=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="QAI 更新应用器")
    ap.add_argument("--staging", required=True, help="已解压的新版本程序目录")
    ap.add_argument("--root", default=str(ROOT), help="QAI 安装目录（默认脚本所在目录）")
    ap.add_argument("--port", type=int, default=8082, help="启动器端口，先等它释放")
    ap.add_argument("--from-version", default="")
    ap.add_argument("--to-version", default="")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log", default="", help="把结果写成 json，方便启动器回报")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    staging = Path(args.staging).resolve()
    result: dict = {
        "ok": False,
        "from": args.from_version or read_version(root),
        "to": args.to_version,
        "root": str(root),
        "staging": str(staging),
    }
    try:
        problem = validate_staging(staging)
        if problem:
            result["error"] = problem
        else:
            if args.port and not args.dry_run:
                wait_port_free(args.port)
            applied = apply_update(root, staging, dry_run=args.dry_run)
            result.update(applied)
            result["ok"] = bool(applied.get("ok"))
    except Exception as e:
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"

    if args.log:
        try:
            Path(args.log).parent.mkdir(parents=True, exist_ok=True)
            Path(args.log).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # 不管成功还是失败，都要把 QAI 拉起来。
    # 启动器此时已经退出；如果这里不重启，用户看到的就是「QAI 自己消失了」，
    # 失败时更糟——他已经没法从界面上看到我写进 result 的错误。
    if not args.no_restart and not args.dry_run:
        try:
            restart_app(root)
        except Exception:
            pass
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
