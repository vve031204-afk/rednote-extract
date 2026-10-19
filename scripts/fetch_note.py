#!/usr/bin/env python3
"""小红书笔记抓取与下载脚本（跨平台，无 WorkBuddy 依赖）。

用法:
    python fetch_note.py <笔记链接/分享文案/笔记ID> [--out 输出目录] [--profile 登录态目录]
                         [--no-convert] [--headed] [--wait 秒] [--login-timeout 秒] [--retries N]

功能:
    1. 识别 App 分享文案、xhslink 短链、完整笔记链接或裸 ID；
    2. 检测登录态（profile 持久化，复用 cookie），未登录时弹出可见窗口扫码；
    3. 从页面 __INITIAL_STATE__ 提取标题/正文/作者/图片清单；
    4. 按 imageList 顺序下载图片（校验状态码与 Content-Type，失败重试），webp 转 jpg；
    5. 输出「输出目录/标题_短ID/01.jpg…」与 metadata.json。

依赖: playwright, pillow
浏览器: 优先系统 Chrome（channel="chrome"），否则回退 playwright 自带 chromium。

退出码:
    0  全部成功
    1  参数/输入无法识别
    2  登录或抓取失败
    3  笔记不存在、私密或不可浏览
    4  图片未全部下载成功（部分成功也会写 metadata.json）
"""
import argparse
import io
import json
import os
import re
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("缺少 playwright，请先运行 setup.sh 安装依赖。")
try:
    from PIL import Image
except ImportError:
    Image = None

DEFAULT_PROFILE = os.path.join(os.path.expanduser("~"), ".xhs_session")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 小于该体积的响应一律视为异常（正常笔记图远大于此）
MIN_IMAGE_BYTES = 2048

# URL 里不会出现空格、汉字或全角标点；遇到它们即视为 URL 结束
_URL_RE = re.compile(
    r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef<>\"'“”‘’]+")
# URL 末尾可能粘着中文标点或引号，需要剥掉
_TRAILING = "，。；：！？、,;:!?）)】]}>\"'”’"
_NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item|item)/([0-9a-fA-F]{24})")
_IMAGE_FILE_RE = re.compile(r"^(\d{2,3})\.(?:jpe?g|webp)$", re.IGNORECASE)

UNAVAILABLE_MARKERS = ("笔记不存在", "暂时无法浏览", "页面不见了", "内容无法查看", "你访问的页面")
CAPTCHA_MARKERS = ("拖动", "拼图", "滑块")
LOGIN_MARKERS = ("扫码登录", "登录后查看", "登录后推荐", "立即登录")


def note_url(target):
    """把分享文案 / 短链 / 完整链接 / 裸 ID 规整为可访问的 URL。

    小红书 App 复制的分享文案形如「34 【标题】 😆 http://xhslink.com/a/xxx」，
    既不以 http 开头也没有 24 位 ID，必须先把其中的 URL 抠出来。
    """
    t = (target or "").strip()
    if not t:
        raise ValueError("请输入笔记链接、分享文案或笔记 ID")
    m = _URL_RE.search(t)
    if m:
        return m.group(0).rstrip(_TRAILING)
    m = _NOTE_ID_RE.search(t)
    if m:
        return f"https://www.xiaohongshu.com/explore/{m.group(1).lower()}"
    m = re.fullmatch(r"[0-9a-fA-F]{24}", t)
    if m:
        return f"https://www.xiaohongshu.com/explore/{t.lower()}"
    raise ValueError(f"无法识别的输入：{t[:60]!r}；请粘贴完整分享链接或 24 位笔记 ID")


def note_id_from(url):
    """从 URL 里取笔记 ID；短链取不到时返回 None。"""
    m = _NOTE_ID_RE.search(url or "")
    return m.group(1).lower() if m else None


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s or "小红书笔记").strip()
    s = s.rstrip(". ")
    return s[:80] or "小红书笔记"


def extract_note(page, note_id=None):
    """从 __INITIAL_STATE__ 提取笔记数据。

    __INITIAL_STATE__ 有循环引用，不能 JSON.stringify，只取需要的字段。
    页面可能预加载了多条笔记，优先按 URL 里的 ID 精确取，取不到才回退第一条。
    """
    js = """
    (id) => {
        const st = window.__INITIAL_STATE__;
        const m = st && st.note && st.note.noteDetailMap;
        if (!m) return null;
        const keys = Object.keys(m);
        const k = (id && m[id]) ? id
                : (keys.length === 1 ? keys[0]
                : (keys.find(x => m[x] && m[x].note) || keys[0]));
        const n = m[k] && m[k].note;
        if (!n) return null;
        return {
            id: k,
            title: n.title || '',
            desc: n.desc || '',
            author: (n.user && n.user.nickname) || '',
            time: n.time || 0,
            images: (n.imageList || [])
                .map(x => ({
                    url: (x.urlDefault || '').replace(/^http:/, 'https:')
                }))
                .filter(x => x.url)
        };
    }
    """
    return page.evaluate(js, note_id)


def _body_text(page):
    try:
        return page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 4000)")
    except Exception:
        return ""


def page_state(page):
    """判断当前页面状态：loaded / unavailable / captcha / login / unknown。"""
    try:
        if page.locator("#detail-title").count() > 0:
            return "loaded"
    except Exception:
        pass
    text = _body_text(page)
    if any(k in text for k in UNAVAILABLE_MARKERS):
        return "unavailable"
    if any(k in text for k in CAPTCHA_MARKERS):
        return "captcha"
    if any(k in text for k in LOGIN_MARKERS):
        return "login"
    return "unknown"


def wait_for_note(page, timeout, label="等待笔记加载"):
    """轮询页面状态，直到出现确定状态（loaded / unavailable / captcha）或超时。"""
    deadline = time.time() + max(1, timeout)
    last_log = 0.0
    while time.time() < deadline:
        state = page_state(page)
        if state in ("loaded", "unavailable", "captcha"):
            return state
        if time.time() - last_log > 20:
            print(f"  {label}...")
            last_log = time.time()
        time.sleep(2)
    return page_state(page)


def fetch_image(ctx, url, attempts=3):
    """下载一张图片，校验 HTTP 状态与 Content-Type，失败按退避重试。"""
    last = "未知错误"
    for n in range(max(1, attempts)):
        try:
            r = ctx.request.get(
                url,
                timeout=60000,
                headers={
                    "Referer": "https://www.xiaohongshu.com/",
                    "User-Agent": UA,
                    "Accept": "image/webp,image/*,*/*;q=0.8",
                },
            )
            if not r.ok:
                last = f"HTTP {r.status}"
            else:
                ctype = (r.headers or {}).get("content-type", "") or ""
                body = r.body()
                if ctype.startswith("image/") and len(body) >= MIN_IMAGE_BYTES:
                    return body
                last = f"非图片响应（{ctype or '无 Content-Type'}，{len(body)}B）"
        except Exception as e:  # 网络异常、超时等
            last = str(e)
        if n + 1 < attempts:
            time.sleep(1.5 * (n + 1))
    raise RuntimeError(last)


def prune_stale(folder, saved_files):
    """删掉文件夹里残留的旧序号图片（重跑时图片变少会留下旧图）。"""
    keep = set(saved_files)
    for name in os.listdir(folder):
        if _IMAGE_FILE_RE.match(name) and name not in keep:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def download(ctx, imgs, folder, convert=True, retries=3):
    """按顺序下载全部图片，返回 (saved, failed)。"""
    saved, failed = [], []
    total = len(imgs)
    for i, im in enumerate(imgs, 1):
        url = (im.get("url") if isinstance(im, dict) else im) or ""
        if not url:
            failed.append({"index": i, "url": "", "error": "图片地址为空"})
            print(f"  [{i}/{total}] 跳过：图片地址为空")
            continue
        try:
            body = fetch_image(ctx, url, attempts=retries)
        except Exception as e:
            failed.append({"index": i, "url": url, "error": str(e)})
            print(f"  [{i}/{total}] 下载失败: {e}")
            continue

        name = f"{i:02d}"
        ext = "jpg"
        written = False
        if convert and Image is not None:
            try:
                with Image.open(io.BytesIO(body)) as im2:
                    im2.convert("RGB").save(
                        os.path.join(folder, name + ".jpg"),
                        "JPEG", quality=95, subsampling=0)
                written = True
            except Exception:
                ext = "webp"
        if not written:
            with open(os.path.join(folder, name + ".webp"), "wb") as f:
                f.write(body)
            ext = "webp"

        saved.append({"index": i, "file": f"{name}.{ext}", "bytes": len(body), "url": url})
        print(f"  [{i}/{total}] {name}.{ext} ({len(body)}B)")
    return saved, failed


def launch_context(p, profile, headless, launch_kwargs):
    """启动浏览器：优先系统 Chrome，其次 playwright chromium，错误信息区分原因。"""
    errors = []
    for channel in ("chrome", None):
        try:
            kwargs = dict(launch_kwargs)
            if channel:
                kwargs["channel"] = channel
            return p.chromium.launch_persistent_context(
                profile, headless=headless, **kwargs)
        except Exception as e:
            msg = str(e)
            errors.append(msg)
            if ("ProcessSingleton" in msg or "SingletonLock" in msg
                    or "already in use" in msg.lower()):
                sys.exit(
                    f"登录态目录正被另一个浏览器实例占用：{profile}\n"
                    "请关闭残留的抓取窗口（或结束占用该目录的 Chrome 进程）后重试。"
                )
    sys.exit(
        "无法启动浏览器：既没有系统 Chrome，也没有 playwright 的 chromium。\n"
        "请安装 Google Chrome（https://www.google.com/chrome/）后重试；\n"
        "或手动下载 chromium：python -m playwright install chromium。\n"
        f"原始错误：{errors[-1]}"
    )


def fail(code, message):
    print(message, file=sys.stderr)
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser(description="小红书笔记抓取与图片下载")
    ap.add_argument("target", help="笔记链接、App 分享文案或笔记 ID")
    ap.add_argument("--out", default=os.getcwd(), help="输出目录（默认当前目录）")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="登录态持久化目录")
    ap.add_argument("--no-convert", action="store_true", help="不转 jpg，保留 webp")
    ap.add_argument("--headed", action="store_true", help="直接打开可见窗口（默认先无头尝试）")
    ap.add_argument("--wait", type=int, default=30, help="已登录时等待详情页加载的秒数（默认 30）")
    ap.add_argument("--login-timeout", type=int, default=300, help="扫码登录最长等待秒数（默认 300）")
    ap.add_argument("--retries", type=int, default=3, help="单张图片下载重试次数（默认 3）")
    args = ap.parse_args()

    try:
        url = note_url(args.target)
    except ValueError as e:
        fail(1, str(e))

    out_dir = os.path.expanduser(args.out)
    profile = os.path.expanduser(args.profile)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(profile, exist_ok=True)
    try:
        os.chmod(profile, 0o700)
    except OSError:
        pass

    note_id = note_id_from(url)
    launch_kwargs = dict(
        viewport={"width": 1360, "height": 940},
        locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
    )

    ctx = None
    try:
        with sync_playwright() as p:
            ctx = launch_context(p, profile, headless=not args.headed,
                                 launch_kwargs=launch_kwargs)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                fail(2, f"打开页面失败：{e}")

            state = wait_for_note(page, args.wait)

            if state == "unavailable":
                fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")

            if state != "loaded" and not args.headed:
                # 未登录或命中验证：换可见窗口，让用户扫码/过滑块
                try:
                    ctx.close()
                except Exception:
                    pass
                ctx = None
                print("需要登录，正在打开扫码窗口...")
                ctx = launch_context(p, profile, headless=False,
                                     launch_kwargs=launch_kwargs)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                except Exception as e:
                    fail(2, f"打开页面失败：{e}")

            if state != "loaded":
                st = page_state(page)
                if st == "unavailable":
                    fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")
                if st == "captcha":
                    print("检测到滑块验证，请在弹出的浏览器窗口中拖动完成验证...")
                else:
                    print("需要登录，请用小红书 App 扫码（窗口已弹出）...")
                state = wait_for_note(page, args.login_timeout, label="等待登录/验证")

            if state == "unavailable":
                fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")
            if state != "loaded":
                fail(2, "等待超时：仍未进入笔记详情页。请重试，并在弹出的窗口中完成扫码/验证。")

            note = extract_note(page, note_id)
            if not note:
                fail(2, "未能提取到笔记内容（可能仍未登录、链接无效或页面结构变化）。")

            note_id = note_id or note.get("id")
            title = note["title"]
            folder_name = safe_name(title)
            if note_id:
                folder_name = f"{folder_name}_{note_id[:8]}"
            folder = os.path.join(out_dir, folder_name)
            os.makedirs(folder, exist_ok=True)

            print(f"标题: {title}")
            print(f"作者: {note['author']}")
            print(f"图片数: {len(note['images'])}")
            print(f"保存到: {folder}")

            saved, failed = download(ctx, note["images"], folder,
                                     convert=not args.no_convert,
                                     retries=args.retries)

            prune_stale(folder, [s["file"] for s in saved])

            meta = {
                "id": note_id,
                "title": title,
                "desc": note["desc"],
                "author": note["author"],
                "time": note["time"],
                "image_count": len(note["images"]),
                "saved_count": len(saved),
                "failed_count": len(failed),
                "saved": saved,
                "failed": failed,
                "url": url,
            }
            with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            print(f"完成：共 {len(note['images'])} 张，成功 {len(saved)} 张，"
                  f"失败 {len(failed)} 张；元数据见 metadata.json")
            if failed:
                for item in failed:
                    print(f"  - 第 {item['index']} 张：{item['error']}")
                fail(4, "部分图片未下载成功，请检查网络后重跑（重跑会补齐并清理旧图）。")
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
