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

初始化账号仅用于管理数据库。应用账号 `sql_optimization_reader` 初始仅获授数据库 `CONNECT`、`public` schema 的 `USAGE` 和 `public.orders` 的 `SELECT`，不拥有业务表，不加入其他角色，也不授予后续新表的默认权限。请先装载订单数据集。加载下述明细数据集时，会单独额外授予 `public.order_items` 的 `SELECT`，不会扩大为所有表权限。

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

本模块仅支持现有订单日期过滤场景。公共服务统一定义默认日期 `2025-06-15`，CLI 可用 `--date YYYY-MM-DD` 指定其他日期；原 SQL 为参数化的 `created_at::date = %s::date`。流程为：Schema Inspector 读取字段及索引 → 获取原 SQL 的估算计划（不执行 ANALYZE）→ 一次模型请求 → 校验结构化范围建议 → 应用构造绑定参数的候选 → 同一只读快照完整结果比较 → 仅对一致候选预热和交替采样。

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

建议格式仅允许 `action=date_range`、`start`、`end`、两个包含性布尔值及 `reason`；也允许仅含 `action=no_suggestion` 与 `reason` 的对象。只接受目标日零点至次日零点的无时区时间边界，拒绝额外字段、原始 SQL、未知操作、重复 JSON 键、非布尔标记和不支持的范围。包含性标记仅用于在应用预定义的比较操作符之间选择；即使结构合法，也必须通过数据库结果验证。候选固定使用 `public.orders` 和四个投影列，时间值通过 psycopg 参数绑定：

```sql
SELECT customer_id, created_at, status, amount
FROM public.orders
WHERE created_at >= %s::timestamp without time zone
  AND created_at < %s::timestamp without time zone
```

绑定参数由同一个日期上下文生成；默认日期时为 `2025-06-15 00:00:00`、`2025-06-16 00:00:00`，不会执行模型返回的文本。比较沿用 10000 行/16 MiB 取数预算、5 秒语句超时、30 秒取数时间预算；性能测量沿用各预热一次、交替各 7 次的自然计划方法。报告包含目标日期、来源、实际模型 ID、收到的原始内容和结构化建议、候选模板与参数、比较结果、扫描条件、14 个原始样本、缓冲区指标及中位数。诊断连接在模型等待期间关闭；结果比较和性能测试另在同一个 REPEATABLE READ 只读事务内执行。

### 本轮实测来源

| 报告 | 模型来源 | 真实 PostgreSQL 验收结果 |
| --- | --- | --- |
| `reports/qwen_normal.json` | 可控正常建议，没有调用千问 | 825/825 行，在本次测试数据上结果一致；Parallel Seq Scan → `orders_created_at_idx` Index Scan |
| `reports/qwen_malformed.json` | 可控格式错误 | 拒绝建议，不执行候选和性能测量 |
| `reports/qwen_wrong-upper.json` | 可控错误上界 | 825/826 行，多出次日零点记录；不进行性能测量 |
| `reports/qwen_no-suggestion.json` | 可控无建议 | 正常结束，无候选和性能测量 |
| `reports/qwen_live.json` | 真实模式入口，但未发起请求 | `missing_config`：缺少 `QWEN_API_KEY`，HTTP 请求次数为 0 |

正常可控建议的真实数据库样本（毫秒）：原查询 `[8.239, 7.556, 7.056, 7.289, 6.557, 6.670, 6.936]`；候选 `[0.085, 0.078, 0.071, 0.062, 0.056, 0.061, 0.067]`。中位数为 `7.056 → 0.067 ms`，耗时变化约 `-99.05%`。两组极差均未超过中位数的 50%，但仍只是本地热缓存的小样本观察，不能视为真实千问调用结果或通用性能保证。

## HTTP、MCP 与持续集成

命令行、HTTP 和 MCP 统一调用 `OptimizationService.optimize(scenario)`，复用结果验证和 Benchmark Runner。公共服务、CLI、HTTP 和 MCP 均支持 `orders_date_filter_2025_06_15` 和 `orders_with_items_count`。HTTP/MCP 使用服务端模型模式配置，客户端只传场景和可选日期。不支持通用 SQL 输入。报告沿用结构化状态、候选模板及绑定参数、结果比较、计划与计时，同时用 `stage` 标明完成或失败阶段。只读连接、5 秒 SQL 超时和取数预算保持不变。

安装项目及测试依赖：

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

### 命令行

```sh
# 真实千问模式；有效 API 配置需在 .env 中填写
.venv/bin/python scripts/optimize_orders.py --scenario orders_date_filter_2025_06_15
# 可控模型建议 + 真实 PostgreSQL，不消耗模型额度
.venv/bin/python scripts/optimize_orders.py --fixture normal
# 显式默认日期或其他日期，均不调用真实模型
.venv/bin/python scripts/optimize_orders.py --fixture normal --date 2025-06-15
.venv/bin/python scripts/optimize_orders.py --fixture normal --date 2025-06-16
# 负向边界用例，预期退出码 1，stage=result_validation，跳过计时
.venv/bin/python scripts/optimize_orders.py --fixture wrong-upper --date 2025-06-15
# 非法日期，模型及数据库调用前返回 invalid_date，预期退出码 1
.venv/bin/python scripts/optimize_orders.py --date 2025-02-29
```

日期必须为严格的 `YYYY-MM-DD`，且能计算次日。默认值只在公共服务 `DEFAULT_ORDER_DATE` 定义；旧场景标识保持兼容，不再从标识中提取日期。`orders.created_at` 已确认是允许 NULL 的 `timestamp without time zone`，因此按无时区日历日生成 `[当天零点, 次日零点)`，不使用 UTC 或本机时区转换。提示词、可控响应、候选校验和报告共用此上下文；诊断、结果比较、原查询预热及全部计时使用同一份 SQL 模板与日期参数。旧的固定基准入口仍保留原日期回归用例。

CLI、HTTP 请求体和 MCP 工具均支持可选日期，省略日期统一由公共服务选择原默认值，各适配层不重复定义默认日期或校验规则。报告以 `target_date` 和 `date_context` 记录实际日期。错误 `<=` 上界结构上允许进入验证，但在包含次日零点记录时由完整结果比较拒绝，并跳过计时。

日期参数化的真实千问验收记录位于 `reports/qwen_date_2025_06_16_live.json`：以 `--date 2025-06-16` 请求 `qwen3.7-plus` 一次，候选边界为 `[2025-06-16 00:00:00, 2025-06-17 00:00:00)`，原查询和候选均为 823 行，在本次测试数据上结果一致。自然计划由 Parallel Seq Scan 变为 `orders_created_at_idx` Index Scan，中位数 `8.416 → 0.085 ms`；原查询样本波动较大，完整原始计时及缓冲区指标保留在报告中。该验收没有运行独立连通性探测或重试。

### FastAPI

在一个终端启动本机 HTTP 服务：

```sh
.venv/bin/python -m uvicorn sql_optimization_agent.api:app --host 127.0.0.1 --port 8010
```

若只想复现可控建议，可以改用以下启动命令（二选一，不要同时占用同一端口）：

```sh
SQL_OPTIMIZATION_MODEL_FIXTURE=normal .venv/bin/python -m uvicorn sql_optimization_agent.api:app --host 127.0.0.1 --port 8010
```

在另一个终端调用：

```sh
curl -sS http://127.0.0.1:8010/optimize \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"orders_date_filter_2025_06_15"}'
# 显式日期：报告 target_date=2025-06-16
curl -sS http://127.0.0.1:8010/optimize \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"orders_date_filter_2025_06_15","date":"2025-06-16"}'
# 非法日期：预期 HTTP 400、status=invalid_date、model_requests=0
curl -i http://127.0.0.1:8010/optimize \
  -H 'Content-Type: application/json' \
  -d '{"scenario":"orders_date_filter_2025_06_15","date":"2025-02-29"}'
```

`date` 是可选的 `YYYY-MM-DD` 字符串；省略或传 null 时使用公共服务默认日期 `2025-06-15`。非法日历日期由公共服务在模型调用前拒绝，映射为 HTTP 400 和 `invalid_date`。未知场景返回 HTTP 400 和 `unsupported_scenario`；缺少场景、日期类型错误或额外传入 `sql` 等字段返回 422。支持场景的成功、无建议和候选拒绝均返回 HTTP 200，并由报告的 `status` 区分；配置、鉴权或服务异常等返回 503 及脱敏报告。测试模式只能由服务端启动环境选择，不能由请求体切换。服务启动后按 Ctrl-C 停止。

### 本地 stdio MCP

stdio 服务启动命令如下，stdout 仅用于 MCP 协议：

```sh
.venv/bin/python -m sql_optimization_agent.mcp_server
# 安装后也可使用 .venv/bin/sql-optimization-mcp
```

演示客户端会自动启动服务、初始化会话、发现工具并调用，无需另外手动启动服务：

```sh
# 真实模型模式
.venv/bin/python scripts/call_optimization_mcp.py
# 可控模型建议 + 真实 PostgreSQL
SQL_OPTIMIZATION_MODEL_FIXTURE=normal .venv/bin/python scripts/call_optimization_mcp.py
# 显式日期
SQL_OPTIMIZATION_MODEL_FIXTURE=normal .venv/bin/python scripts/call_optimization_mcp.py --date 2025-06-16
# 非法日期：预期工具参数错误和退出码 1，模型请求数为 0
SQL_OPTIMIZATION_MODEL_FIXTURE=normal .venv/bin/python scripts/call_optimization_mcp.py --date 2025-02-29
# 复现错误上界拒绝；预期退出码为 1
SQL_OPTIMIZATION_MODEL_FIXTURE=wrong-upper .venv/bin/python scripts/call_optimization_mcp.py
```

工具名为 `optimize_orders_date`，参数 `date` 是可选的 `YYYY-MM-DD` 字符串，省略或传 null 时使用公共服务默认日期。显式调用参数为：

```json
{"scenario":"orders_date_filter_2025_06_15","date":"2025-06-16"}
```

非法日期返回 MCP `isError=true`，结构化报告包含 `status=invalid_date`、`error.type=invalid_tool_argument`、`error.parameter=date` 及 `model_requests=0`，不会调用模型。未知场景返回 `unsupported_scenario` 并设置 MCP `isError=true`；候选被拒绝或模型调用失败也会设置错误标记，同时保留 `structuredContent` 中的完整报告。MCP 使用官方 Python SDK 1.x 的 stdio 传输，示例客户端将发现信息写入 stderr，将结构化报告写入 stdout。参考 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0)。

### CI 与本轮验收

`.github/workflows/ci.yml` 在面向 `main` 的 PR 以及 `main` 更新时执行：Python 3.12 → 安装 `.[test]` → `pip check` → `pytest -q`。不配置数据库服务、不注入百炼密钥。测试使用可控模型响应及数据库边界替身，实际运行建议校验、完整结果比较、HTTP 适配和 MCP 协议；自动阻止意外真实 HTTP/数据库连接。另有真实 stdio 子进程的工具发现和未知场景调用测试，无需外部服务。

初版接口交付的 pytest 验收为 22 项通过（另含 11 个子测试），`pip check` 通过。Mac 上的 PostgreSQL 16 端到端记录保存在 `reports/interfaces_acceptance.json`，明确标注为可控模型建议 + 真实数据库测量：

| 入口 | 结果行数 | 原查询/候选中位耗时（ms） | 自然计划 |
| --- | --- | --- | --- |
| CLI | 825/825 | 7.172 / 0.064 | Parallel Seq Scan → Index Scan |
| 实际 HTTP 服务 | 825/825 | 7.793 / 0.073 | Parallel Seq Scan → Index Scan |
| 实际 stdio MCP | 825/825 | 7.217 / 0.066 | Parallel Seq Scan → Index Scan |

三条路径均为“在本次测试数据上结果一致”，候选使用 `orders_created_at_idx`。HTTP 与 MCP 的错误上界用例均返回 825/826 行，拒绝后未执行性能测量；实际 HTTP 未知场景返回 400。报告包含原始样本与扫描条件，这些热缓存本地测量不能视为模型实回或其他环境的性能保证。

本轮按原命令单独补跑了一次真实千问流程，报告为 `reports/qwen_live_interfaces.json`，与上一轮可控建议报告分别保留。请求次数为 1，在 `model_request` 阶段返回 `authentication_error`；没有得到实际模型 ID 或候选，也没有执行该次性能测试。需核查本地百炼密钥、地域和模型权限后再次运行；本轮未自动重试或修改密钥。

GitHub Actions 的远端运行结果需在创建本 PR 后确认；仅推送功能分支不会触发当前配置的工作流。

接口日期扩展的本地验收：50 项 pytest 测试通过（另含 11 个子测试）。实际 loopback HTTP 服务与 stdio MCP 均以可控建议连接本地 PostgreSQL：省略日期时为 `2025-06-15`、825/825 行，显式 `2025-06-16` 时为 823/823 行，完整结果比较通过；`2025-02-29` 分别返回 HTTP 400 和 MCP 工具参数错误，模型请求数均为 0。该轮未调用真实千问。

## 订单明细数据与 JOIN/EXISTS 固定基准

本节提供可复现数据及独立验收脚本；公共服务/CLI 场景复用同一套 SQL 和验证引擎，千问模式需按后文显式启用。复用现有 Compose 数据库、`.env`、初始化账号和应用只读账号，不创建新容器或数据卷。

### 初始化与重复装载

前提：已按前文创建 `public.orders`、装载订单并配置 `sql_optimization_reader`。在项目根目录执行：

```sh
docker compose up -d --wait
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -w -v ON_ERROR_STOP=1' < sql/seed_order_items.sql
# 再执行同一命令，验证重复装载
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -w -v ON_ERROR_STOP=1' < sql/seed_order_items.sql
```

脚本创建 `public.order_items(item_id bigint PRIMARY KEY, order_id bigint NOT NULL REFERENCES public.orders(order_id), line_number smallint NOT NULL)`，明细行号限制为 1–3；创建 `order_items_order_id_idx` 原列 B-tree 索引，单独授予现有应用账号 SELECT，并执行 `ANALYZE public.order_items`。不会修改账号密码、订单行、现有数据卷或其他表授权。

仅对原有种子订单主键 `1–300000` 和 `900001–900005` 造数：`order_id % 3` 为 0、1、2 时分别生成 0、1、3 条明细，`item_id = order_id * 10 + line_number`。重复执行只补齐缺失记录；主键内容冲突时报错并回滚，不覆盖或删除已有数据。额外既有明细也会保留，因此有额外数据的库应比较实际前后行数，而不能强制要求固定总数。

本轮真实装载记录在 `reports/order_items_initialization.json`：两次均为 **400008** 行，第二次新增 0 行；明细内容指纹一致。原有 **300005** 条订单的行数与完整行内容聚合指纹在两次装载前后均一致。实际核查外键已验证、`order_id` 为 `bigint NOT NULL`，无空关联或孤儿明细；表所有者仍为初始化账号，应用账号仅获得 SELECT，无写权限或 schema CREATE 权限。

### 两条固定 SQL 与验收

```sql
-- JOIN + COUNT(DISTINCT)
SELECT COUNT(DISTINCT o.order_id) AS order_count
FROM public.orders AS o
JOIN public.order_items AS i ON i.order_id = o.order_id
WHERE o.created_at >= TIMESTAMP '2025-06-15 00:00:00'
  AND o.created_at <  TIMESTAMP '2025-06-16 00:00:00';

-- EXISTS + COUNT(*)
SELECT COUNT(*) AS order_count
FROM public.orders AS o
WHERE o.created_at >= TIMESTAMP '2025-06-15 00:00:00'
  AND o.created_at <  TIMESTAMP '2025-06-16 00:00:00'
  AND EXISTS (
      SELECT 1 FROM public.order_items AS i WHERE i.order_id = o.order_id
  );
```

使用独立验收脚本复现（内部使用绑定参数，不接受外部 SQL 或场景输入）：

```sh
.venv/bin/python scripts/benchmark_order_items.py > /tmp/order-items-benchmark.json
.venv/bin/python -m pytest -q
```

脚本使用 `.env` 中的应用只读凭据，在同一个 REPEATABLE READ 只读事务内：统计明细和三类订单 → 比较 JOIN DISTINCT 与 EXISTS 的完整订单 ID 结果及双向差集 → 核对计数 → 各预热一次、严格交替采样各 7 次。ID 比较沿用 10000 行/16 MiB 的取数预算、5 秒语句超时及 30 秒取数预算；不一致或超预算不会进入计时。仅计数相同并不足以通过，测试专门验证了“计数相同但一进一出”的 ID 差异会被拦截。

使用 `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)`，不强制连接方式、索引或关闭顺序扫描。报告保留全部两组 ID、双向差集、计数、14 个原始耗时、自然计划条件、缓冲区访问、Join Type、Heap Fetches 与排序信息。父子节点的缓冲区指标不能重复相加。

本轮实测结果见 `reports/order_items_benchmark.json`：数据库 PostgreSQL 16.15，账号 `sql_optimization_reader`，事务只读且隔离级别为 repeatable read。

| 数据覆盖 | 实测数量 |
| --- | ---: |
| 全库明细行数 | 400008 |
| 2025-06-15 订单数 | 825 |
| 零条明细订单 | 274 |
| 一条明细订单 | 275 |
| 多条明细订单（各 3 条） | 276 |
| 当日关联明细行数 | 1103 |
| 两侧纳入的订单 ID 数 / 最终计数 | 551 / 551 |
| JOIN − EXISTS / EXISTS − JOIN | 0 / 0 |

| 性能指标 | JOIN + COUNT(DISTINCT) | EXISTS + COUNT(*) |
| --- | --- | --- |
| 自然计划 | Aggregate → Sort → Nested Loop (Inner) | Aggregate → Nested Loop (Semi) |
| 扫描 | orders Index Scan + order_items Index Only Scan | 相同的两个索引扫描 |
| 索引 | orders_created_at_idx、order_items_order_id_idx | 相同 |
| 7 次执行时间（ms） | 0.541, 0.505, 0.531, 0.526, 0.560, 0.554, 0.538 | 0.486, 0.419, 0.424, 0.444, 0.443, 0.433, 0.433 |
| 中位数（ms） | 0.538 | 0.433 |
| 根节点 shared hit / read blocks | 每次 2492 / 0 | 每次 2492 / 0 |

结论：**在本次测试数据上结果一致**，551 个实际订单 ID 完全相同，不仅是计数一致。EXISTS 中位耗时本次下降约 **19.52%**，绝对差只有 **0.105 ms**；这是本地热缓存的小样本，不应推断其他数据或负载也有相同收益。原查询需处理 1103 条关联行并排序去重，Semi Join 输出 551 笔订单。报告中 `original` 指 JOIN，`rewritten` 指 EXISTS。

## 订单明细计数场景：公共服务与 CLI

新场景 `orders_with_items_count` 复用上节的 JOIN/EXISTS 模板、绑定参数、完整 ID 比较和自然计划基准。共享引擎位于 `tools/order_items_benchmark.py`，独立基准脚本仅保留固定日期调用，避免维护两份算法。

```sh
# 默认日期由公共服务统一选择 2025-06-15；本场景默认即使用受控 EXISTS，不调用模型
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count
# 显式日期
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-16
# 固定负向候选：同一天移出一个合格 ID，再移入一个没有明细的 ID；预期退出码 1
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15 --fixture swapped-ids
# 原日期过滤场景仍可用可控建议回归
.venv/bin/python scripts/optimize_orders.py --fixture normal --date 2025-06-15
.venv/bin/python -m pytest -q
```

公共服务入口为 `OptimizationService(config).optimize('orders_with_items_count', date='2025-06-15')`，也可省略日期。非法日期继续由同一公共服务在数据库和模型调用前拒绝。未显式启用千问时，无论是否提供模型配置都不会发起模型请求；默认模式只接受默认/`normal` 的 EXISTS 候选及本地负向验收用的 `swapped-ids`，不接收外部 SQL。HTTP/MCP 也开放该场景，但由服务端配置选择模型或受控模式，参见后面的三入口速查。

同一 REPEATABLE READ 只读快照内，先查询两侧完整订单 ID，保存双向差集及各自数量，再实际查询两条计数 SQL。只有 ID 列信息、完整 ID 结果和最终计数都一致，且计数与纳入 ID 数相符，才进入预热和交替采样。ID 或计数不一致分别返回 `membership_mismatch` / `count_mismatch`，报告 `stage=result_validation`、`benchmark=null`，CLI 退出码为 1。仍沿用 10000 行/16 MiB、5 秒 SQL 超时、30 秒取数预算。

报告包含 `target_date`、原查询及候选 SQL、参数、`counts.join_count_distinct` 和 `counts.candidate_count`，以及 `membership.join_minus_candidate_count`、`membership.candidate_minus_join_count`、两侧完整 ID、自然计划和各 7 个计时样本。独立脚本复用后也采用这些字段；旧实测报告原样保留。**551 仅是当前测试数据的预期值，不是运行时通过条件**；其他日期及数据变化以实际查询为准，空结果也不因为缺少三类测试订单而被拒绝。只有独立固定数据验收脚本额外要求三类订单覆盖。

本轮本地真实 PostgreSQL 验收（受控候选，模型请求数均为 0）：

- `2025-06-15`：551/551，双向差集 0/0；报告为 `reports/order_items_count_scenario.json`。自然计划为 Aggregate → Sort → Inner Nested Loop 与 Aggregate → Semi Nested Loop，使用现有两个 B-tree 索引；中位数 0.503/0.400 ms，原始样本在报告中。这是本地热缓存测量，不保证其他负载收益。
- `swapped-ids`：仍为 551/551，但 JOIN 独有 ID 为 135617，候选独有 ID 为 135618，双向差集数量 1/1；报告为 `reports/order_items_count_swapped_ids.json`，拒绝并跳过计时。
- `2025-06-16`：548/548，双向差集 0/0，验证日期及计数动态传递。
- 原日期过滤场景：825/825，完整结果比较通过；独立明细基准入口回归通过。
- 59 项 pytest 测试通过（另含 11 个子测试），包括非 551 的动态计数、同计数不同 ID、ID 相同但计数不同、CLI 调用及接口范围保持不变。

## 订单明细计数：显式千问模式

公共服务直接调用及 CLI 的默认受控模式保持不变；公共服务可显式传入 `use_qwen=True`，CLI 使用 `--qwen` 启用明细场景模型建议。HTTP/MCP 则由本地服务端 `SQL_OPTIMIZATION_ITEMS_MODE` 配置选择，默认 `qwen`，也可选 `controlled`。三个入口均复用现有 `QWEN_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL` 与单次请求客户端。

```sh
# 真实模式：读取 .env，最多一次模型请求，不做探测、重试或格式修复请求
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15 --qwen
# 模拟模型响应，但仍实际读取和验证本地 PostgreSQL
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15 --qwen --fixture items-exists
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15 --qwen --fixture items-no-rewrite
# 格式/动作错误，预期退出码 1，未执行候选
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15 --qwen --fixture items-invalid
```

公共服务调用：`OptimizationService(config).optimize('orders_with_items_count', date='2025-06-15', use_qwen=True)`。模拟响应可通过构造服务时的 `fixture='items-exists'` 等参数选择。显式 `--qwen` 只用于该新场景；原日期场景仍沿用原命令和 `--fixture normal`。

模型只收到原 JOIN/COUNT DISTINCT 查询、日期范围参数、两张表的字段/主键/索引、相关约束及不带 ANALYZE 的估算计划摘要，不收到连接凭据、密码或业务行。先核查实际订单主键、ID 非空性和时间类型，再调用模型；诊断连接在等待模型时关闭。结构化响应严格限定为以下两种格式，不接受 SQL、表名、日期覆盖、其他动作、额外字段或重复 JSON 键：

```json
{"action":"exists","reason":"改写理由"}
```

```json
{"action":"no_rewrite","reason":"不建议改写的理由"}
```

执行入口 `run_action` 也只接受 `exists` 和 `no_rewrite`，不会把动作解释为旧的负向 fixture 或直接执行模型文本。`exists` 的 COUNT SQL 与订单 ID SQL 来自同一份 `EXISTS_FROM` 条件模板，并绑定同一对无时区日期范围参数。随后复用同一只读快照中的完整 ID、多重集/双向差集及最终计数验证，两项均通过才各预热一次、交替采样各 7 次。超时和取数预算与原引擎相同。

`no_rewrite` 返回同名状态、CLI 退出码 0，记录模型理由并结束，不运行候选验证或计时；无效输出在 `suggestion_validation` 阶段被拒绝，退出码 1。请求失败在 `model_request` 阶段记录脱敏状态，不自动重试。报告保留请求模型、实际模型 ID、原始回复、解析后的建议及模型请求次数，不以数据库引擎自身的零请求标记覆盖实际模型调用记录。

本轮验收报告分别保存：

- `reports/order_items_qwen_simulated.json`：模拟 `exists` 得到 551/551、双向差集 0/0；模拟 `no_rewrite` 正常结束；模拟无效动作被拒绝，未验证/计时。原日期场景回归为 825/825。模拟模型路径实际使用本地 PostgreSQL，没有真实模型请求。
- `reports/order_items_qwen_live.json`：真实 `qwen3.7-plus` 请求 **1 次**，返回 `{"action":"exists","reason":"explain using the actual constraints and membership semantics"}`。这是模型实际返回的示例式理由，报告原样保留，没有补写语义解释。数据库验证为 **551/551、双向差集 0/0**，在本次测试数据上结果一致。

真实运行自然计划为 Aggregate → Sort → Inner Nested Loop，与 Aggregate → Semi Nested Loop，均使用 `orders_created_at_idx` 和 `order_items_order_id_idx`。原查询 7 个执行时间（ms）为 `[0.518, 0.492, 0.498, 0.493, 0.499, 0.492, 0.480]`，候选为 `[0.395, 0.394, 0.410, 0.399, 0.387, 0.390, 0.386]`；中位数 **0.493 / 0.394 ms**，本次耗时减少约 **20.08%**，绝对差只有 **0.099 ms**，仅代表本地热缓存小样本。完整 ID、自然计划条件、缓冲区访问与原始计时都在真实报告中。

74 项 pytest 测试通过（另含 11 个子测试），覆盖两动作解析与执行白名单、同源 SQL 条件、显式模式选择、单次模型客户端、模拟响应及原有日期场景回归。真实链路报告在接口交付时复用，不重复调用真实模型。

## 两个场景的 CLI、HTTP、MCP 交付速查

三个入口复用同一公共服务、日期上下文和数据库验证引擎。默认日期只在公共服务定义为 `2025-06-15`；`date` 可省略，不能通过请求覆写模型配置或输入 SQL。模型只负责提出结构化改写动作：日期场景为 `date_range` / `no_suggestion`，明细场景为 `exists` / `no_rewrite`。程序验证动作后，用受控模板和绑定参数生成 SQL；完整结果或订单 ID/计数不一致时不会计时。

### CLI：两个场景

```sh
# 日期场景：真实模式读取现有 .env；加 --fixture normal 可改用可控建议
.venv/bin/python scripts/optimize_orders.py --scenario orders_date_filter_2025_06_15 --date 2025-06-16
# 明细场景：--qwen 才启用模型；省略该开关仍是原受控模式
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15 --qwen
# 无真实模型请求的明细动作验收
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --qwen --fixture items-exists
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --qwen --fixture items-no-rewrite
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --qwen --fixture items-invalid
```

### HTTP：启动和请求

真实模式启动（请求时才调用模型）：

```sh
.venv/bin/python -m uvicorn sql_optimization_agent.api:app --host 127.0.0.1 --port 8010
```

本地可控模型模式启动，两个场景均不发出真实模型请求（与上面的启动命令二选一）：

```sh
SQL_OPTIMIZATION_MODEL_FIXTURE=normal \
SQL_OPTIMIZATION_ITEMS_MODE=qwen \
SQL_OPTIMIZATION_ITEMS_FIXTURE=items-exists \
.venv/bin/python -m uvicorn sql_optimization_agent.api:app --host 127.0.0.1 --port 8010
```

另一个终端调用两个场景：

```sh
curl -sS http://127.0.0.1:8010/optimize -H 'Content-Type: application/json' \
  -d '{"scenario":"orders_date_filter_2025_06_15","date":"2025-06-16"}'
curl -sS http://127.0.0.1:8010/optimize -H 'Content-Type: application/json' \
  -d '{"scenario":"orders_with_items_count","date":"2025-06-15"}'
# 省略日期仍为 2025-06-15
curl -sS http://127.0.0.1:8010/optimize -H 'Content-Type: application/json' \
  -d '{"scenario":"orders_with_items_count"}'
```

### stdio MCP：两个场景

启动命令仍为 `.venv/bin/python -m sql_optimization_agent.mcp_server`，工具名仍为 `optimize_orders_date`，工具说明已列出两个场景。演示客户端会自动启动服务并转发服务端模型环境配置，无需额外启动进程：

```sh
# 真实模式；模型配置从服务端 .env 或环境读取
.venv/bin/python scripts/call_optimization_mcp.py --scenario orders_date_filter_2025_06_15 --date 2025-06-16
.venv/bin/python scripts/call_optimization_mcp.py --scenario orders_with_items_count --date 2025-06-15
# 可控模型建议 + 真实 PostgreSQL
SQL_OPTIMIZATION_MODEL_FIXTURE=normal SQL_OPTIMIZATION_ITEMS_FIXTURE=items-exists \
  .venv/bin/python scripts/call_optimization_mcp.py --scenario orders_with_items_count
```

直接工具参数分别为 `{"scenario":"orders_date_filter_2025_06_15","date":"2025-06-16"}` 和 `{"scenario":"orders_with_items_count","date":"2025-06-15"}`；两个场景都可省略 `date`。MCP 不开放模型模式、fixture 或 SQL 参数。

### 服务端模式、拒绝路径与可复现验收

以下开关仅由启动进程的本地环境设置，修改后应重启服务；不是 HTTP/MCP 请求字段：

| 配置 | 用途 |
| --- | --- |
| `QWEN_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL` | 继续从服务端 `.env` 或环境读取，不由调用方提交 |
| `SQL_OPTIMIZATION_MODEL_FIXTURE=normal` | 原日期场景的可控模型建议，未设置则沿用真实模式 |
| `SQL_OPTIMIZATION_ITEMS_MODE=qwen` | HTTP/MCP 明细场景默认模式；沿用真实模型配置 |
| `SQL_OPTIMIZATION_ITEMS_FIXTURE=items-exists` | 明细模型响应模拟；另可选 `items-no-rewrite`、`items-invalid` |
| `SQL_OPTIMIZATION_ITEMS_MODE=controlled` | 使用原受控候选，不调用模型；可搭配 items fixture `normal` 或 `swapped-ids` |

在上述可控 HTTP 启动命令中替换 items fixture 为 `items-no-rewrite`、`items-invalid`，可复现模型无建议和格式拒绝。结果不一致的本地负向验收用 `SQL_OPTIMIZATION_ITEMS_MODE=controlled SQL_OPTIMIZATION_ITEMS_FIXTURE=swapped-ids`；这是程序内的错误候选测试，并非给模型增加第三种执行动作。模拟模型 `exists` 的结果不一致门禁也有离线测试，验证即使最终计数相同，只要 ID 不同就跳过计时。

HTTP 对 `completed`、`no_suggestion`、`no_rewrite` 以及明确的候选拒绝结果返回 200，用 `status` 区分；非法日期/未知场景为 400，额外传入配置或 SQL 字段为 422，服务配置/模型请求异常为 503。MCP 对 `no_rewrite` 返回正常结果，拒绝及失败则 `isError=true`。无建议和拒绝报告都没有性能测量结果。

本地验收记录为 `reports/order_items_interfaces_acceptance.json`：实际 loopback HTTP 与 stdio MCP、模拟模型响应、真实 PostgreSQL，验收进程禁止真实模型调用。

- `exists`：两个入口均为 551/551，订单 ID 双向差集 0/0；省略日期报告 `2025-06-15`。
- 显式 `2025-06-16`：两个入口均为 548/548。
- `no_rewrite`：两个入口均正常结束，`benchmark=null`。
- 无效输出：两个入口均在建议校验阶段拒绝，未计时。
- 受控错误候选：计数仍为 551/551，但双向 ID 差集 1/1，两个入口均拒绝并跳过计时。
- 原日期场景回归：两个入口均为 825/825；非法日期仍在调用模型前停止。

### 两份真实千问基准（复用既有报告）

| 场景 / 目标日期 | 报告 | 实际结果 | 自然计划变化 | 原查询/候选中位数 |
| --- | --- | --- | --- | --- |
| 日期过滤 / 2025-06-16 | `reports/qwen_date_2025_06_16_live.json` | 823/823，完整多重集一致 | Parallel Seq Scan → Index Scan | 8.416 / 0.085 ms |
| 有明细订单计数 / 2025-06-15 | `reports/order_items_qwen_live.json` | 551/551，ID 双向差集 0/0 | Inner Nested Loop＋Sort → Semi Nested Loop | 0.493 / 0.394 ms |

两次均为实际 `qwen3.7-plus`、各 1 次请求，真实建议及全部 14 个样本、自然计划和缓冲区指标保留在对应报告。本次接口验收复用它们，未再次发出真实请求；验收报告记录了两份文件的 SHA-256，确认复用期间未变化。

测试环境：Mac 上 Python 3.12.13，Docker Compose `postgres:16`，实际 PostgreSQL 16.15（aarch64），`127.0.0.1:5434/sql_optimization`，应用账号 `sql_optimization_reader`；300005 条合成订单、400008 条合成明细。比较及计时使用 REPEATABLE READ 只读快照；各预热一次，再交替采样各 7 次，不强制计划。本地热缓存小样本的性能收益不代表其他数据或负载。

最终全量本地验收：84 项 pytest 测试通过（另含 11 个子测试），`pip check` 与 `git diff --check` 通过。CI 仍使用可控模型和数据库边界替身，不需要百炼密钥或本地数据库；真实 HTTP/stdio 及 PostgreSQL 的补充验收与 CI 测试来源在报告中分别标注。

## 小批量千问评测

`scripts/evaluate_qwen.py` 通过现有 `OptimizationService` 串行运行固定小矩阵，不增加通用 SQL 或 HTTP/MCP 参数：

| 场景 | 日期 | 每组重复 |
| --- | --- | --- |
| `orders_date_filter_2025_06_15` | 2025-06-15、2025-06-16、2026-01-01 | 2 次 |
| `orders_with_items_count` | 同上 | 2 次 |

共 12 个用例。日期/明细场景的模拟动作分别为 `date_range` / `exists`，重复运行相同可控建议；每次仍实际连接本地 PostgreSQL，不能把模拟模型结果标为真实调用。只有 `--live` 才读取现有百炼配置并请求真实模型，每例最多一次，整轮最多 12 次，无探测、重试或格式修复请求。

```sh
# 默认模拟响应；首次运行时会写 reports/qwen_evaluation_simulated.json
.venv/bin/python scripts/evaluate_qwen.py
# 仓库已带实测报告时，用新的输出路径复现，避免覆盖已有证据
.venv/bin/python scripts/evaluate_qwen.py --output /tmp/qwen-evaluation-simulated-new.json
# 显式真实评测：使用本机 .env 的密钥，不把密钥放入命令行
QWEN_MODEL=qwen3.7-plus .venv/bin/python scripts/evaluate_qwen.py --live --output /tmp/qwen-evaluation-live-new.json
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
git diff --check
```

输出路径已存在时在调用任何服务前退出（退出码 2），不会覆盖报告。每完成一例即通过临时文件原子替换保存进度；鉴权、额度/限流或模型配置错误会停止后续用例，保留失败例和此前完成的结果（退出码 1）。中断也保存已完成部分。完整运行且每例为验证通过或保留原查询时退出码 0；其他格式/验证失败仍逐例保留，并在最终返回失败退出码。

逐次报告字段包括：场景、日期、重复编号、实际模型 ID、原始回复、结构化建议及动作、`model_requests`、`model_request_ms`、整个优化流程的 `optimization_elapsed_ms`、PostgreSQL 校验结果和 SQL 执行中位数。`no_suggestion` / `no_rewrite` 统一记为 `decision=keep_original`，保留原查询，不运行候选比较或计时，`benchmark=null`。没有模型请求时请求耗时为 0；真实请求耗时包含 HTTP 客户端准备及响应解析，不包括数据库诊断、比较和计时。

成功候选仍使用同一只读快照、完整结果或 ID/计数门禁，再各预热一次、交替采样各 7 次。报告保存每例 14 个原始 SQL 样本、两个中位数和计划变体；相同节点元数据去重后记录使用它的样本编号，不丢弃耗时样本。重复组摘要记录两次动作、建议是否变化、校验结论是否变化；比较的是报告里的校验结果，不声称两次运行共享一个数据库快照。

评测报告会再次遮蔽本机 API Key 及数据库密码。此功能没有新增依赖，也不会修改 `.env`、造数脚本或基准数据。

### 本轮完整矩阵结果

- `reports/qwen_evaluation_simulated.json`：模拟模型、真实 PostgreSQL，12/12 例校验通过，真实模型请求数为 0。
- `reports/qwen_evaluation_live.json`：真实 `qwen3.7-plus`，恰好 12 次请求、12/12 例校验通过；6 次 `date_range`、6 次 `exists`，没有保留原查询或失败的用例。
- 六组重复调用的动作相同、验证结果相同；每组两次的建议理由文字不同。每一次原始回复均单独保留，不能把两次调用合并成一次或认为模型输出完全确定。
- 真实 HTTP 请求累计耗时约 63.685 秒，逐次耗时独立记录；它不是 SQL 执行时间。

| 场景 | 日期 | 两次校验结果 | 第 1 次原查询/候选中位数（ms） | 第 2 次原查询/候选中位数（ms） |
| --- | --- | --- | --- | --- |
| 日期过滤 | 2025-06-15 | 均 825/825，完整结果一致 | 6.750 / 0.072 | 7.517 / 0.077 |
| 日期过滤 | 2025-06-16 | 均 823/823，完整结果一致 | 6.701 / 0.065 | 6.823 / 0.072 |
| 日期过滤 | 2026-01-01 | 均 0/0，列信息及空结果一致 | 6.840 / 0.004 | 6.938 / 0.004 |
| 有明细计数 | 2025-06-15 | 均 551/551，ID 双向差集 0/0 | 0.659 / 0.514 | 0.627 / 0.496 |
| 有明细计数 | 2025-06-16 | 均 548/548，ID 双向差集 0/0 | 0.600 / 0.491 | 0.546 / 0.446 |
| 有明细计数 | 2026-01-01 | 均 0/0，ID 双向差集 0/0 | 0.008 / 0.006 | 0.010 / 0.008 |

所有一致性结论均为“在本次测试数据上结果一致”；空结果不能证明范围外日期的所有潜在数据也语义等价，仍需依靠边界用例和受控模板约束。性能数据是本地热缓存小样本，不能据此保证其他环境收益。鉴权/额度停止、保留原查询及断点落盘等异常路径由离线可控测试覆盖，本轮真实调用没有触发这些路径。

本轮全量测试 93 项通过（另含 11 个子测试）；`pip check`、`git diff --check` 与报告脱敏检查通过。既有 CI 可直接运行新测试，不需要真实 API Key 或本地 PostgreSQL。

## 本次采样的保守性能判定

公共服务在原有优化结果返回前追加 `performance_assessment`，CLI、HTTP 和 MCP 共用此字段，不改变既有 `status`、错误原因或成功/拒绝流程。复用现有 `benchmark.execution_ms` 的原始样本，不增加模型请求或重复 SQL 计时。已有中位数和百分比字段继续保留。

仅当报告明确为结果验证通过、两侧计时已完成且样本有效时判定：

| 判定 | 规则 |
| --- | --- |
| `improved` | 候选最慢样本严格快于原查询最快样本：`max(candidate) < min(original)` |
| `regressed` | 候选最快样本严格慢于原查询最慢样本：`min(candidate) > max(original)` |
| `inconclusive` | 两组区间重叠或相接，包括端点相等 |

报告同时给出两组区间、重新从原始样本计算的中位数、`median_difference_ms` 和 `median_change_percent`。差值定义为“候选 − 原查询”，负数表示候选中位数更低；百分比为 `(候选中位数 / 原查询中位数 − 1) × 100%`。原查询中位数为 0 时百分比为 JSON null，不做除零或替换成无穷值。

原查询保留（`no_suggestion` / `no_rewrite`）、验证不一致、模型/连接错误、计时缺失或非法样本时，`performance_assessment=null`，另附 `performance_assessment_skipped` 原因。明细场景还要求 ID 验证、双向差集、最终计数及纳入 ID 数一致；不会仅凭计数相同或旧的计时字段作判定。此规则只描述本次采样，不能解释为统计显著性或其他负载上的收益保证。

### 本地模拟建议 + PostgreSQL 验收

```sh
.venv/bin/python scripts/optimize_orders.py --fixture normal --date 2025-06-15
.venv/bin/python scripts/optimize_orders.py --scenario orders_with_items_count --date 2025-06-15 --qwen --fixture items-exists
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
git diff --check
```

本轮实际结果保存在 `reports/performance_assessment_local.json`，模型请求均为 0：

| 场景 | 验证 | 原查询样本区间（ms） | 候选样本区间（ms） | 中位数（原/候选，ms） | 差值（ms） | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| 日期过滤 | 825/825，完整结果一致 | 5.738–7.377 | 0.053–0.074 | 6.196 / 0.060 | -6.136 | improved |
| 有明细计数 | 551/551，ID 双向差集 0/0 | 0.393–0.467 | 0.317–0.338 | 0.407 / 0.325 | -0.082 | improved |

两者中位变化百分比分别约为 -99.03% 和 -20.15%，报告保留原始样本及自然计划。固定样本测试另覆盖明确退化、重叠、双向端点相等、零基线，以及结果不一致/保留原查询时跳过判定；HTTP/MCP 协议测试核对与公共服务字段一致。

### 复用上一轮真实报告，不重新调用模型

从已推送的批量评测提交 [`99c00c1`](https://github.com/Boxer-M/SQL-Optimization-Agent/blob/99c00c1354b9ad3b227d9ca2c74aca8038ec5cc1/reports/qwen_evaluation_live.json) 只读提取真实评测样本，生成 `reports/performance_assessment_reused_live.json`。该文件记录来源提交、报告路径、SHA-256、逐例验证证据、原始计时及复算判定，批量评测现已合入 main；保留其代码和原报告，不改写原始采样证据。

12 例复算为 **10 个 improved、2 个 inconclusive**，没有 regressed。范围外日期 `2026-01-01` 的明细计数两次样本区间未严格分离，因此即使候选中位数更低也判为 inconclusive。本轮没有额外真实千问请求。

全量本地测试 103 项通过（另含 11 个子测试）；`pip check`、`git diff --check` 及报告凭据排除检查通过。

合并最新 main 后的兼容性检查：112 项 pytest 测试通过（另含 11 个子测试），`pip check` 与差异检查通过；未重新调用模型或改写既有报告。
