#!/usr/bin/env python3
"""fetch_note.py 的离线回归测试（不访问网络、不启动真实浏览器）。

运行:
    .venv/bin/python tests/test_fetch_note.py
覆盖三类曾经真实发生的故障：
    1. 退出顺序：浏览器必须在 playwright.stop 之前关闭（否则挂死）；
    2. 可观测性：--progress-file 必须依次落 launch/load/download/done；
    3. 环境前提：stale SingletonLock 能被清理，活进程占用能被识别。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import fetch_note as fn  # noqa: E402


class FakeLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class FakePage:
    def __init__(self, note, loaded=True, body=""):
        self._note = note
        self._loaded = loaded
        self._body = body
        self.url = "https://www.xiaohongshu.com/explore/66f0c8a1000000001e01abcd"

    def locator(self, _selector):
        return FakeLocator(1 if self._loaded else 0)

    def evaluate(self, _js, _arg=None):
        if _arg is None:
            return self._body
        return self._note

    def goto(self, *_a, **_k):
        return None

    def reload(self, *_a, **_k):
        return None


class FakeResponse:
    def __init__(self, body):
        self.ok = True
        self.status = 200
        self.headers = {"content-type": "image/jpeg"}
        self._body = body

    def body(self):
        return self._body


class FakeRequest:
    def get(self, _url, **_kwargs):
        return FakeResponse(b"x" * 4096)


class FakeContext:
    def __init__(self, page, events):
        self.pages = [page]
        self.request = FakeRequest()
        self._events = events

    def cookies(self):
        return [{"name": "web_session", "value": "fake"}]

    def new_page(self):
        return self.pages[0]

    def close(self):
        self._events.append("close")


class FakePlaywright:
    def __init__(self, events):
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._events.append("stop")


class NoteUrlTest(unittest.TestCase):
    def test_share_text(self):
        self.assertEqual(
            fn.note_url("34 【标题】 😆 http://xhslink.com/a/AbCdEf，复制后打开"),
            "http://xhslink.com/a/AbCdEf")

    def test_note_id(self):
        self.assertEqual(
            fn.note_url("66F0C8A1000000001E01ABCD"),
            "https://www.xiaohongshu.com/explore/66f0c8a1000000001e01abcd")

    def test_full_url_keeps_token(self):
        url = "https://www.xiaohongshu.com/explore/66f0c8a1000000001e01abcd?xsec_token=AB"
        self.assertEqual(fn.note_url(url), url)

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            fn.note_url("这不是链接")


class LockTest(unittest.TestCase):
    def test_stale_lock_removed(self):
        with tempfile.TemporaryDirectory() as d:
            os.symlink("somehost-999999", os.path.join(d, "SingletonLock"))
            self.assertIsNone(fn.clear_stale_locks(d))
            self.assertFalse(os.path.lexists(os.path.join(d, "SingletonLock")))

    def test_live_lock_reported(self):
        with tempfile.TemporaryDirectory() as d:
            os.symlink("somehost-%d" % os.getpid(), os.path.join(d, "SingletonLock"))
            self.assertEqual(fn.clear_stale_locks(d), os.getpid())


class PageStateTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(fn.page_state(FakePage(None, loaded=True)), "loaded")
        self.assertEqual(fn.page_state(FakePage(None, loaded=False, body="安全限制 IP存在风险")),
                         "blocked")
        self.assertEqual(fn.page_state(FakePage(None, loaded=False, body="请扫码登录")), "login")


class MainFlowTest(unittest.TestCase):
    def _run(self, note, extra_args=None, page=None, progress_path=None, out=None):
        events = []
        ctx = FakeContext(page or FakePage(note), events)
        argv = ["fetch_note.py", "66f0c8a1000000001e01abcd"]
        argv += extra_args or []
        argv += ["--out", out, "--profile", os.path.join(out, "profile")]
        if progress_path:
            argv += ["--progress-file", progress_path]
        with mock.patch.object(fn, "sync_playwright", return_value=FakePlaywright(events)), \
                mock.patch.object(fn, "launch_context", return_value=ctx), \
                mock.patch.object(sys, "argv", argv):
            fn.main()
        files = []
        for root, _dirs, names in os.walk(out):
            for name in names:
                files.append(os.path.join(root, name))
        return events, out, files

    def test_success_closes_before_stop(self):
        note = {"id": "66f0c8a1000000001e01abcd", "title": "标题", "desc": "d",
                "author": "a", "time": 1,
                "images": [{"url": "https://img/1.jpg"}, {"url": "https://img/2.jpg"}]}
        with tempfile.TemporaryDirectory() as tmp:
            progress = os.path.join(tmp, "progress.json")
            out = tempfile.mkdtemp()
            self.addCleanup(__import__("shutil").rmtree, out, True)
            events, out, files = self._run(note, ["--no-convert"],
                                           progress_path=progress, out=out)
            meta_path = [f for f in files if f.endswith("metadata.json")][0]
            meta = json.load(open(meta_path, encoding="utf-8"))
            self.assertEqual(meta["saved_count"], 2)
            self.assertEqual(meta["failed_count"], 0)
            self.assertEqual(events, ["close", "stop"])
            with open(progress, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["stage"], "done")

    def test_out_creates_title_folder(self):
        note = {"id": "66f0c8a1000000001e01abcd", "title": "标题", "desc": "",
                "author": "a", "time": 0, "images": [{"url": "https://img/1.jpg"}]}
        out = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, out, True)
        self._run(note, ["--no-convert"], out=out)
        folder = os.path.join(out, "标题")
        self.assertTrue(os.path.isfile(os.path.join(folder, "metadata.json")))
        self.assertTrue(os.path.isfile(os.path.join(folder, "01.webp")))

    def test_collision_uses_id_suffix(self):
        note = {"id": "66f0c8a1000000001e01abcd", "title": "标题", "desc": "",
                "author": "a", "time": 0, "images": [{"url": "https://img/1.jpg"}]}
        out = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, out, True)
        os.makedirs(os.path.join(out, "标题"))
        with open(os.path.join(out, "标题", "metadata.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "aaaaaaaaaaaaaaaaaaaaaaaa"}, fh)
        self._run(note, ["--no-convert"], out=out)
        self.assertTrue(os.path.isfile(os.path.join(out, "标题_66f0c8a1", "metadata.json")))

    def test_rerun_same_note_reuses_folder(self):
        note = {"id": "66f0c8a1000000001e01abcd", "title": "标题", "desc": "",
                "author": "a", "time": 0, "images": [{"url": "https://img/1.jpg"}]}
        out = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, out, True)
        self._run(note, ["--no-convert"], out=out)
        self._run(note, ["--no-convert"], out=out)
        self.assertEqual([d for d in os.listdir(out) if d != "profile"], ["标题"])

    def test_probe_does_not_download(self):
        note = {"id": "66f0c8a1000000001e01abcd", "title": "标题", "desc": "",
                "author": "a", "time": 0, "images": [{"url": "https://img/1.jpg"}]}
        with tempfile.TemporaryDirectory() as tmp:
            progress = os.path.join(tmp, "p.json")
            out = tempfile.mkdtemp()
            self.addCleanup(__import__("shutil").rmtree, out, True)
            _events, _out, files = self._run(note, ["--probe"],
                                             progress_path=progress, out=out)
            self.assertFalse([f for f in files if f.endswith(".jpg") or f.endswith(".webp")])
            with open(progress, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["stage"], "done")
            self.assertEqual(data["image_count"], 1)

    def test_blocked_exits_2(self):
        note = {"id": "66f0c8a1000000001e01abcd", "title": "", "desc": "",
                "author": "", "time": 0, "images": []}
        page = FakePage(note, loaded=False, body="安全限制 IP存在风险 error_code=300012")
        with tempfile.TemporaryDirectory() as tmp:
            progress = os.path.join(tmp, "p.json")
            out = tempfile.mkdtemp()
            self.addCleanup(__import__("shutil").rmtree, out, True)
            with self.assertRaises(SystemExit) as cm:
                self._run(note, page=page, progress_path=progress, out=out)
            self.assertEqual(cm.exception.code, 2)
            with open(progress, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["code"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
