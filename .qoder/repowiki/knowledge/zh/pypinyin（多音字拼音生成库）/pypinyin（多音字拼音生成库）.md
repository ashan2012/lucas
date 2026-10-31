---
kind: external_dependency
name: pypinyin（多音字拼音生成库）
slug: pypinyin
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
---

项目用 pypinyin 根据词语上下文重新生成拼音，确保声调正确、多音字按词组判定（如 `贺卡` → `hè kǎ`、`可乐` → `kě lè`、`花朵` → `huā duǒ`）。OCR 的拼音只作为弱提示：只有当它恰好是该字的合法读音且与词典结果不同时才会采纳，并在命令行打印 `!` 提示。

注意：pypinyin 是拼音来源，不是 OCR 来源——项目刻意绕过 Vision 的拼音输出。