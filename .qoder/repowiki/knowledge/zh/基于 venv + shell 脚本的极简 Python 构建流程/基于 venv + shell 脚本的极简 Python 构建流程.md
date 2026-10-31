---
kind: build_system
name: 基于 venv + shell 脚本的极简 Python 构建流程
category: build_system
scope:
    - '**'
source_files:
    - run.sh
    - requirements.txt
    - .gitignore
    - tests/test_zige.py
---

## 1. 使用的系统/方法

仓库没有使用任何传统构建工具（无 Makefile、Dockerfile、CI 配置、setup.py/pyproject.toml）。构建与部署完全依赖两个文件：

- `run.sh` — 启动器，负责创建本地虚拟环境、安装依赖、并执行 `zige` CLI。
- `requirements.txt` — 声明运行时依赖（仅 Pillow ≥10.0 与 pypinyin ≥0.49）。

Python 包通过标准库 `venv` + `pip` 管理，入口为 `python -m zige`（由 `zige/__main__.py` 提供），属于纯脚本型项目，不生成可分发的 wheel/sdist。

## 2. 关键文件

- `run.sh`：唯一构建/启动脚本。
- `requirements.txt`：依赖清单。
- `.gitignore`：忽略 `.venv/`、`cache/`、`out/` 等生成目录。
- `tests/test_zige.py`：唯一的测试文件，未绑定到任何测试命令。

## 3. 架构与约定

- **虚拟环境就地创建**：`run.sh` 在仓库根目录创建 `.venv/`，首次运行检测 `VENV/bin/python` 是否存在，不存在则调用 `python3 -m venv .venv`。
- **依赖懒加载**：仅在虚拟环境中缺少 `PIL` 或 `pypinyin` 时才执行 `pip install -r requirements.txt`，避免重复安装。
- **CLI 入口**：最终通过 `exec "$VENV/bin/python" -m zige "$@"` 转发所有参数给 `zige.__main__`。
- **环境变量**：`PYTHON` 变量可覆盖默认解释器（默认 `python3`）。
- **缓存策略**：`cache/strokes/*.json` 存放已下载的笔顺数据，`cache/bin/vision_ocr` 是编译好的 macOS Vision OCR 二进制，`cache/swift/modules/` 是 Swift 模块缓存；这些目录被 `.gitignore` 排除，不参与版本控制。
- **输出产物**：渲染结果写入 `out/`（也被 `.gitignore` 忽略），支持 PNG/PDF/HTML 三种格式。

## 4. 约定与约束

- 项目没有 CI/CD 流水线、没有 Docker 镜像、没有发布脚本——分发方式即“克隆仓库 + 运行 `./run.sh`”。
- 依赖版本采用宽松下限约束（`>=`），而非固定版本，因此不同环境的依赖可能漂移。
- 测试没有集成到 `run.sh` 中，也没有 pytest 配置文件；运行测试需手动执行 `python -m pytest tests/test_zige.py`（仓库未显式包含 pytest 依赖）。
- 由于 `requirements.txt` 只声明 `pillow` 和 `pypinyin`，但代码还导入了 `requests`、`xml.etree.ElementTree`、`os`、`io` 等标准库/第三方库，若直接 `pip install -r requirements.txt` 后运行，可能会因缺失依赖而失败；实际使用中 `run.sh` 会先尝试导入这两个包，缺失时才会安装 requirements，但不会自动补齐其他缺失依赖。