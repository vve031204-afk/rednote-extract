# xiaohongshu-note-extract

抓取小红书（xiaohongshu.com）笔记的全部图片并按顺序存档（webp 转 jpg），提取正文文字，并忠实原文做分点梳理。

核心难点（登录墙、图片顺序、webp 转码）已固化到脚本里，一次装好，反复使用。

## 目录结构

```
xiaohongshu-note-extract/
├── SKILL.md                  # skill 主手册（标准 name+description，跨工具通用）
├── README.md                 # 本文件
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

脚本会：检测 Python3 → 建独立 `.venv` → 装 playwright + pillow → 检测本机浏览器（没有 Chrome 则自动装 playwright 自带 chromium）。结束时打印出后续抓取命令。

## 使用

```bash
# macOS / Linux
"$SKILL_DIR/.venv/bin/python" "$SKILL_DIR/scripts/fetch_note.py" "<笔记链接或ID>" --out "<输出目录>"

# Windows（Git Bash / WSL）
"$SKILL_DIR/.venv/Scripts/python.exe" "$SKILL_DIR/scripts/fetch_note.py" "<笔记链接或ID>" --out "<输出目录>"
```

参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `target` | 笔记 URL 或笔记 ID（必填） | — |
| `--out` | 图片输出目录 | 当前目录 |
| `--profile` | 登录态持久化目录 | `~/.xhs_session` |
| `--no-convert` | 不转 jpg，保留 webp | 关 |

产出：在输出目录下按笔记标题创建文件夹，内含 `01.jpg`、`02.jpg`… 和 `metadata.json`（标题/作者/正文/图片数）。

首次运行会弹出一个可见浏览器窗口，用小红书 App 扫码登录；登录态保存在 `~/.xhs_session`，之后无需重复扫码。

## 常见问题

- **提示缺少 playwright**：先跑 `bash scripts/setup.sh`。
- **未登录、只有二维码页**：这是小红书的登录墙，必须扫码登录一次。
- **图片顺序**：脚本按接口 `imageList` 顺序下载，已规避 DOM 顺序错乱问题。
- **原图是 webp**：脚本默认转 jpg（quality 95），加 `--no-convert` 可保留原格式。
- **本机没 Chrome**：setup.sh 会自动安装 playwright 的 chromium。
- 更多细节见 `references/notes.md`。
