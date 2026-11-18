# SQL Optimization Agent

用于 SQL 分析与优化的 Agent 项目，包含 Python 项目骨架与 PostgreSQL 16 本地测试环境。

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

## PostgreSQL 测试环境

需要启动 Docker Desktop，并安装 Docker Compose。在项目根目录首次配置本地密码（已有 `.env` 时保留它，不要覆盖）：

```sh
cp -n .env.example .env
.venv/bin/python - <<'PY'
from pathlib import Path
import secrets

path = Path('.env')
text = path.read_text()
if 'POSTGRES_PASSWORD=\n' in text:
    text = text.replace('POSTGRES_PASSWORD=\n', f'POSTGRES_PASSWORD={secrets.token_hex(32)}\n')
    path.write_text(text)
path.chmod(0o600)
PY
```

`.env` 已被 Git 忽略；不要提交或分享其中的密码。默认地址为 `127.0.0.1:5434`，数据库为 `sql_optimization`，初始化账号为 `sql_optimization_admin`。若端口被占用，在 `.env` 中修改 `POSTGRES_PORT`。该账号用于本地测试环境初始化，具有管理员权限。

验证配置并启动（使用 `--quiet` 避免输出展开后的密码）：

```sh
docker compose config --quiet
docker compose up -d --wait
docker compose ps
docker compose port postgres 5432
```

通过容器内 TCP 连接验证账号、密码、数据库与版本；`-w` 禁止密码交互，错误密码会使连接失败：

```sh
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -w -v ON_ERROR_STOP=1 -c "SELECT current_database(), current_user, current_setting('\''server_version'\'');"'
```

从 Mac 上的数据库客户端连接时，使用上述宿主机地址、端口及 `.env` 中的密码。

```sh
docker compose stop     # 停止服务，保留数据
docker compose up -d --wait
```

Compose 项目名为 `sql-optimization-agent`，独立数据卷为 `sql-optimization-agent-postgres-data`。首次初始化后，修改 `.env` 的密码不会自动更改已有数据库账号密码。不要执行 `docker compose down -v`，除非确实需要删除测试数据并重新初始化。

## 订单性能测试数据集

在项目根目录、现有 PostgreSQL 服务运行时装载：

```sh
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -w -v ON_ERROR_STOP=1' < sql/seed_orders.sql
```

连接账号和密码由 Compose 从本地 `.env` 配置到容器，命令不输出密码。脚本以事务创建 `public.orders`，通过固定公式生成主键 `1–300000` 的基础订单，时间覆盖 2025 年全年；主键 `900001–900005` 是固定边界记录，其中 `900001` 与 `900005` 省略主键后整行相同。`created_at` 为允许 NULL 的无时区时间戳，`amount` 为 `numeric(12,2)`。

脚本保留已有表、数据与数据卷，重复执行仅补齐缺失记录；若预留主键已被不同内容占用，会报错并回滚，不覆盖数据。其他主键的已有订单保持不变，因此已有额外订单时总行数可能超过 300005。装载后建立原列 B-tree 索引 `orders_created_at_idx` 并执行 `ANALYZE`。

执行以下验收查询：

```sh
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -w -v ON_ERROR_STOP=1' <<'SQL'
SELECT count(*) FILTER (WHERE order_id BETWEEN 1 AND 300000) AS base_rows,
       count(*) FILTER (WHERE order_id BETWEEN 900001 AND 900005) AS boundary_rows,
       count(*) AS total_rows,
       min(created_at) FILTER (WHERE order_id BETWEEN 1 AND 300000) AS base_min,
       max(created_at) FILTER (WHERE order_id BETWEEN 1 AND 300000) AS base_max,
       count(DISTINCT date_trunc('month', created_at))
           FILTER (WHERE order_id BETWEEN 1 AND 300000) AS base_months
FROM public.orders;

SELECT * FROM public.orders
WHERE order_id BETWEEN 900001 AND 900005 ORDER BY order_id;

SELECT customer_id, created_at, status, amount, count(*) AS copies,
       array_agg(order_id ORDER BY order_id) AS order_ids
FROM public.orders
GROUP BY customer_id, created_at, status, amount HAVING count(*) > 1
ORDER BY customer_id, created_at;

SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'orders' ORDER BY indexname;
SQL
```

初始空库预期结果：基础 300000 行，边界 5 行，总计 300005 行；基础时间最小值 `2025-01-01 00:00:00`，最大值 `2025-12-31 23:59:59`，覆盖 12 个月。边界包含 6 月 15 日零点、该日最后一微秒、6 月 16 日零点和 NULL；重复组包含 `{900001,900005}`。索引定义应为 `USING btree (created_at)`，没有函数或类型转换。

再次运行装载命令及上述查询，确认总行数仍为 300005（有额外既存订单时，与第一次的实际总行数比较）。

