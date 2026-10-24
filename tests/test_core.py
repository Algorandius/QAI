"""QAI 纯函数测试。

用标准库 unittest，不引入新依赖：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

这些函数都不碰网络和数据库，改坏了会立刻红，是最划算的一层保护。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_nonebot() -> None:
    """ai_chat 在模块顶层注册 matcher，必须先 init 才能 import。"""
    import nonebot

    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

import build_portable  # noqa: E402

from src import model_catalog  # noqa: E402
from src.plugins import ai_chat  # noqa: E402


class PlainTextTest(unittest.TestCase):
    """QQ 里渲染不了 Markdown，parity 全靠这个函数。"""

    def test_code_block_replaced(self):
        self.assertEqual(ai_chat.plain_text("```py\nprint(1)\n```"), "[代码块已省略]")

    def test_inline_code_unwrapped(self):
        self.assertEqual(ai_chat.plain_text("用 `pip` 安装"), "用 pip 安装")

    def test_heading_marker_removed(self):
        self.assertEqual(ai_chat.plain_text("## 标题"), "标题")

    def test_bold_unwrapped(self):
        self.assertEqual(ai_chat.plain_text("**重要**"), "重要")

    def test_list_bullet_becomes_dot(self):
        self.assertEqual(ai_chat.plain_text("- 第一项"), "· 第一项")

    def test_blank_lines_collapsed(self):
        self.assertEqual(ai_chat.plain_text("a\n\n\n\nb"), "a\n\nb")

    def test_cq_code_left_alone(self):
        # CQ 码不是 Markdown，由 message_to_text 负责，这里必须原样返回
        self.assertEqual(ai_chat.plain_text("[CQ:face,id=14]"), "[CQ:face,id=14]")


class SplitSentencesTest(unittest.TestCase):
    def test_splits_on_chinese_punctuation(self):
        self.assertEqual(
            ai_chat.split_sentences("你好啊！今天不错。"),
            ["你好啊！", "今天不错。"],
        )

    def test_single_sentence_untouched(self):
        self.assertEqual(ai_chat.split_sentences("就这样"), ["就这样"])

    def test_blank_returns_empty(self):
        self.assertEqual(ai_chat.split_sentences("   "), [])

    def test_limit_keeps_every_char(self):
        text = "一二三四五六七八九十"
        parts = ai_chat.split_sentences(text, limit=5)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(p) <= 5 for p in parts), parts)


class PoliticsGuardTest(unittest.TestCase):
    def test_hits_keyword(self):
        self.assertTrue(ai_chat.is_politics("我们聊聊政治吧"))

    def test_normal_chat_passes(self):
        self.assertFalse(ai_chat.is_politics("今天吃什么好呢"))
        self.assertFalse(ai_chat.is_politics("这个游戏真好玩"))


class MoodPromptTest(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            (6.0, "很开心"),
            (5.9, "心情不错"),
            (2.0, "心情不错"),
            (1.9, "很平静"),
            (-1.9, "很平静"),
            (-2.0, "有点低落"),
            (-5.9, "有点低落"),
            (-6.0, "很难过"),
        ]
        for value, expect in cases:
            with self.subTest(value=value):
                self.assertEqual(ai_chat.mood_prompt(value)[0], expect)

    def test_returns_prompt_text(self):
        label, prompt = ai_chat.mood_prompt(8)
        self.assertEqual(label, "很开心")
        self.assertTrue(prompt)


class AffinityNameTest(unittest.TestCase):
    def test_boundaries(self):
        cases = [
            (100.0, "很信任"),
            (80.0, "很信任"),
            (79.9, "挺熟"),
            (50.0, "挺熟"),
            (49.9, "有点熟"),
            (20.0, "有点熟"),
            (19.9, "刚认识"),
            (0.0, "刚认识"),
            (-0.1, "有点怕"),
            (-20.0, "有点怕"),
        ]
        for value, expect in cases:
            with self.subTest(value=value):
                self.assertEqual(ai_chat.affinity_name(value), expect)


class ModelCatalogTest(unittest.TestCase):
    def test_key_names_for_provider_alias(self):
        self.assertEqual(model_catalog.key_envs_for("zhipu"), ("LLM_KEY_ZHIPU",))
        hunyuan = model_catalog.key_envs_for("hunyuan")
        self.assertEqual(hunyuan[0], "LLM_KEY_HUNYUAN")
        self.assertIn("LLM_KEY_TENCENT", hunyuan)

    def test_write_always_uses_primary_key(self):
        for provider in ("hunyuan", "tencent", "tencent_intl"):
            with self.subTest(provider=provider):
                self.assertEqual(model_catalog.key_env_for(provider), "LLM_KEY_HUNYUAN")

    def test_unknown_provider(self):
        self.assertEqual(model_catalog.key_envs_for("nope"), ())
        self.assertEqual(model_catalog.key_env_for("nope"), "")

    def test_base_for_model_override(self):
        # Hy4 走国际站，base 与厂商默认值不同，这条最容易写错
        self.assertIn(
            "tencentcloudmaas.com",
            model_catalog.base_for("hunyuan", "hy4-preview"),
        )
        self.assertEqual(
            model_catalog.base_for("deepseek", "deepseek-flash"),
            "https://api.deepseek.com/v1",
        )


class SecretNameTest(unittest.TestCase):
    def test_sensitive_names(self):
        for name in (
            "LLM_API_KEY",
            "LLM_KEY_ZHIPU",
            "PIXABAY_API_KEY",
            "PEXELS_API_KEY",
            "UNSPLASH_ACCESS_KEY",
            "ONEBOT_ACCESS_TOKEN",
            "SOME_SECRET",
            "DB_PASSWORD",
        ):
            with self.subTest(name=name):
                self.assertTrue(build_portable.SECRET_NAME_RE.search(name), name)

    def test_plain_settings_not_sensitive(self):
        for name in ("AI_MAX_CHARS", "LLM_PROVIDER", "LLM_MODEL", "LAUNCHER_PORT", "PORT"):
            with self.subTest(name=name):
                self.assertFalse(build_portable.SECRET_NAME_RE.search(name), name)


class StopScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import stop

        cls.stop = stop

    def test_safe_int(self):
        self.assertEqual(self.stop._safe_int("42"), 42)
        self.assertEqual(self.stop._safe_int("abc"), 0)
        self.assertEqual(self.stop._safe_int(None), 0)
        self.assertEqual(self.stop._safe_int("-3"), 0)

    def test_owns_only_project_paths(self):
        root = Path("C:/qai")
        self.assertTrue(self.stop._path_under(
            {"exe": r"C:\qai\runtime\python\python.exe", "cmd": ""}, root))
        self.assertTrue(self.stop._path_under(
            {"exe": "", "cmd": r"python.exe bot.py C:\qai"}, root))
        self.assertFalse(self.stop._path_under({"exe": r"D:\QQ\QQ.exe", "cmd": ""}, root))
        self.assertFalse(self.stop._path_under(None, root))


class UpdaterTest(unittest.TestCase):
    """更新器会动用户磁盘，路径白名单是安全边界，必须钉死。"""

    @classmethod
    def setUpClass(cls):
        import tempfile

        import updater

        cls.updater = updater
        cls.tmp = Path(tempfile.mkdtemp(prefix="qai-upd-"))

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_protected_paths(self):
        for rel in (
            "data/state.json",
            ".env",
            "logs/launcher.log",
            "runtime/python/python.exe",
            "assets/stickers/a.png",
            "napcat/launcher-win10.bat",
            "QAI.exe",
            "some.lnk",
        ):
            with self.subTest(rel=rel):
                self.assertTrue(self.updater._is_protected(Path(rel)), rel)

    def test_program_paths_allowed(self):
        for rel in ("launcher.py", "src/personas.py", "launcher_ui/app.js", "version.py"):
            with self.subTest(rel=rel):
                self.assertFalse(self.updater._is_protected(Path(rel)), rel)

    def test_validate_rejects_incomplete(self):
        self.assertTrue(self.updater.validate_staging(Path(self.tmp) / "not-there"))
        try:
            empty = self.tmp / "empty"
            empty.mkdir(exist_ok=True)
        except OSError as e:      # 受限环境（如沙箱）建不了目录就跳过
            self.skipTest(f"无法创建临时目录：{e}")
        self.assertTrue(self.updater.validate_staging(empty))

    def test_collect_plan_skips_user_data(self):
        staging = self.tmp / "staging"
        try:
            staging.mkdir(exist_ok=True)
            for rel in ("launcher.py", "version.py", ".env", "QAI.exe"):
                (staging / rel).write_text("x", encoding="utf-8")
        except OSError as e:
            self.skipTest(f"无法创建临时文件：{e}")

        planned = {rel.as_posix() for _, rel in self.updater.collect_plan(staging)}
        self.assertEqual(planned, {"launcher.py", "version.py"})


class VersionCompareTest(unittest.TestCase):
    """更新判断靠它，比错了会「永远有新版本」或者「永远不更新」。"""

    @classmethod
    def setUpClass(cls):
        import launcher

        cls.launcher = launcher

    def test_newer(self):
        self.assertTrue(self.launcher._ver_newer("1.1.2", "1.1.1"))
        self.assertTrue(self.launcher._ver_newer("v1.2", "1.1.9"))
        self.assertTrue(self.launcher._ver_newer("2.0.0", "1.9.9"))

    def test_not_newer(self):
        self.assertFalse(self.launcher._ver_newer("1.1.1", "1.1.1"))
        self.assertFalse(self.launcher._ver_newer("1.1.0", "1.1.1"))
        self.assertFalse(self.launcher._ver_newer("", "1.1.1"))

    def test_tolerates_garbage(self):
        self.assertEqual(self.launcher._ver_tuple("v1.2.3-beta.1"), (1, 2, 3))
        self.assertEqual(self.launcher._ver_tuple("abc"), (0,))


class UpdateSourceTest(unittest.TestCase):
    """国内经常连不上 GitHub，多源逻辑是可用性的关键。"""

    @classmethod
    def setUpClass(cls):
        import launcher

        cls.launcher = launcher

    def tearDown(self):
        import os

        os.environ.pop("QAI_UPDATE_MIRRORS", None)
        os.environ.pop("QAI_UPDATE_MANIFEST", None)
        if hasattr(self.launcher, "ENV"):
            self.launcher.ENV.pop("QAI_UPDATE_MIRRORS", None)
            self.launcher.ENV.pop("QAI_UPDATE_MANIFEST", None)

    def test_direct_first_then_builtin_mirrors(self):
        mirrors = self.launcher.update_mirrors()
        self.assertEqual(mirrors[0], "")
        self.assertIn("https://gh-proxy.com/", mirrors)
        self.assertIn("https://github.chenc.dev/", mirrors)

    def test_custom_mirror_wins_and_gets_slash(self):
        import os

        os.environ["QAI_UPDATE_MIRRORS"] = "https://my.mirror/qai,https://other"
        mirrors = self.launcher.update_mirrors()
        self.assertIn("https://my.mirror/qai/", mirrors)
        self.assertIn("https://other/", mirrors)
        self.assertLess(mirrors.index("https://my.mirror/qai/"), mirrors.index("https://gh-proxy.com/"))

    def test_manifest_shape(self):
        info = self.launcher._normalize_release({
            "version": "1.1.9",
            "notes": "改了东西",
            "page": "https://example.com",
            "update": {"name": "QAI-1.1.9-update.zip", "url": "https://example.com/a.zip", "size": 123},
        })
        self.assertEqual(info["latest"], "1.1.9")
        self.assertEqual(info["asset"], "QAI-1.1.9-update.zip")
        self.assertEqual(info["asset_size"], 123)
        self.assertTrue(info["asset_is_update_pack"])

    def test_github_shape_prefers_small_pack(self):
        info = self.launcher._normalize_release({
            "tag_name": "v1.2.0",
            "body": "说明",
            "html_url": "https://github.com/x/y",
            "assets": [
                {"name": "QAI-1.2.0-Windows-x64.zip", "browser_download_url": "u1", "size": 111},
                {"name": "QAI-1.2.0-update.zip", "browser_download_url": "u2", "size": 222},
                {"name": "QAI-1.2.0-update.zip.sha256", "browser_download_url": "u3"},
            ],
        })
        self.assertEqual(info["latest"], "1.2.0")
        self.assertEqual(info["asset"], "QAI-1.2.0-update.zip")
        self.assertEqual(info["asset_url"], "u2")
        self.assertEqual(info["sha256_url"], "u3")
        self.assertTrue(info["asset_is_update_pack"])

    def test_github_shape_falls_back_to_full(self):
        info = self.launcher._normalize_release({
            "tag_name": "v1.2.0",
            "assets": [{"name": "QAI-1.2.0-Windows-x64.zip", "browser_download_url": "u1", "size": 1}],
        })
        self.assertEqual(info["asset"], "QAI-1.2.0-Windows-x64.zip")
        self.assertFalse(info["asset_is_update_pack"])


class _FakeReq:
    """伪造一个带 headers 的请求对象。"""

    def __init__(self, headers):
        self.headers = headers


class RequestGuardTest(unittest.TestCase):
    """写接口的防线：跨站拒绝 + 令牌校验。挡的是「打开网页就能操控 QAI」。"""

    @classmethod
    def setUpClass(cls):
        import launcher

        cls.launcher = launcher

    def test_cross_site_rejected(self):
        self.assertFalse(self.launcher._same_site_ok(_FakeReq({"Sec-Fetch-Site": "cross-site"})))
        self.assertFalse(self.launcher._same_site_ok(_FakeReq({"Origin": "https://evil.example"})))

    def test_same_site_allowed(self):
        # QAI 自己在 8082 页面里嵌 8081 的 iframe，这是 same-site，必须放行
        self.assertTrue(self.launcher._same_site_ok(_FakeReq({"Sec-Fetch-Site": "same-site"})))
        self.assertTrue(self.launcher._same_site_ok(_FakeReq({"Sec-Fetch-Site": "same-origin"})))
        self.assertTrue(self.launcher._same_site_ok(_FakeReq({"Origin": "http://127.0.0.1:8082"})))

    def test_server_side_calls_allowed(self):
        # stop.py / qai_window.py 这类本机进程不带这些头
        self.assertTrue(self.launcher._same_site_ok(_FakeReq({})))

    def test_token_required(self):
        self.assertFalse(self.launcher._token_ok(_FakeReq({})))
        self.assertFalse(self.launcher._token_ok(_FakeReq({"X-QAI-Token": "guess"})))
        self.assertTrue(
            self.launcher._token_ok(_FakeReq({"X-QAI-Token": self.launcher._API_TOKEN}))
        )

    def test_token_is_random(self):
        self.assertGreaterEqual(len(self.launcher._API_TOKEN), 20)


class UpdaterSafetyTest(unittest.TestCase):
    """更新器会改用户磁盘：语法预检 + 失败回滚必须可靠。"""

    @classmethod
    def setUpClass(cls):
        import ast
        import tempfile

        import updater

        cls.updater = updater
        cls.ast = ast
        cls.tmp = Path(tempfile.mkdtemp(prefix="qai-upd2-"))

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _make(self, name, files):
        root = self.tmp / name
        try:
            for rel, text in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        except OSError as e:
            self.skipTest(f"无法创建临时目录：{e}")
        return root

    def test_precheck_catches_syntax_error(self):
        root = self._make("bad", {
            "launcher.py": "# ok\n",
            "version.py": 'VERSION = "1.0"\n',
            "src/broken.py": "def f(:\n",
        })
        problem = self.updater.precheck_python(root)
        self.assertIn("broken.py", problem)

    def test_precheck_passes_valid(self):
        root = self._make("good", {
            "launcher.py": "x = 1\n",
            "version.py": 'VERSION = "1.0"\n',
            "src/ok.py": "def f():\n    return 1\n",
        })
        self.assertEqual(self.updater.precheck_python(root), "")

    def test_restore_backup(self):
        install = self._make("install", {"launcher.py": "# new\n"})
        backup = self._make("backup", {"launcher.py": "# old\n"})
        restored = self.updater.restore_backup(backup, install)
        self.assertEqual(restored, 1)
        self.assertEqual((install / "launcher.py").read_text(encoding="utf-8"), "# old\n")


class WatchdogQuotaTest(unittest.TestCase):
    """机器人「起来就死」时，看门狗必须重启有限次后停手，不能无限闪烁。"""

    def test_gives_up_after_quota(self):
        import launcher as l

        saved = (
            l._WATCHDOG_STOP, l._BOT_WANTED, l._BOT_PROC,
            l.port_open, l.start_bot, l._BOT_LAST_FAILURE,
        )

        class Proc:
            pid = 1
            returncode = 1

            def poll(self):
                return 1

        class Ev:
            def __init__(self):
                self.n = 0

            def wait(self, timeout):
                self.n += 1
                return self.n > 30

            def set(self):
                pass

        calls: list[int] = []
        try:
            l._WATCHDOG_STOP = Ev()
            l._BOT_WANTED = True
            l._BOT_PROC = Proc()
            l._BOT_LAST_FAILURE = {}
            l.port_open = lambda *a, **k: False

            def fake_start():
                calls.append(1)
                l._BOT_PROC = Proc()
                return {"ok": True, "started": True}

            l.start_bot = fake_start
            l.watchdog_loop()

            self.assertEqual(len(calls), l._MAX_RESTARTS)
            self.assertFalse(l._BOT_WANTED)
            self.assertEqual(l._BOT_LAST_FAILURE.get("code"), 1)
            self.assertTrue(l._BOT_LAST_FAILURE.get("reason"))
        finally:
            (l._WATCHDOG_STOP, l._BOT_WANTED, l._BOT_PROC,
             l.port_open, l.start_bot, l._BOT_LAST_FAILURE) = saved


class NapcatPathResolveTest(unittest.TestCase):
    """用户填的 NapCat 路径五花八门，resolve_napcat_dir 要尽量救回来。"""

    def setUp(self):
        import launcher

        self.l = launcher
        self.tmp = Path(__import__("tempfile").mkdtemp(prefix="qai-napcat-"))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, rel: str, launcher_bat: bool = True) -> Path:
        d = self.tmp / rel
        d.mkdir(parents=True, exist_ok=True)
        if launcher_bat:
            (d / "launcher-win10.bat").write_text("@echo off\n", encoding="utf-8")
        return d

    def test_exact_dir(self):
        d = self._make("napcat")
        self.assertEqual(self.l.resolve_napcat_dir(str(d)), d)

    def test_bat_file_input(self):
        d = self._make("napcat")
        self.assertEqual(self.l.resolve_napcat_dir(str(d / "launcher-win10.bat")), d)

    def test_quotes_and_trailing_slash(self):
        d = self._make("napcat")
        self.assertEqual(self.l.resolve_napcat_dir(f'"{d}/"'), d)

    def test_parent_dir_with_single_child(self):
        d = self._make("outer/napcat")
        self.assertEqual(self.l.resolve_napcat_dir(str(self.tmp / "outer")), d)

    def test_inner_subdir_walks_up(self):
        d = self._make("napcat")
        inner = d / "NapCat.Shell"
        inner.mkdir()
        self.assertEqual(self.l.resolve_napcat_dir(str(inner)), d)

    def test_non_napcat_dir(self):
        plain = self.tmp / "empty"
        plain.mkdir()
        self.assertIsNone(self.l.resolve_napcat_dir(str(plain)))

    def test_empty_input(self):
        self.assertIsNone(self.l.resolve_napcat_dir("   "))

    def test_detect_finds_dir_under_root(self):
        """扫描逻辑要能找到带 launcher 的目录。"""
        d = self._make("deep/a/b/napcat")
        import time

        hits = self.l._scan_for_napcat(self.tmp, 4, time.time() + 5, [500])
        self.assertIn(str(d), [h["path"] for h in hits])


if __name__ == "__main__":
    unittest.main()
