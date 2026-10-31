---
kind: external_dependency
name: hanzi-writer-data（汉字笔顺字形数据源）
slug: hanzi-writer-data
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

每个汉字的笔顺 SVG 路径与运笔轨迹（medians）来自开源数据集 hanzi-writer-data（https://github.com/chanind/hanzi-writer-data），字形源自 Make Me a Hanzi / Arphic 字体，遵循 Arphic Public License。

集成方式：首次运行按 Unicode 码点从网络下载对应 JSON，缓存在 `cache/strokes/<hex>.json`，之后可完全离线使用（`--offline`）。`strokes.py` 内嵌一个精简的 SVG 路径解析器，仅支持 hanzi-writer 用到的 `M/L/Q/C/T/Z` 指令集，并将坐标统一做 `y_img = 900 - y_data` 翻转 + 以 `(520, 500)` 为中心的等比缩放，不做逐字归一化以保持字形相对大小。
- GB2312 之外的生僻字可能无数据，渲染时显示提示文字并继续处理其他字，退出码为 0。
- 数据来源地址在 README 中声明，实际下载逻辑见 `strokes.py`。