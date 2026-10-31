---
kind: dependency_management
name: Python 依赖管理：requirements.txt + venv 隔离
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
    - run.sh
    - .gitignore
---

## 1. 使用的系统/方法

仓库使用 Python 生态的标准依赖管理方式：
- 依赖声明：根目录 `requirements.txt`，仅包含两个第三方包。
- 环境隔离：通过标准库 `venv` 创建本地虚拟环境 `.venv/`。
- 安装脚本：根目录 `run.sh` 负责自动创建 venv、升级 pip、按需安装依赖并启动 CLI。
- 无锁文件：没有 `requirements.lock` / `pipenv.lock` / `poetry.lock` 等锁定文件。
- 无私有源：未发现 `pip.conf`、`setup.cfg`、`pyproject.toml` 或任何自定义 index URL。
- 无 vendoring：没有 `vendor/` 目录，所有第三方包均从 PyPI 安装。

## 2. 关键文件

- `requirements.txt`：唯一的外部依赖清单。
- `run.sh`：依赖安装与入口脚本。
- `.gitignore`：排除 `.venv/`、`cache/`、`out/`、`__pycache__/` 等生成物。
- `zige/__main__.py`、`zige/cli.py` 等模块中仅 import `PIL`（pillow）和 `pypinyin` 作为第三方依赖。

## 3. 架构与约定

- 依赖范围极小：项目只依赖 `pillow>=10.0`（图像渲染）和 `pypinyin>=0.49`（拼音处理），其余均为 Python 标准库（`argparse`、`pathlib`、`dataclasses`、`subprocess`、`urllib`、`json`、`io` 等）。
- 运行时依赖检测：`run.sh` 在每次执行时先检查 `.venv/bin/python -c "import PIL, pypinyin"` 是否成功，失败则回退到 `pip install -r requirements.txt`。这意味着即使手动修改了 venv，下次运行也会自动修复。
- venv 首次构建：如果 `.venv/bin/python` 不存在，脚本会调用 `python3 -m venv .venv` 再 `pip install --quiet --upgrade pip`。
- 入口统一：用户通过 `./run.sh [args]` 启动，最终执行 `python -m zige`，避免直接调用解释器导致的环境不一致。

## 4. 约定与约束

- 外部依赖仅在 `requirements.txt` 中声明，且版本约束为宽松下限（`>=`），未锁定精确版本。
- 第三方包不随代码提交；`.gitignore` 显式忽略 `.venv/`、`cache/`、`out/`、`__pycache__/`、`*.py[cod]`。
- 运行前必须存在可执行的 `python3`（由环境变量 `PYTHON` 控制，默认 `python3`），脚本通过 `set -e` 保证任一命令失败即退出。
- 未发现私有 PyPI 镜像、`PIP_INDEX_URL`、`PIP_TRUSTED_HOST` 或 `--extra-index-url` 的使用，依赖来源为默认 PyPI。
- 测试依赖与开发依赖未单独声明，`tests/test_zige.py` 仅使用标准库 `unittest`、`tempfile`、`sys`、`pathlib`，无需额外安装。