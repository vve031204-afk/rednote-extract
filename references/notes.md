# 小红书抓取经验（references）

本文记录处理小红书笔记时踩过的坑与结论，供执行时参考。抓取细节以这些结论为准，不要重新摸索。

## 1. 访问必须登录

- `www.xiaohongshu.com/explore/<id>` 笔记页对**未登录**用户只显示二维码登录页，正文和图片都不渲染。
- 匿名抓取（WebFetch / curl / 无头浏览器）拿不到笔记内容：
  - 桌面 UA 命中登录墙，页面只剩「登录后推荐更懂你的笔记」。
  - 移动端 UA 会跳到滑块验证页（「拖动箭头完成拼图」）。
  - 第三方阅读代理（如 r.jina.ai）对小红书同样失效。
- **结论**：必须扫码登录一次，登录态保存到本地 profile 目录（默认 `~/.xhs_session`），之后复用。

## 2. 抓取方式

- 用 Playwright 的 `launch_persistent_context(profile, channel="chrome")`，登录态随 profile 持久化。
- 首次无登录态时用 `headless=False` 弹窗扫码；已有登录态可 `headless=True` 静默抓取。
- 本机有 Google Chrome 时优先 `channel="chrome"`（真实浏览器指纹，不易触发验证）；没有则回退 playwright 自带 chromium（需 `playwright install chromium`）。
- 注意：某些机器读取系统 Chrome 的用户配置目录（`~/Library/Application Support/Google/Chrome`）会被安全策略拦截，**无法直接复用已登录的 Chrome**，只能用独立 profile + 扫码。

## 3. 数据提取（关键坑）

- 笔记数据在 `window.__INITIAL_STATE__.note.noteDetailMap[<noteId>].note`。
- `__INITIAL_STATE__` 存在**循环引用**，不能直接 `JSON.stringify`，会抛 `Converting circular structure`。要只用 `evaluate` 在页面里取需要的字段（title / desc / user.nickname / time / imageList）再序列化。
- 图片清单在 `note.imageList`，每项有 `urlDefault`（形如 `http://sns-webpic-qc.xhscdn.com/.../spectrum/1040g0k...`）和 `urlPre`。
- 完整正文在 `note.desc`，含话题标签（`#xxx#`）和换行。

## 4. 图片顺序（关键坑）

- **以 `imageList` 的顺序为准**，这是笔记的真实页码顺序。
- DOM 里 `.swiper-slide img` 的顺序**不可靠**——首图往往是最后一张（轮播/预加载机制导致），直接按 DOM 顺序下载会错序。
- 下载时 `urlDefault` 的 `http://` 换成 `https://`，并带上 `Referer: https://www.xiaohongshu.com/`。

## 5. 图片格式

- 下载得到的原始文件多为 **webp**，直接给用户/归档不够友好，建议用 Pillow 转成 JPG（quality≈95）。
- 尺寸通常是竖版 1080×1800（长图文），转 jpg 时注意别压坏小字。

## 6. 梳理时的约定

- 读图梳理要**忠实于原文**，分点表示，**不增加、不修改、不评价**内容。
- 注意作者表述中的自相矛盾处（例如开篇写「五个维度」但只列了四项），用括号注明即可，不要擅自"修正"。
- 交付时按笔记标题建文件夹，图片按 `01.jpg`、`02.jpg`… 顺序命名，另存一份 `复盘梳理.md`（或按需命名）做文字梳理。
