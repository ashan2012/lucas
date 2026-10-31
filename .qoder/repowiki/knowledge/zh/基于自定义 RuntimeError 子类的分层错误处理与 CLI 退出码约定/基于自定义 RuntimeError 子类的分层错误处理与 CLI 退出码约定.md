---
kind: error_handling
name: 基于自定义 RuntimeError 子类的分层错误处理与 CLI 退出码约定
category: error_handling
scope:
    - '**'
source_files:
    - zige/cli.py
    - zige/ocr.py
    - zige/strokes.py
    - zige/layout.py
    - zige/pipeline.py
---

## 1. 总体方案

本仓库没有引入第三方异常框架，而是采用 **Python 内置 `RuntimeError` 子类 + 顶层 `main()` 集中捕获** 的模式：

- 每个子系统定义一个语义化的错误类（`OcrError`、`StrokeError`、`EmptyCardError`），全部继承自 `RuntimeError`。
- 底层模块抛出这些异常，上层调用方按类型区分并输出到 `sys.stderr`，同时返回不同的非零退出码（2/3/4）给 shell。
- 对“可恢复的失败”（如单个汉字笔顺缺失、封面渲染失败）则通过 `BuildResult.warnings` / `missing` 字段回传，而不是中断流程。

## 2. 关键错误类型与位置

| 异常类 | 定义位置 | 含义 |
|---|---|---|
| `OcrError` | `zige/ocr.py:27` | OCR 后端不可用、Swift 编译失败、Vision 进程超时或返回码非 0、无法读取图片 |
| `StrokeError` | `zige/strokes.py:44` | 笔顺数据下载失败、缓存损坏、离线模式下无本地缓存、stroke JSON 为空 |
| `EmptyCardError` | `zige/layout.py:38` | 解析后未发现任何 pinyin/hanzi 单元格（空卡片） |

此外还使用标准异常：
- `ValueError`：CLI 参数解析失败（`cli.py:31`）、`--only` 未匹配任何词（`pipeline.py:183`）、`export.py` 中无页面可写（`export.py:38`）。
- `FileNotFoundError`：输入图片或 JSON 不存在（`cli.py:138`）。
- `ImportError`：可选依赖 `pypinyin` 缺失时降级（`pinyin_util.py:18-19`）。

## 3. 传播与呈现路径

### 3.1 顶层入口 `cli.main`

`cli.py` 的 `main()` 是唯一的错误出口点，结构如下：

```text
try:
    pipeline.words_from_image() / words_from_json()
except OcrError      → stderr + exit(2)
except EmptyCardError → stderr + exit(3)   # 业务语义错误，区别于系统错误
except FileNotFoundError → stderr + exit(2)
```

构建阶段再包一层：

```text
try:
    pipeline.build(...)
except StrokeError     → stderr + exit(4)   # 资源获取失败
except ValueError      → stderr + exit(2)   # 参数/数据校验失败
```

所有错误统一以 `print(f"error: {exc}", file=sys.stderr)` 形式输出，不打印 traceback，保证 CLI 输出简洁。

### 3.2 OCR 层 (`ocr.py`)

- `_helper_path()`：Swift 编译器缺失、`swiftc` 超时、编译返回码非 0 均包装为 `OcrError`，并用 `from exc` 保留原始异常链。
- `image_size()`：PIL 失败后回退到 `sips`；两者都失败时抛 `OcrError("cannot read image")`。
- `read_image()`：文件不存在直接抛 `OcrError`；`subprocess.TimeoutExpired` 包装为 `OcrError("Vision OCR timed out")`；最终 returncode ≠ 0 时汇总 stderr 抛 `OcrError("Vision OCR failed")`。
- 对中文模型首次加载失败的已知问题做了重试（sleep 0.5s × attempt）。