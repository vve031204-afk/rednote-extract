---
name: xiaohongshu-note-extract
description: 小红书笔记抓取与梳理工具：按顺序下载笔记全部图片（webp 转 jpg）并忠实原文做分点梳理。仅当用户显式选定本 skill 时才调用，不要根据对话内容自动匹配触发。Xiaohongshu note extractor: downloads all note images in order and summarizes the author's content. Invoke only when the user explicitly selects this skill; do not auto-trigger from conversation content.
agent_created: true
disable-model-invocation: true
version: 0.0.7
---

# 小红书笔记抓取与梳理

## 概述

抓取小红书笔记的全部图片并按顺序存档，提取正文文字，并忠实于原文做分点梳理。核心难点（登录墙、图片顺序、webp 转码）已固化到脚本 `scripts/fetch_note.py`，直接复用，不要重新摸索。

## 调用方式

- 仅当用户显式选定本 skill 时执行，不根据对话内容自动匹配触发。
- 适用场景（供用户显式选择时参考）：下载小红书笔记全部图片、提取笔记文字、系统性梳理作者表述并整理成文档。

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
"$SKILL_DIR/.venv/bin/python" "$SKILL_DIR/scripts/fetch_note.py" "<笔记链接或ID>" --out "<输出目录>"
```

Windows（Git Bash / WSL）下 venv 的 python 在 `$SKILL_DIR/.venv/Scripts/python.exe`，其余参数一致。

- 脚本会判断登录态：已有登录态则静默抓取；未登录会弹出一个可见浏览器窗口，提示用户用小红书 App 扫码，随后自动继续。
- `--out` 指定**用户要保存图片的目录**：用户指定就用用户指定的；用户未指定则用当前工作目录；**不要用临时目录**。
- 输出结果：在输出目录下按笔记标题创建文件夹，内含按顺序命名的 `01.jpg`、`02.jpg`… 以及 `metadata.json`（标题、作者、正文、图片数）。
- 登录态持久化在 `~/.xhs_session`，之后无需重复扫码。
- 下载成功后**必须继续执行第 3、4 步**，不要在此停下。

### 3. 读图梳理（忠实原文，必做）

- 用 Read 逐张读取文件夹里的图片，按页码顺序理解全文。
- 分点梳理作者表述，**不增加、不修改、不评价**内容；原文里的例子、数字、措辞全部保留。
- 发现作者表述自相矛盾处（如写「五个维度」却只列四项），用括号注明，不要擅自修正。
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
