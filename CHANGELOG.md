# 更新日志（Changelog）

## v0.1.0

首个版本，当前包含以下能力：

- **抓取与下载**：登录小红书后按 `imageList` 顺序下载笔记全部图片，webp 转 jpg，输出 `01.jpg`、`02.jpg`… 与 `metadata.json`。
- **登录态**：检测登录态；未登录自动弹出扫码窗口，登录态持久化复用。
- **读图梳理**：由 AI 逐张读图，忠实原文分点梳理并写成 Markdown 文档。
- **环境自检**：`setup.sh` 幂等无交互，优先使用系统 Chrome，不自动下载 chromium。
- **跨工具复用**：标准 `SKILL.md` 格式，可在 WorkBuddy / Codex / DeepSeek Harness 中使用。

> 说明：本工具目前仍处于 0→1 的早期阶段，尚未形成稳定版本历史；自 v0.1.0 起正式记录版本。
