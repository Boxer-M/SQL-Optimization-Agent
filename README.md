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

## 应用只读账号

初始化账号仅用于管理数据库。应用账号 `sql_optimization_reader` 仅获授数据库 `CONNECT`、`public` schema 的 `USAGE` 和 `public.orders` 的 `SELECT`，不拥有业务表，不加入其他角色，也不授予后续新表的默认权限。请先装载订单数据集。

在项目根目录执行以下命令：补齐 `.env` 中的只读账号与随机密码，保留已有配置，并创建角色。密码不会写入 SQL、命令参数或输出；Python 在内存中生成 PostgreSQL SCRAM-SHA-256 校验值，通过环境变量传给容器内的 psql。已有同名数据库角色时脚本会停止并回滚，必须先人工核查用途、权限和凭据，不会重设其密码或权限。

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import base64
import hashlib
import hmac
import os
import secrets
import subprocess

path = Path('.env')
text = path.read_text()
values = dict(line.split('=', 1) for line in text.splitlines()
              if line and not line.startswith('#') and '=' in line)
for key, default in [('POSTGRES_READER_USER', 'sql_optimization_reader'),
                     ('POSTGRES_READER_PASSWORD', secrets.token_hex(32))]:
    if not values.get(key):
        if key in values:
            text = text.replace(f'{key}=\n', f'{key}={default}\n')
        else:
            text = text.rstrip() + f'\n{key}={default}\n'
        values[key] = default
assert values['POSTGRES_READER_USER'] == 'sql_optimization_reader'
path.write_text(text)
path.chmod(0o600)
salt = secrets.token_bytes(16)
salted = hashlib.pbkdf2_hmac('sha256', values['POSTGRES_READER_PASSWORD'].encode(), salt, 4096)
stored = hashlib.sha256(hmac.digest(salted, b'Client Key', 'sha256')).digest()
server = hmac.digest(salted, b'Server Key', 'sha256')
b64 = lambda value: base64.b64encode(value).decode()
env = os.environ.copy()
env['POSTGRES_READER_PASSWORD_VERIFIER'] = f'SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}'
subprocess.run(['docker', 'compose', 'exec', '-T', '-e', 'POSTGRES_READER_PASSWORD_VERIFIER',
                'postgres', 'sh', '-c',
                'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" '
                '-d "$POSTGRES_DB" -w -v ON_ERROR_STOP=1'],
               input=Path('sql/create_reader.sql').read_bytes(), env=env, check=True)
PY
```

以下命令使用临时 `postgres:16` 客户端，经宿主机 `127.0.0.1:5434` 登录；需要 Docker Desktop 开启 host networking。客户端退出后自动删除，不改变数据库数据卷。若修改过 `.env` 的端口，命令会使用对应端口。密码仅通过环境传递，请勿启用 shell 的 `set -x`。

```sh
(
  set -a
  . ./.env
  set +a
  export PGPASSWORD="$POSTGRES_READER_PASSWORD"
  docker run --rm -i --network host --env PGPASSWORD postgres:16 \
    psql -h 127.0.0.1 -p "${POSTGRES_PORT:-5434}" \
    -U "$POSTGRES_READER_USER" -d sql_optimization -w -v ON_ERROR_STOP=1 <<'SQL'
SELECT current_database(), current_user;
SELECT COUNT(*) FROM public.orders;
SELECT has_database_privilege(current_user, current_database(), 'CONNECT') AS can_connect,
       has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
       has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
       has_table_privilege(current_user, 'public.orders', 'SELECT') AS can_select,
       has_table_privilege(current_user, 'public.orders',
           'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS has_write_or_other_grants;
SELECT rolname, rolsuper, rolinherit, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname = current_user;
SELECT count(*) AS role_memberships FROM pg_auth_members
WHERE member = (SELECT oid FROM pg_roles WHERE rolname = current_user);
SELECT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = 'orders';
SQL

  # 显式事务用于保护意外成功的情况；无论 UPDATE 是否成功，均回滚。
  docker run --rm -i --network host --env PGPASSWORD postgres:16 \
    psql -h 127.0.0.1 -p "${POSTGRES_PORT:-5434}" \
    -U "$POSTGRES_READER_USER" -d sql_optimization -w <<'SQL'
BEGIN;
UPDATE public.orders SET status = status WHERE FALSE;
ROLLBACK;
SQL
)
```

预期数据库为 `sql_optimization`、用户为 `sql_optimization_reader`、订单数为 `300005`；CONNECT、USAGE、SELECT 为真，schema CREATE 和表写入等其他授权为假；角色管理属性均为假、角色成员关系为 0、表所有者仍为初始化账号。UPDATE 必须返回 `permission denied for table orders`，不能仅依赖事务只读设置或 `WHERE FALSE`。若 UPDATE 意外成功，停止使用该账号，核查实际授权并修正后重新验收。

## Python 只读数据库连接

在已有 Python 3.12 虚拟环境中安装 psycopg 3（二进制发行版，无需本机 libpq）及 python-dotenv，然后执行固定连接检查：

```sh
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip check
.venv/bin/python scripts/check_db_connection.py
```

脚本始终读取项目根目录的 `.env`，环境变量优先（包括显式空值），不修改 `.env`。沿用 `POSTGRES_READER_USER`、`POSTGRES_READER_PASSWORD` 和 `POSTGRES_PORT`（默认 `5434`）；只接受应用账号 `sql_optimization_reader`，目标固定为 `127.0.0.1` 上的 `sql_optimization`，连接超时为 5 秒，不读取初始化密码 `POSTGRES_PASSWORD`。本轮脚本不接受 SQL 或其他命令行参数，只执行内置验证查询。

成功输出：

```text
database=sql_optimization
user=sql_optimization_reader
transaction_read_only=on
orders_count=300005
```

验证空密码在连接前被拒绝（该命令预期退出码为 1，不会修改 `.env`）：

```sh
POSTGRES_READER_PASSWORD= .venv/bin/python scripts/check_db_connection.py
```

预期错误：`Missing required configuration: POSTGRES_READER_PASSWORD`。连接及查询失败时只输出脱敏提示，不输出原始数据库异常、密码或连接串。

可复用入口为 `sql_optimization_agent.db.readonly_connection`，可接收配置映射，默认读取进程环境。调用方使用 `with readonly_connection(config) as connection:`，在其中使用游标执行查询。连接启动时设置默认只读事务，后续事务也使用只读模式；数据库账号权限仍是访问控制边界。退出 `with` 时事务成功则提交，异常则回滚，连接始终关闭。

## 单表 Schema Inspector

`sql_optimization_agent.tools.schema_inspector.inspect_table(schema_name, table_name, config)`
复用应用只读连接，返回可直接 JSON 序列化的字典：`schema`、`table`、`columns`、`primary_key`、`indexes`。字段按表定义顺序、主键字段按主键定义顺序返回；索引按名称排序，定义包含主键索引。字段类型保留精度（如 `numeric(12,2)`），可空性使用布尔值。

在项目根目录、已安装项目且数据库运行时执行：

```sh
.venv/bin/python - <<'PY'
import json
import os
from dotenv import dotenv_values
from sql_optimization_agent.tools.schema_inspector import inspect_table, TableNotFoundError

config = {**dotenv_values('.env', interpolate=False), **os.environ}
print(json.dumps(inspect_table('public', 'orders', config), ensure_ascii=False, indent=2))
try:
    inspect_table('public', 'orders_does_not_exist', config)
except TableNotFoundError as error:
    print(error)
else:
    raise AssertionError('Expected TableNotFoundError')
PY
```

schema 和表名必须显式传入，按数据库中真实名称精确匹配，不受 `search_path` 影响；通过参数绑定查询系统目录，不将输入拼接进 SQL。不存在的目标抛出带 schema、表名的 `TableNotFoundError`。支持普通表、分区表和外部表；本入口不将视图视为表，不读取业务行或统计信息。

