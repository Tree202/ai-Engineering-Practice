# 六条版式规则

每个模块暴露 `check(path, html) -> list[dict]`，由 `../check_layout.py` 驱动。
模块只用标准库，可以单独 `python det_xxx.py` 运行自测。

| 模块 | 抓什么 | 真值验证 |
|---|---|---|
| `det_dangling_node.py` | SVG 里有入边、没出边的非终止节点 | 原版 01 页报出 5b/5c/兜底 三条，已修版 0 条 |
| `det_dangling_edge.py` | 箭头端点附近没有任何节点 | 原版 01 页报出那条停在方框外 10.8px 的箭头 |
| `det_grid_text.py` | grid/flex 容器里混有裸文本节点 | 原版 02 页报出 `.chk li` 七条，已修版 0 条 |
| `det_overflow.py` | 元素坐标跑出 viewBox | 复刻 14 页历史缺陷（y=530 vs 画布高 524）精确报出 |
| `det_collision.py` | 文字压别的方框、方框互相重叠 | — |
| `det_refs.py` | `url(#id)` 指向不存在的 marker、重复 id | — |

**调参不是拍脑袋定的。** 例如 `det_dangling_edge` 先统计了已修版 23 页全部 95 个
带箭头端点到最近节点的距离，分布是 0px×28、2px×9、4px×49、6px×6、8px×1，
而真值缺陷是 10.8px 且垂直偏移 10px —— 容差 8px 正好卡在两者之间。
`det_overflow` 做了变异测试：画布砍 8px 一条不报、砍 40px 全部报出。

**驱动层还做了三处规则收窄**，每一处都由一次对抗复核的证据推出来，
见 `../check_layout.py` 里 `收窄()` 的注释。那是收窄判据，不是白名单。
