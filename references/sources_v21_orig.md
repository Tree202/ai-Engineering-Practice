# 本次修正依据的来源登记（v2.1，核验日期 2026-09-01）

只登记本轮 26 项修正实际依据的来源；全书 193 条断言的总登记属 v3 议题，未做。

| 修正项 | 依据 | 来源 / 验证方式 |
|---|---|---|
| 12 页 git switch 退出码 1→128 | git 的 `die()`（fatal 前缀）一律以 128 退出 | git 官方错误处理约定；本机 git 实测 `git switch nosuchbranch; echo $?` → 128 |
| 22 页「144=手动终止」→130/143 | 128+N 规则中 N 为信号号：SIGINT=2、SIGTERM=15；信号 16 在 Linux 为 SIGSTKFLT、macOS 为 SIGURG | POSIX 信号表（`man 7 signal`）；限定为 POSIX 环境 |
| 14 页步骤 4 `--staged`→`git status --short` | 步骤 4 时点尚未 `git add`，暂存区为空；`git diff HEAD` 亦不含未跟踪文件 | git 官方文档：`git-diff`、`git-status` |
| 02 页 pytest 收集规则说明 | `python_functions` 默认 `["test"]`；本页配置覆盖为 `["test_*"]` 后演示不再成立 | pytest 官方文档 Conventions for Python test discovery |
| 13 页删除「auto 权限模式」 | 官方权限模式为 default / acceptEdits / plan / bypassPermissions；「auto」是状态栏 auto-accept edits 的显示文案 | Claude Code 官方文档 Permission modes（settings/IAM）；产品内 /help |
| 01/19 页 enforce_admins 前提 | GitHub 分支保护的 required status checks 默认不约束管理员，需 enforce_admins/Do not allow bypassing | GitHub REST/branch protection 文档；教程 19 页自己的真机配置 |
| 20/21/22 页「用例总数门禁=弱门禁」口径 | 数量门禁可挡纯删除，无法挡「删一补一」语义替换（逻辑推演，非外部出处） | 三页交叉核对 + Codex 评审共识 |
| 18 页「分母漏洞」对策改配总数门禁 | 被注释/删除的用例不进入 pytest 结果，不计入 skipped | pytest 行为（收集机制）；逻辑推演 |
| 08/16/18/19/22 页交叉引用页码/节号修正 | 全文检索核对目标页实际内容 | 本地 Grep 逐条验证 |
| myshop 基线 17 passed, 1 skipped | Windows + Python 3.12.10 + pytest 8.4.2 实测复现 | 本机实测（2026-09-01），五次改坏实验红绿计数全部与教程 08 页一致 |
| Python 3.9 EOL 说明 | 3.9 于 2025-10 结束官方支持 | Python 官方版本状态页（devguide/versions） |

## 工具版本（本机验证环境）

- Windows 11 · Python 3.12.10 · pytest 8.4.2 · mypy 1.19.1 · ruff 0.16.3 · git for Windows
- 教程原实测环境（保留于各页页脚）：macOS 15.7.4 · Python 3.9.6 · 同版本三工具
