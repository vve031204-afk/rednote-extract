#!/usr/bin/env python3
"""小红书笔记抓取与下载脚本（跨平台，无 WorkBuddy 依赖）。

用法:
    python fetch_note.py <笔记链接/分享文案/笔记ID> [--out 输出目录] [--profile 登录态目录]
                         [--no-convert] [--wait 秒] [--retries N]
    python fetch_note.py --login [--profile 登录态目录] [--login-timeout 秒]
    python fetch_note.py <链接> --probe [--progress-file 路径]

功能:
    1. 识别 App 分享文案、xhslink 短链、完整笔记链接或裸 ID；
    2. 默认全程无头抓取；需要登录/滑块时以退出码 5 结束，由调用方询问用户后
       运行 --login 打开一次可见窗口完成扫码，登录态保存后自动关闭；
       --probe 只体检（登录态 + 笔记可浏览 + 图片数），不下载；
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
    5  需要登录或人工验证（先询问用户，同意后运行 --login）
"""
import argparse
import io
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("缺少 playwright，请先运行 setup.sh 安装依赖。")
try:
    from PIL import Image
except ImportError:
    Image = None

COOKIE_CACHE = os.path.expanduser("~/.xhs_cookies.json")
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
# 小红书对 IP 的风控页（error_code 300012），不是登录墙也不是笔记不存在
BLOCKED_MARKERS = ("IP存在风险", "安全限制", "error_code=300012")

LOGIN_HINT = ("需要登录小红书：请先询问用户，同意后运行 "
              "`fetch_note.py --login` 打开扫码窗口。")
CAPTCHA_HINT = ("需要人工完成滑块验证：请先询问用户，同意后运行 "
                "`fetch_note.py --login` 打开验证窗口。")
CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
UA_TEMPLATE = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/%s Safari/537.36")
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
"""

BLOCKED_HINT = ("小红书返回安全限制（error_code 300012）。优先用 --headed 重试一次："
                "该错误常见于无头指纹被识别，而非 IP 被封；headed 仍失败再切换网络/代理。")


def _now():
    return time.strftime("%H:%M:%S")


def log(msg, err=False):
    """带时间戳的行缓冲日志；接管道时也能实时看到。"""
    print(f"[{_now()}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


PROGRESS_PATH = None


def progress(stage, **fields):
    """把当前阶段原子写入 --progress-file，供外部轮询，不依赖 stdout。"""
    if not PROGRESS_PATH:
        return
    data = {
        "stage": stage,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pid": os.getpid(),
    }
    data.update(fields)
    tmp = f"{PROGRESS_PATH}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, PROGRESS_PATH)
    except OSError:
        pass


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def clear_stale_locks(profile):
    """清理上次异常退出留下的 Chrome 单例锁。

    返回占用该 profile 的存活 pid；没有活进程占用时返回 None（锁已清理）。
    """
    lock = os.path.join(profile, "SingletonLock")
    if os.path.islink(lock):
        try:
            pid = os.readlink(lock).rsplit("-", 1)[-1]
        except OSError:
            pid = ""
        if pid.isdigit() and _pid_alive(int(pid)):
            return int(pid)
    removed = []
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = os.path.join(profile, name)
        if os.path.lexists(path):
            try:
                os.remove(path)
                removed.append(name)
            except OSError:
                pass
    if removed:
        log("已清理残留锁：" + ", ".join(removed))
    return None


def safe_close(ctx, code=0, timeout=10):
    """关闭浏览器；close 卡住时用 SIGALRM 打断并强制退出，避免进程挂死。

    Playwright 的同步对象绑定在创建线程上，不能放到子线程里 close，
    因此这里用主线程的 alarm 做超时兜底。
    """
    if ctx is None:
        return
    if not hasattr(signal, "SIGALRM"):
        try:
            ctx.close()
        except Exception:
            pass
        return

    def _timeout(signum, frame):
        raise TimeoutError("close timeout")

    old = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(timeout)
    try:
        ctx.close()
    except TimeoutError:
        log("关闭浏览器超过 %ss，强制退出进程。" % timeout, err=True)
        os._exit(code)
    except Exception:
        pass
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def write_back_cookies(p, target, cookies, launch_kwargs):
    """把登录态写回另一个 profile（默认 ~/.xhs_session）。"""
    if not cookies:
        return False
    try:
        os.makedirs(target, exist_ok=True)
        ctx = p.chromium.launch_persistent_context(target, headless=True, **launch_kwargs)
    except Exception as e:
        log("登录态回写失败：%s" % e, err=True)
        return False
    try:
        ctx.add_cookies(cookies)
        return True
    except Exception as e:
        log("登录态回写失败：%s" % e, err=True)
        return False
    finally:
        safe_close(ctx)


def prepare_isolated_profile(base):
    """复制一份独立 profile（跳过 Singleton* 锁），用于并发/临时运行。"""
    dst = tempfile.mkdtemp(prefix="xhs-prof-")
    if os.path.isdir(base):
        try:
            shutil.copytree(base, dst, symlinks=True, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("Singleton*"))
        except Exception as e:
            log("复制登录态失败（%s），将使用空 profile，可能需要重新登录。" % e, err=True)
    return dst


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


def choose_folder(out_dir, title, note_id):
    """在 out_dir 下选目录：优先 <标题>；被别的笔记占用才加 <ID前8位> 后缀。"""
    base = safe_name(title)

    def existing_id(path):
        try:
            with open(os.path.join(path, "metadata.json"), encoding="utf-8") as fh:
                return json.load(fh).get("id")
        except Exception:
            return None

    def usable(path):
        if not os.path.isdir(path):
            return True
        if not os.listdir(path):
            return True
        return note_id is not None and existing_id(path) == note_id

    folder = os.path.join(out_dir, base)
    if usable(folder):
        return folder
    suffix = (note_id or "")[:8] or "副本"
    folder = os.path.join(out_dir, "%s_%s" % (base, suffix))
    if usable(folder):
        return folder
    i = 2
    while True:
        candidate = os.path.join(out_dir, "%s_%s_%d" % (base, suffix, i))
        if usable(candidate):
            return candidate
        i += 1


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
    if any(k in text for k in BLOCKED_MARKERS):
        return "blocked"
    if any(k in text for k in CAPTCHA_MARKERS):
        return "captcha"
    if any(k in text for k in LOGIN_MARKERS):
        return "login"
    if "website-login" in (page.url or ""):
        return "login"
    return "unknown"


def wait_for_note(page, timeout, label="等待笔记加载"):
    """轮询页面状态，直到出现确定状态（loaded / unavailable / captcha）或超时。"""
    deadline = time.time() + max(1, timeout)
    last_log = 0.0
    while time.time() < deadline:
        state = page_state(page)
        if state in ("loaded", "unavailable", "captcha", "blocked"):
            return state
        if time.time() - last_log > 20:
            log("  %s..." % label)
            progress("load", detail=label)
            last_log = time.time()
        time.sleep(2)
    return page_state(page)


def is_logged_in(ctx):
    """登录态判据：存在非空的 web_session cookie。"""
    try:
        for cookie in ctx.cookies():
            if cookie.get("name") in ("web_session", "web_session_id") and cookie.get("value"):
                return True
    except Exception:
        pass
    return False


def login_flow(p, profile, launch_kwargs, timeout, write_back_to=None, stealth=True,
               cache_cookies=True):
    """打开一次可见窗口供用户扫码，登录成功后立即关闭并返回 0；超时返回 5。

    登录页可能自己跳转或新开标签页，循环里需要容忍页面被关闭、重新取页。
    """
    log("正在打开浏览器窗口，请用小红书 App 扫码登录（成功后窗口会自动关闭）...")
    progress("login", detail="opening")
    ctx = launch_context(p, profile, headless=False, launch_kwargs=launch_kwargs, stealth=stealth)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.xiaohongshu.com/explore",
                      wait_until="domcontentloaded", timeout=90000)
        except Exception:
            pass
        start = time.time()
        deadline = start + max(10, timeout)
        last_tick = 0.0
        while time.time() < deadline:
            try:
                pages = ctx.pages
            except Exception:
                log("浏览器窗口已关闭，登录未完成。", err=True)
                return 5
            if not pages:
                try:
                    page = ctx.new_page()
                    page.goto("https://www.xiaohongshu.com/explore",
                              wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
            else:
                page = pages[0]
            try:
                # 有 web_session、页面不是登录墙/风控页，才算登录成功
                st = page_state(page)
                if st == "blocked":
                    log("小红书返回安全限制（IP 风险），请切换网络后重试扫码。", err=True)
                    return 2
                if is_logged_in(ctx) and st != "login" and "website-login" not in (page.url or ""):
                    cookies = []
                    try:
                        cookies = ctx.cookies()
                    except Exception:
                        pass
                    if write_back_to:
                        if write_back_cookies(p, write_back_to, cookies, launch_kwargs):
                            log("登录态已回写到 %s" % write_back_to)
                        else:
                            log("登录态仅保存在 %s" % profile, err=True)
                    if cache_cookies:
                        save_cookies(ctx)
                    log("登录成功，登录态已保存。")
                    progress("login", detail="ok")
                    return 0
            except Exception:
                pass
            waited = int(time.time() - start)
            if waited - last_tick >= 15:
                log("  等待扫码…（已等待 %ss / %ss）" % (waited, timeout))
                progress("login", detail="waiting-scan", waited=waited)
                last_tick = waited
            time.sleep(1)
        log("等待登录超时，请重试。", err=True)
        return 5
    finally:
        safe_close(ctx)


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


class _HttpResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self._body = body

    @property
    def ok(self):
        return 200 <= self.status < 300

    def body(self):
        return self._body


class _UrllibRequest:
    """让 download() 能在纯 HTTP 模式下复用：只实现 ctx.request.get。"""

    def __init__(self, cookie_header=""):
        self.cookie_header = cookie_header
        self.request = self  # 复用 download() 里的 ctx.request.get

    def get(self, url, timeout=60000, headers=None):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers=self._headers(headers))
        try:
            with urllib.request.urlopen(req, timeout=max(1, timeout / 1000)) as resp:
                return _HttpResponse(resp.status, dict(resp.headers), resp.read())
        except urllib.error.HTTPError as e:
            return _HttpResponse(e.code, dict(e.headers or {}), e.read())

    def _headers(self, extra):
        base = {
            "User-Agent": UA,
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
        }
        if self.cookie_header:
            base["Cookie"] = self.cookie_header
        if extra:
            base.update({k: v for k, v in extra.items() if k.lower() != "cookie"})
        return base


def save_cookies(ctx):
    """把浏览器 Cookie 缓存到 ~/.xhs_cookies.json（0600），供 --http 复用。"""
    try:
        cookies = [c for c in ctx.cookies()
                   if "xiaohongshu.com" in (c.get("domain") or "")
                   or "rednote.com" in (c.get("domain") or "")]
        if not cookies:
            return False
        tmp = COOKIE_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cookies, fh, ensure_ascii=False)
        os.replace(tmp, COOKIE_CACHE)
        os.chmod(COOKIE_CACHE, 0o600)
        return True
    except Exception:
        return False


def cookie_header_from_cache():
    try:
        with open(COOKIE_CACHE, encoding="utf-8") as fh:
            cookies = json.load(fh)
    except Exception:
        return ""
    seen, parts = set(), []
    for c in cookies:
        name = c.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        parts.append("%s=%s" % (name, c.get("value", "")))
    return "; ".join(parts)


def _note_from_state(state, note_id):
    note_map = ((state or {}).get("note") or {}).get("noteDetailMap") or {}
    item = note_map.get(note_id)
    if not item:
        for key, value in note_map.items():
            item, note_id = value, key
            break
    card = (item or {}).get("note") or {}
    if not card:
        return None
    title = (card.get("title") or "").strip()
    desc = card.get("desc") or ""
    if not title:
        title = desc.strip().split("\n")[0][:40] or "小红书笔记"
    images = []
    for im in card.get("imageList") or []:
        url = (im or {}).get("urlDefault") or (im or {}).get("url") or ""
        if url:
            images.append({"url": url.replace("http://", "https://", 1)})
    return {
        "title": title,
        "desc": desc,
        "author": ((card.get("user") or {}).get("nickname")) or "",
        "time": card.get("time") or 0,
        "images": images,
    }


def http_extract(url, note_id):
    """不启动浏览器：带 Cookie 直接 GET 详情页并解析 __INITIAL_STATE__。"""
    import urllib.error
    import urllib.request

    cookie_header = cookie_header_from_cache()
    if not cookie_header:
        raise RuntimeError("没有 Cookie 缓存，请先跑一次浏览器模式（会自动缓存）")
    headers = {
        "User-Agent": UA,
        "Cookie": cookie_header,
        "Referer": "https://www.xiaohongshu.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "identity",
        "Upgrade-Insecure-Requests": "1",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            html = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s" % e.code)
    if any(k in html for k in BLOCKED_MARKERS):
        raise RuntimeError("被风控拦截（300012）")
    if "noteDetailMap" not in html:
        if "请先登录" in html or "website-login" in html:
            raise RuntimeError("登录态失效")
        raise RuntimeError("页面里没有 noteDetailMap（可能需要登录或链接无效）")
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not match:
        match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*\})\s*</script>", html, re.S)
    if not match:
        raise RuntimeError("未找到 __INITIAL_STATE__")
    raw = match.group(1)
    # 页面里混着 JS 字面量：undefined / new Map([]) / new Set([])，先规整成合法 JSON
    raw = re.sub(r"(?<=[:,\[])undefined(?=[,}\]])", "null", raw)
    raw = re.sub(r"new Map\((?:\[\])?\)", "{}", raw)
    raw = re.sub(r"new Set\((?:\[\])?\)", "[]", raw)
    try:
        state = json.loads(raw, strict=False)
    except Exception as e:
        raise RuntimeError("解析 __INITIAL_STATE__ 失败：%s" % e)
    note = _note_from_state(state, note_id)
    if not note or not note["images"]:
        raise RuntimeError("未能从页面数据中提取到笔记/图片")
    return note


def download(ctx, imgs, folder, convert=True, retries=3):
    """按顺序下载全部图片，返回 (saved, failed)。"""
    saved, failed = [], []
    total = len(imgs)
    for i, im in enumerate(imgs, 1):
        url = (im.get("url") if isinstance(im, dict) else im) or ""
        if not url:
            failed.append({"index": i, "url": "", "error": "图片地址为空"})
            log("  [%s/%s] 跳过：图片地址为空" % (i, total))
            continue
        try:
            body = fetch_image(ctx, url, attempts=retries)
        except Exception as e:
            failed.append({"index": i, "url": url, "error": str(e)})
            log("  [%s/%s] 下载失败: %s" % (i, total, e), err=True)
            progress("download", saved=len(saved), total=total, failed=len(failed))
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
        log("  [%s/%s] %s.%s (%sB)" % (i, total, name, ext, len(body)))
        progress("download", saved=len(saved), total=total, failed=len(failed))
    return saved, failed


def chrome_user_agent():
    """用本机 Chrome 的真实版本号拼 UA，避免 HeadlessChrome。"""
    import subprocess
    for path in CHROME_PATHS:
        if not os.path.exists(path):
            continue
        try:
            out = subprocess.check_output([path, "--version"], stderr=subprocess.DEVNULL,
                                          timeout=10).decode()
            version = out.strip().split()[-1]
            if re.match(r"^\d+\.", version):
                return UA_TEMPLATE % version
        except Exception:
            continue
    return None


def build_launch_kwargs(stealth=True):
    """构造启动参数；stealth=True 时去掉自动化标记并伪装成真实 Chrome。"""
    kwargs = dict(
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        args=["--no-first-run", "--no-default-browser-check"],
    )
    if stealth:
        kwargs["ignore_default_args"] = ["--enable-automation"]
        kwargs["args"] += ["--disable-blink-features=AutomationControlled"]
        ua = chrome_user_agent()
        if ua:
            kwargs["user_agent"] = ua
    return kwargs


def launch_context(p, profile, headless, launch_kwargs, stealth=True):
    """启动浏览器：先清理 stale 锁，优先系统 Chrome，其次 playwright chromium。"""
    busy = clear_stale_locks(profile)
    if busy:
        sys.exit(
            "登录态目录正被另一个浏览器实例占用：%s\n"
            "占用进程 pid=%s 仍存活，请先关闭该窗口或结束进程后重试。"
            % (profile, busy)
        )
    errors = []
    for channel in ("chrome", None):
        for attempt in (1, 2):
            try:
                kwargs = dict(launch_kwargs)
                if channel:
                    kwargs["channel"] = channel
                ctx = p.chromium.launch_persistent_context(
                    profile, headless=headless, **kwargs)
                if stealth:
                    try:
                        ctx.add_init_script(STEALTH_JS)
                    except Exception:
                        pass
                return ctx
            except Exception as e:
                msg = str(e)
                errors.append(msg)
                if ("ProcessSingleton" in msg or "SingletonLock" in msg
                        or "already in use" in msg.lower()):
                    # 第一次失败再清一次锁重试；仍失败才报占用
                    clear_stale_locks(profile)
                    if attempt == 1:
                        log("检测到锁冲突，已清理并重试启动。", err=True)
                        continue
                    sys.exit(
                        "登录态目录启动失败：%s\n"
                        "已清理 stale 锁但仍报占用；原始错误：%s\n"
                        "如仍有残留 Chrome，请手动关闭后重试。" % (profile, msg)
                    )
                break
    sys.exit(
        "无法启动浏览器：既没有系统 Chrome，也没有 playwright 的 chromium。\n"
        "请安装 Google Chrome（https://www.google.com/chrome/）后重试；\n"
        "或手动下载 chromium：python -m playwright install chromium。\n"
        f"原始错误：{errors[-1]}"
    )


def fail(code, message):
    progress("error", code=code, message=message)
    log(message, err=True)
    sys.exit(code)


def deliver(note, note_id, out_dir, args, ctx, url):
    """统一的产出流程：probe / 建目录 / 下载 / 写 metadata。"""
    note_id = note_id or note.get("id")
    title = note["title"]
    image_count = len(note["images"])

    if args.probe:
        log("probe OK：标题=%s | 作者=%s | 图片数=%s" %
            (title, note["author"], image_count))
        progress("done", stage_detail="probe", title=title,
                 image_count=image_count)
        return 0

    folder = choose_folder(out_dir, title, note_id)
    if os.path.basename(folder) != safe_name(title):
        log("同名目录已被占用，本次改存：%s" % os.path.basename(folder))
    os.makedirs(folder, exist_ok=True)

    log("标题: %s" % title)
    log("作者: %s" % note["author"])
    log("图片数: %s" % image_count)
    log("保存到: %s" % folder)
    progress("download", folder=folder, title=title,
             image_count=image_count, saved=0, failed=0)

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
        "image_count": image_count,
        "saved_count": len(saved),
        "failed_count": len(failed),
        "saved": saved,
        "failed": failed,
        "url": url,
    }
    with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 交付以 metadata.json 为准：先落盘、再打完成标记，最后才关浏览器
    log("完成：共 %s 张，成功 %s 张，失败 %s 张；元数据见 metadata.json" %
        (image_count, len(saved), len(failed)))
    progress("done", stage_detail="complete", folder=folder, title=title,
             image_count=image_count, saved=len(saved), failed=len(failed))
    if failed:
        for item in failed:
            log("  - 第 %s 张：%s" % (item["index"], item["error"]), err=True)
        fail(4, "部分图片未下载成功，请检查网络后重跑（重跑会补齐并清理旧图）。")
    return 0


def main():
    global PROGRESS_PATH
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="小红书笔记抓取与图片下载")
    ap.add_argument("target", nargs="?", help="笔记链接、App 分享文案或笔记 ID")
    ap.add_argument("--login", action="store_true",
                    help="只做登录：打开一次可见窗口扫码，成功后保存登录态并退出")
    ap.add_argument("--probe", action="store_true",
                    help="只体检：检查登录态/笔记可浏览/图片数，不下载")
    ap.add_argument("--out", default=None,
                    help="输出目录；指定后图片直接放进去，不指定则自动建 <标题>_<ID前8位>")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="登录态持久化目录")
    ap.add_argument("--isolated-profile", action="store_true",
                    help="复制一份临时 profile（并发安全，结束不自动删除）")
    ap.add_argument("--save-login-to", default=None,
                    help="登录成功后写回的 profile（默认 %s）" % DEFAULT_PROFILE)
    ap.add_argument("--no-write-back", action="store_true",
                    help="使用非默认 profile 登录时不写回默认目录")
    ap.add_argument("--no-convert", action="store_true", help="不转 jpg，保留 webp")
    ap.add_argument("--headed", action="store_true",
                    help="调试用：直接打开可见窗口（默认全程无头）")
    ap.add_argument("--wait", type=int, default=30, help="等待详情页加载的秒数（默认 30）")
    ap.add_argument("--login-timeout", type=int, default=90,
                    help="登录模式下最长等待秒数（默认 90）")
    ap.add_argument("--retries", type=int, default=3, help="单张图片下载重试次数（默认 3）")
    ap.add_argument("--no-stealth", action="store_true",
                    help="关闭反自动化伪装（排查兼容性问题时用）")
    ap.add_argument("--http", action="store_true",
                    help="优先用 HTTP 直连（不启动浏览器，失败再回退浏览器）")
    ap.add_argument("--http-only", action="store_true",
                    help="只用 HTTP 直连，失败不启动浏览器（需先有一次浏览器运行缓存 Cookie）")
    ap.add_argument("--no-save-cookies", action="store_true",
                    help="不把浏览器 Cookie 缓存到 ~/.xhs_cookies.json")
    ap.add_argument("--progress-file", default=None,
                    help="把当前阶段原子写入该文件，供外部轮询")
    args = ap.parse_args()

    if args.progress_file:
        PROGRESS_PATH = os.path.abspath(os.path.expanduser(args.progress_file))

    stealth = not args.no_stealth
    launch_kwargs = build_launch_kwargs(stealth)
    base_profile = os.path.expanduser(args.profile)
    profile = base_profile
    if args.isolated_profile:
        profile = prepare_isolated_profile(base_profile)
        log("使用隔离 profile：%s" % profile)

    if args.login:
        write_back_to = None
        if not args.no_write_back:
            target = os.path.expanduser(args.save_login_to or DEFAULT_PROFILE)
            if os.path.abspath(target) != os.path.abspath(profile):
                write_back_to = target
        os.makedirs(profile, exist_ok=True)
        try:
            os.chmod(profile, 0o700)
        except OSError:
            pass
        with sync_playwright() as p:
            code = login_flow(p, profile, launch_kwargs, args.login_timeout, write_back_to,
                              stealth=stealth, cache_cookies=not args.no_save_cookies)
        if args.isolated_profile:
            log("隔离 profile 保留在 %s（可手动删除）。" % profile)
        sys.exit(code)

    if not args.target:
        ap.error("缺少 target：请提供笔记链接/分享文案/ID，或使用 --login")

    try:
        url = note_url(args.target)
    except ValueError as e:
        fail(1, str(e))

    out_dir = os.path.expanduser(args.out or os.getcwd())
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(profile, exist_ok=True)
    try:
        os.chmod(profile, 0o700)
    except OSError:
        pass

    note_id = note_id_from(url)

    if (args.http or args.http_only) and not args.headed:
        if not os.path.isfile(COOKIE_CACHE):
            if args.http_only:
                fail(2, "没有 Cookie 缓存：请先跑一次浏览器模式（成功后会自动缓存到 %s）" % COOKIE_CACHE)
            log("没有 Cookie 缓存，跳过 HTTP 模式，改用浏览器。")
        else:
            try:
                note = http_extract(url, note_id)
                log("HTTP 模式成功：未启动浏览器。")
                progress("done", stage_detail="http", title=note["title"],
                         image_count=len(note["images"]))
                deliver(note, note_id, out_dir, args,
                        _UrllibRequest(cookie_header_from_cache()), url)
                return 0
            except SystemExit:
                raise
            except Exception as e:
                log("HTTP 模式失败：%s" % e, err=True)
                if args.http_only:
                    fail(2, "HTTP 模式失败：%s" % e)

    progress("launch", url=url, profile=profile, mode="probe" if args.probe else "fetch")

    with sync_playwright() as p:
        ctx = launch_context(p, profile, headless=not args.headed,
                             launch_kwargs=launch_kwargs, stealth=stealth)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            progress("load", url=url)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                fail(2, "打开页面失败：%s" % e)

            state = wait_for_note(page, args.wait)

            if state == "unavailable":
                fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")
            if state == "blocked":
                fail(2, BLOCKED_HINT)

            if state != "loaded" and not args.headed:
                # 默认不打开可见窗口：需要登录/验证时直接退出，由调用方询问用户
                st = page_state(page)
                if st == "blocked":
                    fail(2, BLOCKED_HINT)
                if st == "captcha":
                    fail(5, CAPTCHA_HINT)
                if st == "unavailable":
                    fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")
                if st == "login":
                    fail(5, LOGIN_HINT)
                # unknown：可能只是加载慢，重载一次再判定
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    state = wait_for_note(page, args.wait)
                except Exception:
                    state = "unknown"
                if state == "unavailable":
                    fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")
                if state == "blocked":
                    fail(2, BLOCKED_HINT)
                if state != "loaded":
                    # 无登录态时，未加载出详情页基本就是登录墙没被文案命中
                    if not is_logged_in(ctx):
                        fail(5, LOGIN_HINT)
                    fail(2, "页面加载超时。可加 --headed 打开可见窗口排查。")

            if state != "loaded":
                st = page_state(page)
                if st == "unavailable":
                    fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")
                if st == "blocked":
                    fail(2, BLOCKED_HINT)
                if st == "captcha":
                    log("检测到滑块验证，请在弹出的浏览器窗口中拖动完成验证...")
                else:
                    log("需要登录，请用小红书 App 扫码（窗口已弹出）...")
                state = wait_for_note(page, args.login_timeout, label="等待登录/验证")

            if state == "unavailable":
                fail(3, "笔记不存在、已删除或不可浏览（私密/违规）。请确认链接有效。")
            if state != "loaded":
                fail(2, "等待超时：仍未进入笔记详情页。请重试，并在弹出的窗口中完成扫码/验证。")

            note = extract_note(page, note_id)
            if not note:
                fail(2, "未能提取到笔记内容（可能仍未登录、链接无效或页面结构变化）。")

            if not args.no_save_cookies:
                save_cookies(ctx)
            deliver(note, note_id, out_dir, args, ctx, url)
            return 0
        finally:
            safe_close(ctx)


if __name__ == "__main__":
    main()
