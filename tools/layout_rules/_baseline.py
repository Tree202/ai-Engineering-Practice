# -*- coding: utf-8 -*-
"""自测样本目录的统一解析 —— 六个检测器共用。

为什么要有这个文件:
    六个 det_*.py 的自测各自写死了作者机器的绝对路径
    (`D:/ext.zhaoliuliu3/Desktop/ai-workflow`)。换台机器,两个直接抛
    OSError、两个静默跳过真值段、其余对照段作废 —— 但退出码全是 0,
    看起来像「全过了」。上一轮修了 check_layout.py 的 `_猜基线()`,
    却没修这一类,第三轮外部评审又把它翻了出来。

    这次按「修一类」处理:所有路径经此解析,缺样本时明说跳过,
    并让调用方能据此决定退出码。

查找顺序:
    1. 环境变量 AIWF_BASELINE(CI 与异机的正规入口)
    2. 仓库同级的 ai-workflow/(作者本机的既有布局)
    3. tools/layout_rules/fixtures/baseline/(随仓库发布的快照,见该目录 README)
都找不到就返回空串,由调用方打印「跳过真值段」。
"""

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))          # …/claude_AI


def 基线目录() -> str:
    """只读原版(未修)的 23 页所在目录;找不到返回空串。"""
    for cand in (os.environ.get("AIWF_BASELINE", ""),
                 os.path.join(os.path.dirname(_ROOT), "ai-workflow"),
                 os.path.join(_HERE, "fixtures", "baseline")):
        if cand and os.path.isdir(cand):
            return cand
    return ""


def 已修目录() -> str:
    """本仓库的 23 页(已修版)。它随仓库走,一定存在。"""
    return os.path.join(_ROOT, "ai-workflow")


def 样本(名: str):
    """返回 (坏样本路径, 好样本路径);坏样本不存在时第一个为空串。"""
    b = 基线目录()
    return (os.path.join(b, 名) if b else ""), os.path.join(已修目录(), 名)
