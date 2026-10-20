# xiaohongshu-note-extract

抓取小红书（xiaohongshu.com）笔记的全部图片并按顺序存档（webp 转 jpg），提取正文文字，并忠实原文做分点梳理。

核心难点（登录墙、图片顺序、webp 转码）已固化到脚本里，一次装好，反复使用。

## 目录结构

```
xiaohongshu-note-extract/
├── SKILL.md                  # skill 主手册（标准 name+description，跨工具通用）
├── README.md                 # 本文件
├── requirements.txt          # Python 依赖（锁定大版本）
├── references/
│   └── notes.md              # 踩坑经验（登录墙、图片顺序、循环引用等）
└── scripts/
    ├── setup.sh              # 环境自检 + 安装（建独立 venv、装依赖、检测浏览器）
    └── fetch_note.py         # 抓取 + 下载 + 转 jpg
tests/
    └── test_fetch_note.py    # 离线回归测试（不联网、不启动真实浏览器）
```

## 安装（按你用的工具选一条）

三种工具的 skill 都是「扫描本地目录」，把仓库 clone 到对应目录即可被识别：

```bash
# WorkBuddy
git clone https://github.com/vve031204-afk/xiaohongshu-note-extract.git ~/.workbuddy/skills/xiaohongshu-note-extract

# Codex CLI（也可用项目内的 .agents/skills/）
git clone https://github.com/vve031204-afk/xiaohongshu-note-extract.git ~/.agents/skills/xiaohongshu-note-extract

# DeepSeek Harness（也可用 ~/.agents/skills/ 共享目录）
git clone https://github.com/vve031204-afk/xiaohongshu-note-extract.git ~/.dsh/skills/xiaohongshu-note-extract
```

> 本仓库已公开，任何人可直接 clone，无需登录。

## 首次使用（每台新机器做一次）

```bash
cd <skill目录>
bash scripts/setup.sh
```

脚本会：检测 Python 3.9+ → 建独立 `.venv` → 按 `requirements.txt` 装 playwright + pillow → 检测本机浏览器。**不会自动下载 chromium**：没有 Chrome 时只提示安装，或由你手动执行 `python -m playwright install chromium`。结束时打印出后续抓取命令。

## 使用

```bash
# macOS / Linux
"$SKILL_DIR/.venv/bin/python" -u "$SKILL_DIR/scripts/fetch_note.py" "<笔记链接 / 分享文案 / 笔记ID>" \
  --out "<输出目录>" --progress-file /tmp/xhs-progress.json > /tmp/xhs.log 2>&1 &
sleep 15 && tail -30 /tmp/xhs.log

# Windows（Git Bash / WSL）
"$SKILL_DIR/.venv/Scripts/python.exe" "$SKILL_DIR/scripts/fetch_note.py" "<笔记链接 / 分享文案 / 笔记ID>" --out "<输出目录>"
```

`target` 支持三种形式，**优先用分享链接**（裸 ID 缺少 `xsec_token`，可能打不开）：

- 完整链接：`https://www.xiaohongshu.com/explore/<id>?...`
- App 分享文案：`34 【标题】 😆 http://xhslink.com/a/xxxx`
- 24 位笔记 ID：`66f0c8a1000000001e01abcd`

参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `target` | 笔记链接 / 分享文案 / 笔记 ID（抓取时必填） | — |
| `--login` | 只做登录：打开一次可见窗口扫码，成功后保存登录态并退出 | 关 |
| `--probe` | 只体检：验证登录态/笔记可浏览/图片数，不下载 | 关 |
| `--progress-file` | 把当前阶段原子写入该文件，供外部轮询 | 关 |
| `--isolated-profile` | 复制临时 profile（并发安全，结束保留） | 关 |
| `--save-login-to` | 登录成功后写回的 profile | `~/.xhs_session` |
| `--no-write-back` | 用非默认 profile 登录时不写回默认目录 | 关 |
| `--out` | 项目文件夹；脚本在其下建 `<标题>/` 存放产出 | 当前目录，同样建 `<标题>/` |
| `--profile` | 登录态持久化目录 | `~/.xhs_session` |
| `--no-convert` | 不转 jpg，保留 webp | 关 |
| `--headed` | 调试用：直接打开可见窗口（默认全程无头） | 关 |
| `--wait` | 已登录时等待详情页加载的秒数 | 30 |
| `--login-timeout` | `--login` 模式的最长等待秒数 | 90 |
| `--retries` | 单张图片下载重试次数 | 3 |

产出：在 `--out`（或当前目录）下建 `<标题>/`，内含 `01.jpg`、`02.jpg`…、`metadata.json` 与梳理文档；标题重名时自动改用 `<标题>_<ID前8位>/`。重跑同一篇会复用原目录并清理旧序号图，避免图片变少时残留旧页。

### 运行纪律（避免“看不见状态”）

1. **先体检**：`--probe` 5–10 秒内返回登录态、笔记可浏览性和图片数，`0` 才正式抓取；临时文件用 `$$` 保证唯一（如 `/tmp/xhs-$$.log`），避免并发互相覆盖。
2. **`-u` + 日志落盘，不要接 `| tail`**：管道会让 Python 全缓冲，进程结束前外部零输出。
3. **前台最多等 100 秒**，超时转后台，再轮询日志/进度文件。
4. **先看产物再判进程**：`metadata.json` 存在且 `failed_count == 0` 即已完成，与进程是否退出无关。
5. `--progress-file` 阶段：`launch → load → login → download → done`，失败写 `error`。

**默认全程无头，不会自动弹出浏览器**。需要登录或滑块验证时，脚本以退出码 `5` 结束、不等待；确认要登录后再单独运行：

```bash
python scripts/fetch_note.py --login
```

该命令打开一次可见窗口，登录成功（检测到 `web_session` cookie）后立即关闭并保存登录态到 `~/.xhs_session`（权限 700），之后的抓取仍为无头。

## 退出码

| 码 | 含义 | 建议 |
|---|---|---|
| 0 | 全部成功 | — |
| 1 | 输入无法识别 | 换完整链接或分享文案 |
| 2 | 登录/抓取失败 | 按提示扫码或重试 |
| 3 | 笔记不存在/私密/不可浏览 | 确认链接 |
| 4 | 部分图片未下载成功 | 重跑补齐（会清理旧图） |
| 5 | 需要登录或滑块验证 | 询问用户同意后运行 `--login` |

## 常见问题

- **提示缺少 playwright**：先跑 `bash scripts/setup.sh`。
- **未登录、只有二维码页**：这是小红书的登录墙。脚本会以退出码 `5` 退出，确认后运行 `python scripts/fetch_note.py --login` 扫码，登录态会保存。
- **为什么不再自动弹 Chrome**：默认无头；只有 `--login` 或显式 `--headed` 才会打开可见窗口。
- **报「安全限制 / 300012」**：先加 `--headed` 重试一次。实测同一 IP、同一 cookie 下无头被拦、headed 3 秒通过，说明它更像无头指纹识别而不是 IP 封禁；headed 也失败时再切换网络/代理或手动完成验证。
- **思维导图/表格截图**：图片会照常下载；梳理时按层级转写原文，字号过小可放大或切块读图。
- **图片顺序**：脚本按接口 `imageList` 顺序下载，已规避 DOM 顺序错乱问题。
- **原图是 webp**：脚本默认转 jpg（quality 95），加 `--no-convert` 可保留原格式。
- **本机没 Chrome**：脚本会提示安装 Chrome，或手动 `python -m playwright install chromium`（不会自动下载）。
- **提示登录态目录被占用**：脚本会先自动清理 stale 的 `SingletonLock/Socket/Cookie` 再启动；只有锁指向的 Chrome 进程仍存活时才需要手动关闭。
- **为什么接 `| tail` 看不到输出**：stdio 走管道时 Python 默认全缓冲；用 `-u` 或加 `--progress-file` 即可实时观测。
- 更多细节见 `references/notes.md`。
