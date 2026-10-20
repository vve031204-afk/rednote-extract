---
name: xiaohongshu-note-extract
description: 小红书笔记抓取与梳理工具：按顺序下载笔记全部图片（webp 转 jpg）并忠实原文做分点梳理。仅当用户显式选定本 skill 时才调用，不要根据对话内容自动匹配触发。Xiaohongshu note extractor — downloads all note images in order and summarizes the author's content. Invoke only when the user explicitly selects this skill; do not auto-trigger from conversation content.
agent_created: true
disable-model-invocation: true
version: 0.1.8
---

# 小红书笔记抓取与梳理

## 概述

抓取小红书笔记的全部图片并按顺序存档，提取正文文字，并忠实于原文做分点梳理。核心难点（登录墙、图片顺序、webp 转码）已固化到脚本 `scripts/fetch_note.py`，直接复用，不要重新摸索。

## 调用方式

- 仅当用户显式选定本 skill 时执行，不根据对话内容自动匹配触发。
- 适用场景（供用户显式选择时参考）：下载小红书笔记全部图片、提取笔记文字、系统性梳理作者表述并整理成文档。


## 运行纪律（每次抓取都要遵守）

1. **先体检，再抓取**（5–10 秒，只验证不下载）：
   ```bash
   "$SKILL_DIR/.venv/bin/python" -u "$SKILL_DIR/scripts/fetch_note.py" "<链接>" --probe --progress-file /tmp/xhs-progress.json
   ```
   退出码 `0` 才进入正式抓取；`5` 先按第 2 步的登录流程询问用户；`6` 是账号被限制（不要连续重试）；`2`/`3` 按提示处理。
2. **一律 `-u` + 日志落盘，禁止接 `| tail`**。管道会让 Python 全缓冲，进程结束前外部看不到任何输出：
   ```bash
   "$SKILL_DIR/.venv/bin/python" -u "$SKILL_DIR/scripts/fetch_note.py" "<链接>" --out "<输出目录>" \
     --progress-file /tmp/xhs-progress.json > /tmp/xhs.log 2>&1 &
   sleep 15 && tail -30 /tmp/xhs.log
   ```
3. **前台最多等 100 秒**：没结束就转后台，之后每 10–15 秒看一次 `/tmp/xhs.log` 或进度文件，不要空等。
4. **先看产物，再判进程**：`<输出目录>/*/metadata.json` 存在且 `failed_count == 0` 就说明交付已完成；进程是否退出不影响结果。
5. 并发运行或反复重跑时用 `--isolated-profile`，每次复制独立 profile，避免共用目录互相踩锁。
6. 脚本自带 `--progress-file`：阶段依次为 `launch → load → login → download → done`（失败写 `error`），可直接读取判断卡点。

## 执行步骤

> **这是一个完整任务，4 步必须全部走完**：下载完成后要紧接着读图梳理、写成文档并交付，**不要只跑完第 2 步（下载）就停**。

### 1. 初始化环境（直接执行，勿向用户反复确认）

约定 `$SKILL_DIR` 指「本 SKILL.md 文件所在的目录」。首次使用前运行一次环境自检与安装：

```bash
bash "$SKILL_DIR/scripts/setup.sh"
```

- **必须用上面这一条命令一次性完整跑完 setup.sh，不要拆开脚本逐条执行，也不要逐条向用户确认**。
- 脚本是**幂等**的：会自动检测 `.venv`、playwright、pillow、浏览器是否就绪，缺什么装什么，已装则秒级跳过。
- **脚本内部的 playwright / pillow 安装属于标准流程，直接执行即可，不要停下来向用户询问「是否安装 xxx」**（用户选定本 skill 即视为同意安装依赖）。
- 浏览器方面：脚本优先使用系统 Chrome（有 Chrome 就绪则跳过，**不会下载 chromium**）；没有 Chrome 只提示安装、不自动下载，不会卡在下载环节。
- 脚本结束时打印后续抓取命令，照抄执行；若 `.venv` 已存在且依赖齐全，可直接跳到第 2 步。

### 2. 抓取并下载图片

```bash
"$SKILL_DIR/.venv/bin/python" -u "$SKILL_DIR/scripts/fetch_note.py" "<笔记链接 / App 分享文案 / 笔记ID>" \
  --out "<输出目录>" --progress-file /tmp/xhs-progress.json > /tmp/xhs.log 2>&1 &
sleep 15 && tail -30 /tmp/xhs.log
```

Windows（Git Bash / WSL）下 venv 的 python 在 `$SKILL_DIR/.venv/Scripts/python.exe`，其余参数一致。

- 输入支持三种形式：完整笔记链接、App 里复制的分享文案（含 `xhslink.com` 短链）、24 位笔记 ID。**优先用分享链接**：裸 ID 缺少 `xsec_token`，可能打不开。
- **优先用无浏览器模式**：首次用浏览器跑成功后，Cookie 会自动缓存到 `~/.xhs_cookies.json`（0600）；之后加 `--http` 可完全不启动浏览器、不弹窗，遇到 300012 时也先试这条。`--http-only` 表示失败也不回退浏览器。
- **默认全程无头抓取，不会自动打开可见窗口**。如果退出码是 `5`（需要登录或滑块验证），先暂停任务，用提问卡片询问用户「是否现在打开 Chrome 扫码登录」；用户同意后再运行：
  ```bash
  "$SKILL_DIR/.venv/bin/python" "$SKILL_DIR/scripts/fetch_note.py" --login
  ```
  该命令只做登录：打开一次可见窗口，检测到登录成功后**立即关闭**并保存登录态；随后重新执行第 2 步的抓取命令（仍为无头）。用户拒绝则停止任务并说明原因，不要擅自开窗口。
- `--headed` 仅用于调试排查，默认不要使用。
- `--out` 指定**用户选定的项目文件夹**：脚本会在它下面建一个**以笔记标题命名的文件夹**（`<标题>/`），图片、`metadata.json`、梳理文档都放在里面；标题重名时才加 `_<ID前8位>` 后缀。用户未指定则用当前目录，同样建 `<标题>/`。**不要用临时目录**，也不要自己拼标题子目录。
- 输出结果：`<输出目录>/<标题>_<笔记ID前8位>/`，内含按顺序命名的 `01.jpg`、`02.jpg`… 以及 `metadata.json`（标题、作者、正文、图片数、失败列表）。
- 登录态持久化在 `~/.xhs_session`，之后无需重复扫码。
- **退出码 `6` 且提示 `300011` 时**：账号被限制，不要连续重试；建议换账号、降频，或用 `--proxy` 换出口 IP。
- **退出码 `2` 且提示 `300012` 时**：v0.1.6 起默认已做反自动化伪装（真实 Chrome 版本 UA、去掉 `--enable-automation`、`navigator.webdriver` 置空、`window.chrome.runtime`、中文语言/时区）。仍被拦时先告诉用户「无头被风控拦了，我开个窗口跑」，再加 `--headed` 重试；headed 也失败再切换网络/代理。
- **看退出码决定下一步**：`0` 全部成功；`1` 输入无法识别；`2` 登录/抓取失败；`3` 笔记不存在或不可浏览；`4` 部分图片失败；`5` 需要登录/人工验证；`6` 账号安全限制（300011，换账号或等 10-30 分钟）（按上一条询问用户后跑 `--login`，登录默认最多等 90 秒，可用 `--login-timeout` 调整）。遇到 `4` 先重跑一次补齐，不要带着缺图继续第 3 步。
- 下载成功后**必须继续执行第 3、4 步**，不要在此停下。

### 3. 读图梳理（忠实原文，必做）

- 用 Read 逐张读取文件夹里的图片，按页码顺序理解全文。
- 分点梳理作者表述，**不增加、不修改、不评价**内容；原文里的例子、数字、措辞全部保留。
- 发现作者表述自相矛盾处（如写「五个维度」却只列四项），用括号注明，不要擅自修正。
- **思维导图 / 流程图 / 表格截图**：这类图是高密度文字，必须转写而不是一句带过。按层级写成嵌套列表（中心主题 → 一级分支 → 二级节点），保留节点原文、箭头方向和连线关系；不概括、不合并同义节点、不评价。字号太小看不清时，用 Pillow 把图放大 2 倍或切成 2×2 四块分别读，仍无法辨认的词写 `（原文不清：xxx）`。交付时单独标注，如 `图 3（思维导图）转写如下：`。
- 把梳理结果写成 Markdown 文件（如 `复盘梳理.md`）放到同一文件夹，并在开头标注标题、作者、页数。

### 4. 交付

- 把图片文件夹与梳理文档一并交付给用户。
- 简要说明：笔记标题、作者、图片数量、梳理的章节结构。

## 注意事项

- 图片顺序以接口 `imageList` 为准，不要信任页面 DOM 的图片顺序（可能错序）。
- 下载得到的原图多为 webp，脚本默认已转 jpg；如需保留原格式，加 `--no-convert`。
- 若脚本提示缺少 playwright，先跑 `setup.sh`。
- 详细坑与结论见 `references/notes.md`，遇到异常时优先查阅。

## 跨工具复用（Codex / DeepSeek Harness 等）

- 本 skill 的 `SKILL.md` 使用标准 `name` + `description` frontmatter，脚本为纯 Python + Playwright，无任何工具特有依赖。
- 只要把本目录（或软链接）放到目标工具的 skills 扫描目录即可被识别：
  - Codex CLI：`~/.agents/skills/` 或项目 `.agents/skills/`
  - DeepSeek Harness：`~/.dsh/skills/` 或 `~/.agents/skills/`
- 在新电脑上：拷贝本目录 → 运行 `scripts/setup.sh` → 首次扫码登录一次，即可使用。
