DynaCalendar — Windows 演示包（免安装）
======================================

双击 DynaCalendar.exe 即可运行。不需要安装 Python 或 Node，也不需要联网。

和“干净版”的区别：这个包里的 DynaCalendar-data\ 已经带了一份演示数据库，
打开就是导入好的课表和排好的学习计划，方便直接看效果。想做一次完整的
“导入 → 建目标 → 拖材料 → 自动排程”流程，见下面的“手动导入示例”。

本包由 GitHub Actions 每次构建时重新生成，演示数据以构建当天为基准。


第一次运行
----------
1. 双击 DynaCalendar.exe。
2. Windows 可能提示“Windows 已保护你的电脑”——因为 exe 没有代码签名，
   点“更多信息” → “仍要运行”。
3. 会弹出一个黑色控制台窗口，浏览器自动打开 http://127.0.0.1:8000
   （没自动打开就手动访问；8000 被占用时会自动改用 8001、8002……）。
4. 关闭那个控制台窗口即停止程序。


演示数据里有什么
----------------
- 目标 MATH2001，deadline 取自 EXAM 事件（12/18）
- 已导入的课表：Gym、MATH2001 Lecture、CS1010 Lecture 等
- 今天已经排好的 3 个学习任务（50 分钟一格，自动避开上课时间）
- 勾选任意任务：变绿，并立即生成 +1/+3/+7/+14 天的 4 个复习任务
- 拖动任务到别的时间：会被“钉住”，Re-plan 也不会移动它
- 演示数据的时间按 UTC+8 生成（可在构建时用 demo_utc_offset_minutes 改）


数据放在哪
----------
程序旁边的 DynaCalendar-data\：

  dynacalendar.db      SQLite 数据库（演示数据就在这里）
  materials\           上传原件与转换后的文本
  samples\             示例 .ics 与材料

想从空库开始：关掉程序，删掉整个 DynaCalendar-data\ 文件夹（保留 exe），
再双击 exe —— 会重新建库并复制 samples。

存放位置需要可写：桌面、下载、文档都可以，不要放在 C:\Program Files 下。


手动导入示例（可选，空库时用）
------------------------------
1. Calendar 页 → Import .ics → DynaCalendar-data\samples\sample_calendar.ics
   应看到 “Imported 10 events” 和 MATH2001 Final Exam 的横幅。
2. 点横幅上的 Create goal —— deadline 就是考试日期。
3. Inbox 页 → 选中该目标 → 把 samples\materials 里的 4 个 .md 拖进去，
   每个文件都会被转换、打标签、估算工时。
4. Week 页 —— 学习任务落在课程之外的空档里，不会和已导入的课重叠。
5. 勾一个任务，看 +1/+3/+7/+14 天的复习卡片出现；再问一句
   “what's due this week?”，即使没有配置大模型也能基于日历回答。


说明
----
- 单用户本地应用：只监听 127.0.0.1，不会暴露到局域网。
- 大模型可选：不配置时所有 AI 能力都有确定性兜底，完全离线可用。
  要接的话，点右上角齿轮，填任意 OpenAI 兼容的 base URL 和模型。
- 排程全部由确定性的 Python 引擎完成，模型只负责打标签、去重和聊天。
- 源码：https://github.com/lunnua154-wq/DynamicCalendar
