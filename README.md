# SQL Optimization Agent

用于 SQL 分析与优化的 Agent 项目。目前仅包含 Python 项目骨架，后续按功能任务接入数据库、优化能力与业务依赖。

## 本地安装

需要 Python 3.12 和 Git。在项目根目录执行：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
```

验证 Python 版本与安装路径：

```sh
.venv/bin/python --version
.venv/bin/python -c "import sql_optimization_agent; print(sql_optimization_agent.__file__)"
```

导入路径应指向本项目的 `src/sql_optimization_agent/__init__.py`。

