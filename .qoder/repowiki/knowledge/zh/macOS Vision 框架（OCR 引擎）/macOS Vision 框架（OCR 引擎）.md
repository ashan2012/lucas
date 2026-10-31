---
kind: external_dependency
name: macOS Vision 框架（OCR 引擎）
slug: macos-vision
category: external_dependency
category_hints:
    - framework_behavior
    - client_constraint
scope:
    - '**'
---

项目通过编译 `zige/swift/vision_ocr.swift` 为本地二进制，调用 macOS 系统自带的 Vision 框架对图片中的汉字进行 OCR。该能力是平台绑定的：仅在 macOS 上可用，依赖 `swiftc`（Xcode Command Line Tools），且识别结果不出本机。

关键约束：
- 汉字识别准确，但拼音声调经常乱码（如 `huà huà` → `hud hud`），因此项目不采用 Vision 的拼音输出，仅取汉字文本。
- 运行时存在偶发失败（`nilError` / `Error loading network`），Swift 端内置三级降级策略（accurate+语言 → fast+语言 → accurate+自动语言），Python 层再重试一次；仍失败则需重跑。
- 构建产物缓存于 `cache/bin/vision_ocr`，避免每次重新编译。