---
kind: configuration_system
name: 基于 argparse + 模块级常量的轻量配置系统
category: configuration_system
scope:
    - '**'
source_files:
    - zige/config.py
    - zige/cli.py
    - zige/__main__.py
---

## 1. 使用的系统与工具

仓库没有引入任何第三方配置框架（如 Pydantic Settings、dynaconf、pydantic-settings、python-dotenv 等）。配置由两部分组成：
- **运行时 CLI 参数**：通过标准库 `argparse` 在 `zige/cli.py` 中声明。
- **程序内常量/路径**：集中在 `zige/config.py`，以模块级全局常量形式暴露。

没有 `.env`、`.yaml`、`.toml`、`.json` 等外部配置文件；也没有环境变量读取逻辑。所有“可配置项”要么来自命令行，要么直接改源码中的常量。

## 2. 关键文件

- `zige/config.py` — 项目根路径、缓存目录、笔顺数据源 URL、画布尺寸、默认单元格大小与 DPI 缩放等全局常量。
- `zige/cli.py` — `argparse` 参数定义、默认值、参数校验与向 `pipeline.BuildOptions` 的映射。
- `zige/__main__.py` — 入口，仅转发到 `cli.main`。
- `zige/pipeline.py` — 接收 `BuildOptions` 数据类，是 CLI 选项进入业务逻辑的边界。

## 3. 架构与约定

### 3.1 常量层 (`config.py`)

`config.py` 只暴露顶层常量，不封装 getter：

| 常量 | 含义 |
|---|---|
| `PROJECT_DIR` | `Path(__file__).resolve().parent.parent`，即仓库根目录 |
| `CACHE_DIR` / `STROKE_CACHE_DIR` | `cache/` 与 `cache/strokes/` |
| `STROKE_SOURCES` | 两个 hanzi-writer-data CDN 地址模板，按顺序尝试 |
| `CANVAS` / `Y_FLIP` | 笔顺 SVG 画布 1024×1024 及 y 轴翻转偏移 |
| `GLYPH_CENTER_X/Y` / `GLYPH_REFERENCE_EXTENT` | 字形居中校准参数 |
| `DEFAULT_CELL = 200` / `DEFAULT_DPI_SCALE = 2` | 渲染默认值 |

这些常量被 `cli.py`、`strokes.py`、`draw.py`、`layout.py` 等模块直接 `from .config import ...` 使用，形成“单点常量源”。

### 3.2 CLI 参数层 (`cli.py`)

`build_parser()` 集中声明所有 CLI 选项，并给出默认值：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `image` (positional) | 必填之一 | 输入照片 |
| `--from-json` | 无 | 从已解析 JSON 重建 |
| `-o, --out` | `out/<card name>` | 输出目录 |
| `--cell` | `DEFAULT_CELL` (200) | 田字格像素边长 |
| `--scale` | `DEFAULT_DPI_SCALE` (2) | 超采样倍数 |
| `--max-cols` | 7 | 每行最大格子数 |
| `--practice` | 2 | 每个字后的空白练习格数 |
| `--only` | 无 | 指定要渲染的字序号列表 |
| `--no-pdf` | False | 跳过合并 PDF |
| `--no-char-pages` | False | 跳过逐字页 |
| `--offline` | False | 禁用网络下载，仅用缓存 |
| `--workers` | 8 | 并行下载线程数 |
| `--dump-ocr` / `--debug` / `--version` | 调试开关 | |

自定义类型 `_parse_only` 将逗号分隔字符串解析为 `List[int]`，非法值抛出 `argparse.ArgumentTypeError`。

### 3.3 选项传递层 (`pipeline.BuildOptions`)

CLI 最终把参数组装成 `pipeline.BuildOptions` 对象传入 `pipeline.build()`，这是 CLI 与业务逻辑之间的唯一配置接口。该对象包含 `out_dir`、`cell`、`scale`、`max_cols`、`practice_cells`、`make_pdf`、`make_char_pages`、`allow_network`、`workers`、`only` 等字段。

### 3.4 默认输出目录策略

`_default_out()` 根据输入来源决定输出目录名：优先取图片文件名 stem，否则取 `--from-json` 所在父目录名，再过滤非字母数字字符后拼接到 `out/` 下。若两者都为 None，则回退为 `out/card`。

## 4. 约定与约束

- **无外部配置文件**：仓库中没有 `.env`、`.yaml`、`.toml`、`.ini` 等配置加载代码；`config.py` 是唯一的全局配置源，且全部是硬编码常量。
- **无环境变量读取**：未发现对 `os.environ` 或 `os.getenv` 的调用，因此不存在“环境变量覆盖”机制。
- **默认值双处维护**：CLI 默认值引用 `config.DEFAULT_CELL` / `config.DEFAULT_DPI_SCALE`，但 `--cell` 和 `--scale` 的 help 文本也硬编码了 `(default 200)` / `(default 2)`，存在轻微重复（见 `cli.py` 第 49、54 行）。
- **路径一律使用 `pathlib.Path`**：`config.PROJECT_DIR`、`_default_out()`、以及 CLI 中对 `args.image` / `args.from_json` / `args.out` 的处理统一使用 `Path(...).expanduser()`，支持 `~` 展开。
- **离线模式通过 `--offline` 布尔开关控制**：`allow_network = not args.offline`，下游据此决定是否访问 `STROKE_SOURCES` 中的 CDN。
- **错误码约定**：`main()` 返回整数作为退出码：0=成功，2=参数/IO/OCR 错误，3=空卡片，4=笔顺数据错误（见 `cli.py` 第 100–183 行）。
- **依赖注入边界**：除 `config.py` 的全局常量外，所有运行时配置必须通过 `BuildOptions` 显式传入，禁止在子模块中直接读 `sys.argv` 或 `argparse`。

## 5. 总结

这是一个极简的 Python 脚本式配置系统：配置来源只有两处——`zige/config.py` 的模块级常量（路径、URL、画布尺寸、默认值）和 `zige/cli.py` 的 `argparse` 参数。没有分层、没有 schema 校验、没有环境隔离，适合一个面向本地桌面用户的 CLI 工具。扩展新配置项时，应同时更新 `config.py`（如需持久化常量）、`cli.py` 的 `build_parser()` 与 `main()` 中的 `BuildOptions` 构造处。