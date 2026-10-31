---
kind: logging_system
name: 基于 print(sys.stderr) 的极简日志输出
category: logging_system
scope:
    - '**'
source_files:
    - zige/cli.py
    - zige/pipeline.py
---

## 1. 使用的系统/方案

仓库没有引入任何第三方日志框架（如 `logging`、`loguru`、`structlog`），也没有自定义 logger 模块。所有“日志”输出均通过 Python 标准库的 `print(..., file=sys.stderr)` 完成，属于最基础的 stderr 直写模式。

## 2. 关键文件与位置

- `zige/cli.py`：CLI 入口，几乎所有用户可见的输出（错误、警告、进度）都直接 `print` 到 stderr。
- `zige/pipeline.py`：核心构建流程 `build()` 接受一个可注入的 `log: Callable[[str], None]` 参数，默认值为 `lambda msg: print(msg, file=sys.stderr)`，内部用带阶段编号的消息（如 `[1/4] fetching stroke data...`、`[2/4] rendering word pages ...`、`[3/4] rendering single-character pages ...`、`[4/4] writing index ...`）打印进度。
- `zige/__main__.py`、`zige/export.py`、`zige/draw.py`、`zige/strokes.py`、`zige/ocr.py`、`zige/layout.py`、`zige/config.py`、`zige/fonts.py`、`zige/pinyin_util.py`：未出现 `import logging` 或 `from logging`，也未见结构化字段。

## 3. 架构与约定

- **无集中式 Logger**：不存在 logger 初始化、handler 配置、formatter 定义等任何日志基础设施代码。
- **stderr 作为唯一 sink**：所有非交互式输出统一写入 `sys.stderr`，stdout 保留给纯数据输出（例如 OCR 调试文本在 `cli.py` 中通过 `print(ocr.read_text_debug(args.image))` 输出到 stdout）。
- **错误/警告区分靠前缀**：CLI 中以 `error:` 前缀表示异常（`print(f"error: {exc}", file=sys.stderr)`），以 `! ` 前缀表示警告（`print(f"  ! {warning}", file=sys.stderr)`），由调用方自行决定消息语义，而非通过日志级别区分。
- **进度消息带阶段编号**：`pipeline.build()` 使用 `[N/4]` 形式的固定格式标记四个阶段，便于从终端快速定位当前步骤。
- **可注入 log 回调**：`build()` 的 `log` 参数允许测试或上层替换默认实现，但仓库内只有 CLI 调用它时走默认 lambda，未见外部覆盖。

## 4. 约定与约束

- **观察到的约定**：
  - 所有面向用户的诊断信息一律 `print(..., file=sys.stderr)`，不混入 stdout。
  - 异常路径统一以 `error:` 开头；可选性失败以 `! ` 开头。
  - 长流程进度采用 `[x/y]` 阶段编号 + 简短描述。
  - 没有日志级别（DEBUG/INFO/WARNING/ERROR 等）的概念，也没有日志开关或过滤逻辑。
  - 没有结构化字段（无 JSON 日志、无请求 ID、无时间戳、无线程/进程标识）。
- **无强制规则**：仓库中没有 lint 规则、文档或类型注解强制要求使用某种日志方式；`logging` 模块未被导入，因此该约束是事实上的现状而非显式规范。

综上，本仓库的“日志系统”就是 Python 内置的 `print` 到 stderr，没有任何框架、级别、结构化字段或集中路由。