# 更新日志（Changelog）

## v0.0.7（最新）

**修复「未登录时误判为已加载，只存到 1 张封面图」**

- `fetch_note.py` 的 `is_loaded()` 判据收紧：只认 `#detail-title`（登录后的完整详情页才有），去掉"能取到笔记数据就视为已加载"的兜底。
- 原因：未登录时小红书仍通过 xsec_token 返回「标题 + 封面第一张图」，原兜底逻辑会误判为已加载、跳过扫码，导致只存 1 张图。
- 扫码轮询超时（5 分钟）改为明确报错退出，不再用残缺数据继续往下跑。

## v0.0.6

**修复「卡在安装 playwright chromium」**

- `setup.sh` 不再自动下载 chromium：检测到系统 Chrome 就直接用（跳过），没检测到只提示安装 Chrome，绝不自动下载 120MB。
- 原因：原逻辑「检测 Chrome → 没检测到就自动下载 chromium」在 agent 环境不可靠，加上 agent 会把"下载"停下向用户确认，导致卡在确认环节。
- `fetch_note.py` 无浏览器时的报错改为提示「安装 Google Chrome」。
- `SKILL.md` 明确：用一条命令一次性跑完 setup.sh，不要拆开逐条执行、不要逐条确认。

## v0.0.5

**强调「下载 → 读图梳理 → 交付」是不可跳过的完整流程**

- `SKILL.md` 执行步骤开头新增硬性说明：4 步必须全部走完，不要只跑完下载就停。
- `--out` 明确用用户指定目录（未指定则当前工作目录），不要用临时目录。
- 步骤 3「读图梳理」标注为必做。

## v0.0.4

**修复「卡在确认初始化环境」**

- `SKILL.md` 明确：初始化是标准流程，直接执行，不要停下来向用户询问是否初始化。
- `setup.sh` 幂等化：依赖已装则秒级跳过；pip 加 `--no-input` 无交互；精确浏览器检测（仅真正的 Chrome 才省去 chromium 下载）。

## v0.0.3

**跨工具健壮性加固（保证新电脑/新工具下载后开箱即用）**

- `SKILL.md`：`<skill-dir>` 占位符改为明确 `$SKILL_DIR`（= SKILL.md 所在目录）约定；补充 Windows 下 venv python 路径（`.venv/Scripts/python.exe`）。
- `fetch_note.py`：新增 `launch_context()` 统一封装浏览器启动（Chrome 优先、chromium 兜底、都缺则友好报错）；`download()` 兼容 dict/字符串两种 imageList 项。
- 新增 `README.md`（目录结构、三平台安装命令、首次 setup、参数表、FAQ）。

## v0.0.2

**调整触发方式为「仅显式选定才调用」**

- `description` 移除自动匹配触发词，改为"仅当用户显式选定本 skill 时才调用，不要根据对话内容自动匹配触发"。
- 新增 frontmatter `disable-model-invocation: true`。
- 正文「何时使用」改为「调用方式」。

## v0.0.1

**初始创建**

- 用 skill-creator 初始化 `xiaohongshu-note-extract`。
- `SKILL.md`：标准 name + description，含触发词与执行步骤。
- `scripts/fetch_note.py`：登录态检测 → 扫码 → 按 imageList 顺序下载 → webp 转 jpg → metadata.json。
- `scripts/setup.sh`：环境自检（建 venv、装 playwright+pillow、检测浏览器）。
- `references/notes.md`：踩坑经验（登录墙、图片顺序、循环引用、webp 转 jpg 等）。
- 上传 GitHub 私有仓库（后转公开）。
