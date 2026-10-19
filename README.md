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
"$SKILL_DIR/.venv/bin/python" "$SKILL_DIR/scripts/fetch_note.py" "<笔记链接 / 分享文案 / 笔记ID>" --out "<输出目录>"

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
| `target` | 笔记链接 / 分享文案 / 笔记 ID（必填） | — |
| `--out` | 图片输出目录 | 当前目录 |
| `--profile` | 登录态持久化目录 | `~/.xhs_session` |
| `--no-convert` | 不转 jpg，保留 webp | 关 |
| `--headed` | 直接打开可见窗口（默认先无头尝试） | 关 |
| `--wait` | 已登录时等待详情页加载的秒数 | 30 |
| `--login-timeout` | 扫码/验证的最长等待秒数 | 300 |
| `--retries` | 单张图片下载重试次数 | 3 |

产出：`<输出目录>/<标题>_<笔记ID前8位>/`，内含 `01.jpg`、`02.jpg`… 和 `metadata.json`（标题/作者/正文/图片数/失败列表）。重跑会自动清理旧序号图，避免图片变少时残留旧页。

首次运行在需要登录时会弹出可见浏览器窗口，用小红书 App 扫码；登录态保存在 `~/.xhs_session`（权限 700），之后无需重复扫码。

## 退出码

| 码 | 含义 | 建议 |
|---|---|---|
| 0 | 全部成功 | — |
| 1 | 输入无法识别 | 换完整链接或分享文案 |
| 2 | 登录/抓取失败 | 按提示扫码或重试 |
| 3 | 笔记不存在/私密/不可浏览 | 确认链接 |
| 4 | 部分图片未下载成功 | 重跑补齐（会清理旧图） |

## 常见问题

- **提示缺少 playwright**：先跑 `bash scripts/setup.sh`。
- **未登录、只有二维码页**：这是小红书的登录墙，必须扫码登录一次。
- **图片顺序**：脚本按接口 `imageList` 顺序下载，已规避 DOM 顺序错乱问题。
- **原图是 webp**：脚本默认转 jpg（quality 95），加 `--no-convert` 可保留原格式。
- **本机没 Chrome**：脚本会提示安装 Chrome，或手动 `python -m playwright install chromium`（不会自动下载）。
- **提示登录态目录被占用**：上一次运行留下了残留 Chrome，关掉它再重试。
- 更多细节见 `references/notes.md`。
