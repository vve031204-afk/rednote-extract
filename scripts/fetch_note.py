#!/usr/bin/env python3
"""小红书笔记抓取与下载脚本（跨平台，无 WorkBuddy 依赖）。

用法:
    python fetch_note.py <笔记URL或笔记ID> [--out 输出目录] [--profile 登录态目录] [--no-convert]

功能:
    1. 检测是否已登录（登录态持久化在 profile 目录，可复用，避免每次扫码）；
    2. 未登录时弹出一个可见浏览器窗口，提示扫码登录；
    3. 登录后从页面 __INITIAL_STATE__ 提取标题/正文/作者/图片清单；
    4. 按 imageList 顺序下载图片，webp 转 jpg，存入「输出目录/笔记标题/01.jpg...」；
    5. 输出 metadata.json（标题、作者、正文、图片数）。

依赖: playwright, pillow
浏览器: 优先使用系统 Chrome（channel="chrome"），否则回退到 playwright 自带的 chromium。
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


def note_url(target):
    """把笔记 ID 或各种形式的 URL 规整为 explore 链接。"""
    t = target.strip()
    if t.startswith("http"):
        return t
    m = re.search(r"([0-9a-f]{24,32})", t)
    if m:
        return f"https://www.xiaohongshu.com/explore/{m.group(1)}"
    return t


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s or "小红书笔记").strip()
    return s[:80] or "小红书笔记"


def extract_note(page):
    """从 __INITIAL_STATE__ 提取笔记数据（避开循环引用，只取需要的字段）。"""
    js = """
    () => {
        const st = window.__INITIAL_STATE__;
        const m = st && st.note && st.note.noteDetailMap;
        if (!m) return null;
        const k = Object.keys(m)[0];
        const n = m[k].note;
        if (!n) return null;
        return {
            title: n.title || '',
            desc: n.desc || '',
            author: (n.user && n.user.nickname) || '',
            time: n.time || 0,
            images: (n.imageList || []).map(x => ({
                url: (x.urlDefault || '').replace(/^http:/, 'https:')
            }))
        };
    }
    """
    return page.evaluate(js)


def is_loaded(page):
    """只有笔记详情页的标题元素出现，才认为真正加载成功。

    注意：未登录时小红书仍可能返回笔记的「标题 + 封面第一张图」，
    若用 extract_note 非空兜底会误判为已加载、漏掉扫码，导致只存到 1 张图。
    因此这里只认 #detail-title（登录后的完整详情页才有）。
    """
    try:
        return page.locator("#detail-title").count() > 0
    except Exception:
        return False


def download(ctx, imgs, folder, convert=True):
    saved = []
    for i, im in enumerate(imgs, 1):
        url = im.get("url") if isinstance(im, dict) else im
        try:
            r = ctx.request.get(url, timeout=90000,
                                headers={"Referer": "https://www.xiaohongshu.com/"})
            body = r.body()
            if len(body) < 3000:
                print(f"  [{i}/{len(imgs)}] 内容过小，跳过: {len(body)}B")
                continue
            ext = "jpg"
            if convert and Image is not None:
                try:
                    im2 = Image.open(io.BytesIO(body)).convert("RGB")
                    im2.save(f"{folder}/{i:02d}.jpg", "JPEG", quality=95, subsampling=0)
                except Exception:
                    open(f"{folder}/{i:02d}.webp", "wb").write(body)
                    ext = "webp"
            else:
                open(f"{folder}/{i:02d}.webp", "wb").write(body)
                ext = "webp"
            saved.append({"index": i, "file": f"{i:02d}.{ext}", "bytes": len(body), "url": url})
            print(f"  [{i}/{len(imgs)}] {i:02d}.{ext} ({len(body)}B)")
        except Exception as e:
            print(f"  [{i}/{len(imgs)}] 下载失败: {e}")
    return saved


def launch_context(p, profile, headless, launch_kwargs):
    """启动浏览器：优先系统 Chrome，其次 playwright 自带 chromium，都失败给出友好提示。"""
    last_err = None
    try:
        return p.chromium.launch_persistent_context(
            profile, channel="chrome", headless=headless, **launch_kwargs)
    except Exception as e:
        last_err = e
    try:
        return p.chromium.launch_persistent_context(
            profile, headless=headless, **launch_kwargs)
    except Exception as e:
        last_err = e
    sys.exit(
        "无法启动浏览器：既没有系统 Chrome，也没有 playwright 的 chromium。\n"
        "请安装 Google Chrome（https://www.google.com/chrome/）后重试；\n"
        "或手动下载 chromium：python -m playwright install chromium。\n"
        f"原始错误：{last_err}"
    )


def main():
    ap = argparse.ArgumentParser(description="小红书笔记抓取与图片下载")
    ap.add_argument("target", help="笔记 URL 或笔记 ID")
    ap.add_argument("--out", default=os.getcwd(), help="输出目录（默认当前目录）")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="登录态持久化目录")
    ap.add_argument("--no-convert", action="store_true", help="不转 jpg，保留 webp")
    args = ap.parse_args()

    url = note_url(args.target)
    os.makedirs(args.profile, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)

    with sync_playwright() as p:
        launch_kwargs = dict(
            viewport={"width": 1360, "height": 940},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        # 先无头尝试（若已有登录态直接成功）；失败/未登录再起可见窗口
        ctx = launch_context(p, args.profile, headless=True, launch_kwargs=launch_kwargs)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        time.sleep(6)

        if not is_loaded(page):
            # 未登录：关掉无头实例，弹可见窗口扫码
            ctx.close()
            print("需要登录，正在打开扫码窗口，请用小红书 App 扫码...")
            ctx = launch_context(p, args.profile, headless=False, launch_kwargs=launch_kwargs)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=90000)

            logged_in = False
            for i in range(150):  # 最长约 5 分钟
                time.sleep(2)
                if is_loaded(page):
                    logged_in = True
                    break
                if i % 10 == 0:
                    print(f"  等待扫码登录... {i * 2}s")
            if not logged_in:
                print("登录超时：5 分钟未检测到笔记内容。请重新运行，并在弹出的窗口中完成扫码。")
                sys.exit(2)

        note = extract_note(page)
        if not note:
            print("未能提取到笔记内容（可能仍未登录或链接无效）。")
            sys.exit(2)

        title = note["title"]
        folder = os.path.join(args.out, safe_name(title))
        os.makedirs(folder, exist_ok=True)
        print(f"标题: {title}")
        print(f"作者: {note['author']}")
        print(f"图片数: {len(note['images'])}")
        print(f"保存到: {folder}")

        saved = download(ctx, note["images"], folder, convert=not args.no_convert)

        meta = {
            "title": title,
            "desc": note["desc"],
            "author": note["author"],
            "time": note["time"],
            "image_count": len(note["images"]),
            "saved": saved,
            "url": url,
        }
        with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        print(f"完成：共下载 {len(saved)} 张，元数据见 metadata.json")
        ctx.close()


if __name__ == "__main__":
    main()
