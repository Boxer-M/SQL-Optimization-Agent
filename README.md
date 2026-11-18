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

## SQL 优化验证与性能基准

在项目根目录、数据库运行且 `.env` 已配置只读账号时执行：

```sh
.venv/bin/python scripts/benchmark_orders.py
# 保存新报告而不覆盖仓库中本轮的实测报告：
.venv/bin/python scripts/benchmark_orders.py > /tmp/orders-date-benchmark.json
# 不连接数据库的比较逻辑测试：
.venv/bin/python -m unittest discover -s tests -v
```

入口不接受任何参数或外部 SQL，仅运行代码内固定查询。两条查询都投影 `customer_id, created_at, status, amount`，比较以下条件：

```sql
-- 原查询
WHERE created_at::date = DATE '2025-06-15'
-- 正确改写：左闭右开
WHERE created_at >= TIMESTAMP '2025-06-15 00:00:00'
  AND created_at < TIMESTAMP '2025-06-16 00:00:00'
-- 固定负向用例：错误包含次日零点
WHERE created_at >= TIMESTAMP '2025-06-15 00:00:00'
  AND created_at <= TIMESTAMP '2025-06-16 00:00:00'
```

验证复用 `db.readonly_connection`，在同一 REPEATABLE READ 只读事务快照中比较列名、PostgreSQL 类型 OID、长度、精度和小数位，以及完整结果的整行值和出现次数。内部保留 `None`、`datetime` 和 `Decimal`，不按集合去重、不转换成浮点数。差异示例在 JSON 中用带类型标记的时间和 Decimal 展示，NULL 保持 JSON null。

取数使用服务端游标，每批最多 256 行。每条查询最多 10000 行、16 MiB 的逐行 pickle 序列化大小（不是 Python 堆内存的精确上限），取数阶段总预算为 30 秒；每条数据库语句超时为 5 秒。超预算或超时会停止并报出原因，不继续性能测试；列或结果不一致时报告差异并跳过该 SQL 对的性能测试。固定负向用例必须检出差异。

仅对一致的查询对各预热一次，随后严格按原查询、改写查询交替采样，各 7 次。使用 `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)`，保留自然规划器设置，不禁用顺序扫描或强制索引。输出 14 个原始执行时间、每组中位数、耗时变化百分比、计划节点及条件、每次采样的缓冲区指标。指标为 PostgreSQL 服务端 EXPLAIN 执行时间，不包含向 Python 传输全部业务结果的耗时。父节点缓冲区指标包含子节点，不应重复相加。

本轮实测报告保存在 `reports/orders_date_benchmark.json`：

| 项目 | 原查询 | 正确改写 |
| --- | --- | --- |
| 命中行数 | 825 | 825 |
| 自然计划 | Gather → Parallel Seq Scan | Index Scan，`orders_created_at_idx` |
| 7 次执行时间（毫秒） | 13.589, 9.838, 8.081, 7.451, 7.560, 6.969, 7.052 | 0.119, 0.079, 0.062, 0.073, 0.063, 0.054, 0.057 |
| 中位数（毫秒） | 7.560 | 0.063 |
| 根节点 shared hit/read blocks（各次均相同） | 2713 / 0 | 16 / 0 |

结论：**在本次测试数据上结果一致**。改写中位耗时变化 `(0.063 / 7.560 - 1) × 100% = -99.17%`。两组样本均有较大波动，极差/中位数分别约 87.6% 和 103.2%；这是一份热缓存、小样本的本地观察，不能据此保证其他负载或数据上的相同收益。

错误 `<=` 用例返回 826 行，额外纳入 `(10003, 2025-06-16 00:00:00, pending, 345.67)`，已拒绝进行该 SQL 对的性能测试。NULL 创建时间在三个条件下均不命中；原有重复行按出现次数保留。实际验证了行数与字节预算超限会停止，且不进入性能采样。

## 千问优化与命令行编排

本模块仅支持现有订单日期过滤场景，原 SQL 固定为前述 `created_at::date = DATE '2025-06-15'`。流程为：Schema Inspector 读取字段及索引 → 获取原 SQL 的估算计划（不执行 ANALYZE）→ 一次模型请求 → 校验结构化范围建议 → 应用构造绑定参数的候选 → 同一只读快照完整结果比较 → 仅对一致候选预热和交替采样。

### 本地模型配置

在被 Git 忽略的项目根目录 `.env` 中填写以下变量。`.env.example` 只提供空密钥和占位值，不要将实际密钥写进模板、报告或命令行参数。

```dotenv
QWEN_API_KEY=<在本地填写百炼密钥>
QWEN_BASE_URL=https://<workspace-id>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=<百炼中已开通的模型ID>
```

建议先使用截图中可见的通用文本模型 `qwen3.7-plus`。北京业务空间专属 endpoint 如上，将 `<workspace-id>` 替换为业务空间 ID；北京旧地址 `https://dashscope.aliyuncs.com/compatible-mode/v1` 仍可使用。本轮已在本地 `.env` 预填该旧地址和建议模型，只保留 `QWEN_API_KEY` 为空。API Key 和 endpoint 必须属于同一地域，模型须在你的账号中可调用；免费额度与“用完即停”设置以控制台为准。参考[百炼 OpenAI 兼容接口文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。

千问只接收原 SQL、表字段/索引摘要、估算计划摘要，不接收数据库凭据或业务行。程序不输出 HTTP 响应原始错误、认证头或连接串，报告还会脱敏已知本地密钥。连接超时 5 秒，HTTP 读写等待超时 30 秒，响应上限 64 KiB；每次优化最多一次 HTTP POST，不自动重试、重定向或发起格式修复请求。报告明确区分缺配置、鉴权、额度/限流、超时、格式错误、无建议和结果不一致等状态。

### 运行与报告

```sh
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip check
# 真实千问调用；先在 .env 中填写有效配置
.venv/bin/python scripts/optimize_orders.py > /tmp/qwen-orders-live.json
# 无需模型密钥的可控响应；这些命令仍实际访问 PostgreSQL
.venv/bin/python scripts/optimize_orders.py --fixture normal > /tmp/qwen-orders-normal.json
.venv/bin/python scripts/optimize_orders.py --fixture malformed > /tmp/qwen-orders-malformed.json
.venv/bin/python scripts/optimize_orders.py --fixture wrong-upper > /tmp/qwen-orders-wrong-upper.json
.venv/bin/python scripts/optimize_orders.py --fixture no-suggestion > /tmp/qwen-orders-no-suggestion.json
.venv/bin/python -m unittest discover -s tests -v
```

成功或无建议退出码为 0；配置阻塞、格式错误和结果不一致退出码为 1，同时仍输出有原因的 JSON 报告。原有 `scripts/benchmark_orders.py` 入口保持可运行，不调用模型。

建议格式仅允许 `action=date_range`、`start`、`end`、两个包含性布尔值及 `reason`；也允许仅含 `action=no_suggestion` 与 `reason` 的对象。当前只接受 2025-06-15 零点至 2025-06-16 零点的无时区时间边界，拒绝额外字段、原始 SQL、未知操作、重复 JSON 键、非布尔标记和不支持的范围。包含性标记仅用于在应用预定义的比较操作符之间选择；即使结构合法，也必须通过数据库结果验证。候选固定使用 `public.orders` 和四个投影列，时间值通过 psycopg 参数绑定：

```sql
SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at >= %s::timestamp without time zone
  AND created_at < %s::timestamp without time zone
```

绑定参数为 `2025-06-15 00:00:00`、`2025-06-16 00:00:00`，不会执行模型返回的文本。比较沿用 10000 行/16 MiB 取数预算、5 秒语句超时、30 秒取数时间预算；性能测量沿用各预热一次、交替各 7 次的自然计划方法。报告包含来源、实际模型 ID、收到的原始内容和结构化建议、候选模板与参数、比较结果、扫描条件、14 个原始样本、缓冲区指标及中位数。诊断连接在模型等待期间关闭；结果比较和性能测试另在同一个 REPEATABLE READ 只读事务内执行。

### 本轮实测来源

| 报告 | 模型来源 | 真实 PostgreSQL 验收结果 |
| --- | --- | --- |
| `reports/qwen_normal.json` | 可控正常建议，没有调用千问 | 825/825 行，在本次测试数据上结果一致；Parallel Seq Scan → `orders_created_at_idx` Index Scan |
| `reports/qwen_malformed.json` | 可控格式错误 | 拒绝建议，不执行候选和性能测量 |
| `reports/qwen_wrong-upper.json` | 可控错误上界 | 825/826 行，多出次日零点记录；不进行性能测量 |
| `reports/qwen_no-suggestion.json` | 可控无建议 | 正常结束，无候选和性能测量 |
| `reports/qwen_live.json` | 真实模式入口，但未发起请求 | `missing_config`：缺少 `QWEN_API_KEY`，HTTP 请求次数为 0 |

正常可控建议的真实数据库样本（毫秒）：原查询 `[8.239, 7.556, 7.056, 7.289, 6.557, 6.670, 6.936]`；候选 `[0.085, 0.078, 0.071, 0.062, 0.056, 0.061, 0.067]`。中位数为 `7.056 → 0.067 ms`，耗时变化约 `-99.05%`。两组极差均未超过中位数的 50%，但仍只是本地热缓存的小样本观察，不能视为真实千问调用结果或通用性能保证。

