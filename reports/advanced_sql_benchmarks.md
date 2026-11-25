# 复杂 JOIN 与大规模聚合实测报告

基线 `4c963ed7db9b26489a0bca6570c9db89840f5482`；确定性候选，真实千问请求 **0**。
结果只描述本次合成数据、自然计划和暖缓存采样，验收不以加速为条件。未做吞吐测试，不报告 QPS。
A/C/D samples were collected in the formal 300k/1m matrix. B was remeasured on the identical 300k uniform seed after making the paid-only transaction policy explicit. All B SQL, plans and samples below are from that paid-only recheck. Descriptive metadata and safety guards were completed without rerunning unchanged A/C SQL.

## 环境、口径与复现

```json
{
  "host": {
    "system": "Darwin",
    "machine": "arm64",
    "python": "3.12.13"
  },
  "postgresql": "PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) on aarch64-unknown-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit",
  "settings": {
    "effective_cache_size": {
      "setting": "524288",
      "unit": "8kB"
    },
    "hash_mem_multiplier": {
      "setting": "2",
      "unit": null
    },
    "jit": {
      "setting": "on",
      "unit": null
    },
    "max_parallel_workers_per_gather": {
      "setting": "2",
      "unit": null
    },
    "random_page_cost": {
      "setting": "4",
      "unit": null
    },
    "seq_page_cost": {
      "setting": "1",
      "unit": null
    },
    "shared_buffers": {
      "setting": "16384",
      "unit": "8kB"
    }
  },
  "budgets": {
    "result_rows": 120000,
    "result_bytes": 67108864,
    "statement_timeout_ms": 60000,
    "fetch_seconds": 30
  },
  "docker_runtime": {
    "os_type": "linux",
    "architecture": "aarch64",
    "cpus": 10,
    "memory_bytes": 8321515520
  }
}
```
每对查询在一个只读 REPEATABLE READ 快照比较列名/类型及完整多重集，原生保留 NULL、Decimal 和重复次数。
对账通过后复用现有 measure_pair：各预热一次，原/候选交替各 7 次。样本为 TIMING OFF 的服务端 EXPLAIN 执行时间，不含客户端传输。
P50 是样本中位数；P95 是最近秩法，n=7 时等于最大样本，不代表稳定尾延迟。自然计划不禁用扫描、连接、聚合或并行策略。
另行采集 EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)，timing-on 计划不计入 7 个样本；完整计划树、实际行数、loops、临时块均保存在同名 JSON。

完整复现命令 R1（先按 README 启动独立实例，仅操作带标记的测试库）：
```bash
.venv/bin/python scripts/benchmark_advanced.py --env-file .env.advanced --initialize \
  --sizes 300000 1000000 --seed 20261125 --report reports/advanced_sql_benchmarks.json
.venv/bin/python scripts/summarize_advanced_benchmarks.py \
  reports/advanced_sql_benchmarks.json --output reports/advanced_sql_benchmarks.md
ADVANCED_BENCHMARK_ENV=.env.advanced .venv/bin/python -m pytest -q
git diff --check
```
`--initialize` 拒绝非空且未标记的库；不要指向日常优化库。脚本不自动读取项目普通 .env。

## 数据分布与可重复装载

| 基础订单 | 分布 | 总订单 | 明细 | 用户 | 商品 | 退款 | 商家1占比 | 最大商家占比 | 客户组 | 商家组 | 地区×品类组 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 300000 | uniform | 300001 | 400002 | 100002 | 1000 | 60003 | 1.0000% | 1.0003% | 100000 | 100 | 168 |
| 300000 | hot | 300001 | 400002 | 100002 | 1000 | 60003 | 80.0000% | 80.0000% | 100000 | 100 | 168 |
| 1000000 | uniform | 1000001 | 1333335 | 100002 | 1000 | 200003 | 1.0000% | 1.0001% | 100000 | 100 | 168 |
| 1000000 | hot | 1000001 | 1333335 | 100002 | 1000 | 200003 | 80.0000% | 80.0000% | 100000 | 100 | 168 |

每档多 1 笔手工订单、2 条手工明细和 3 条退款；C 按 order_id < 900000000 排除手工订单，正好测 300000 / 1000000 基础订单。
均匀分布覆盖 100 商家；热点分布也是 100 商家，其中商家 1 占 80%，其余分散到 99 商家。客户分组基数固定 100000。
重复装载 300000 均匀数据，完整统计与订单内容指纹一致：**True**。所有五表行数不增长。
固定种子 20261125 的整数同余规则、日期范围、金额总和、订单指纹及 NULL 统计均在 JSON datasets 字段。
- 300000/uniform：NULL 日期 2654，NULL 金额 2362；NULL 维度 {'user_regions': 5883, 'product_categories': 32, 'product_prices': 10}；指纹 `1da22279252e5f4ea4db5ffc061711f9`。
- 300000/hot：NULL 日期 2654，NULL 金额 2362；NULL 维度 {'user_regions': 5883, 'product_categories': 32, 'product_prices': 10}；指纹 `bedfb05d4efa09035d7d0d35bc75bae6`。
- 1000000/uniform：NULL 日期 8849，NULL 金额 7874；NULL 维度 {'user_regions': 5883, 'product_categories': 32, 'product_prices': 10}；指纹 `8fe51d29d2324ee9ccdf66912db09e65`。
- 1000000/hot：NULL 日期 8849，NULL 金额 7874；NULL 维度 {'user_regions': 5883, 'product_categories': 32, 'product_prices': 10}；指纹 `766711924dae53a03a565bb03867fb48`。

基础索引（各档一致；额外索引只在独立 index 实验存在）：
```sql
CREATE INDEX order_items_order_id_idx ON public.order_items USING btree (order_id);
CREATE UNIQUE INDEX order_items_pkey ON public.order_items USING btree (item_id);
CREATE INDEX orders_created_at_idx ON public.orders USING btree (created_at);
CREATE INDEX orders_customer_idx ON public.orders USING btree (customer_id);
CREATE UNIQUE INDEX orders_pkey ON public.orders USING btree (order_id);
CREATE UNIQUE INDEX products_pkey ON public.products USING btree (product_id);
CREATE INDEX refunds_order_id_idx ON public.refunds USING btree (order_id);
CREATE UNIQUE INDEX refunds_pkey ON public.refunds USING btree (refund_id);
CREATE UNIQUE INDEX users_pkey ON public.users USING btree (customer_id);
```

## 全场景对账与性能总表

| case_id | 数据引用 | 原/候选结果行 | 对账 | P50 原/候选 ms | P95 原/候选 ms | 判定 | 推荐 | 溢出 原/候选 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_narrow | 300000/uniform | 154/154 | True | 6.241/7.903 | 6.317/8.807 | regressed | keep_original | False/False |
| A_wide | 300000/uniform | 168/168 | True | 690.844/523.342 | 751.373/533.655 | improved | use_candidate | True/True |
| B_correct | 300000/uniform | 100002/100002 | True | 14.441/12.774 | 17.643/13.663 | improved | use_candidate | False/False |
| B_wrong_fanout | 300000/uniform | 100002/100002 | False | —/— | —/— | 跳过 | keep_original | —/— |
| B_wrong_drops_users | 300000/uniform | 100002/695 | False | —/— | —/— | 跳过 | keep_original | —/— |
| C_300000_uniform_merchant_id | 300000/uniform | 100/100 | True | 152.04/19.958 | 156.121/20.828 | improved | use_candidate | True/False |
| C_300000_uniform_customer_id | 300000/uniform | 100000/100000 | True | 165.156/84.058 | 244.964/101.933 | improved | use_candidate | False/False |
| C_300000_hot_merchant_id | 300000/hot | 100/100 | True | 113.191/18.326 | 117.194/20.312 | improved | use_candidate | True/False |
| C_300000_hot_customer_id | 300000/hot | 100000/100000 | True | 142.029/82.875 | 149.481/88.948 | improved | use_candidate | False/False |
| C_1000000_uniform_merchant_id | 1000000/uniform | 100/100 | True | 657.172/49.261 | 680.668/55.75 | improved | use_candidate | True/False |
| C_1000000_uniform_customer_id | 1000000/uniform | 100000/100000 | True | 718.095/434.619 | 1040.756/506.569 | improved | use_candidate | True/False |
| C_1000000_hot_merchant_id | 1000000/hot | 100/100 | True | 312.262/46.805 | 407.152/62.296 | improved | use_candidate | True/False |
| C_1000000_hot_customer_id | 1000000/hot | 100000/100000 | True | 327.005/379.543 | 361.665/403.453 | inconclusive | keep_original | True/False |
| C_index_only | 1000000/hot | 100/100 | True | 330.012/51.151 | 455.112/69.782 | improved | use_candidate | True/False |
| C_work_mem_64kB | 1000000/hot | 100000/100000 | True | 239.945/382.809 | 277.766/412.278 | regressed | keep_original | False/False |
| C_work_mem_64MB | 1000000/hot | 100000/100000 | True | 380.983/158.547 | 668.056/240.013 | improved | use_candidate | False/False |

B 的成交口径只含范围内 paid 订单，销售额取明细数量×商品价格，退款独立求和；无交易用户返回 0/0。
A 汇总范围内全部订单，金额同样来自明细；按地区、品类逐组验证 amount、line_count、order_count。
C 基线为先按商家+客户聚合、再按目标键汇总；候选消除多余一级聚合。两者均保留 NULL SUM 语义。

## 单变量对照与收益边界

| 仅改变 | 参照 → 对照 | 原 SQL P50 ms | 候选 SQL P50 ms |
| --- | --- | --- | --- |
| 分布 | C_300000_uniform_merchant_id → C_300000_hot_merchant_id | 152.04 → 113.191 | 19.958 → 18.326 |
| 分布 | C_300000_uniform_customer_id → C_300000_hot_customer_id | 165.156 → 142.029 | 84.058 → 82.875 |
| 分组键 | C_300000_uniform_merchant_id → C_300000_uniform_customer_id | 152.04 → 165.156 | 19.958 → 84.058 |
| 分组键 | C_300000_hot_merchant_id → C_300000_hot_customer_id | 113.191 → 142.029 | 18.326 → 82.875 |
| 分布 | C_1000000_uniform_merchant_id → C_1000000_hot_merchant_id | 657.172 → 312.262 | 49.261 → 46.805 |
| 分布 | C_1000000_uniform_customer_id → C_1000000_hot_customer_id | 718.095 → 327.005 | 434.619 → 379.543 |
| 分组键 | C_1000000_uniform_merchant_id → C_1000000_uniform_customer_id | 657.172 → 718.095 | 49.261 → 434.619 |
| 分组键 | C_1000000_hot_merchant_id → C_1000000_hot_customer_id | 312.262 → 327.005 | 46.805 → 379.543 |
| 规模 | C_300000_uniform_merchant_id → C_1000000_uniform_merchant_id | 152.04 → 657.172 | 19.958 → 49.261 |
| 规模 | C_300000_uniform_customer_id → C_1000000_uniform_customer_id | 165.156 → 718.095 | 84.058 → 434.619 |
| 规模 | C_300000_hot_merchant_id → C_1000000_hot_merchant_id | 113.191 → 312.262 | 18.326 → 46.805 |
| 规模 | C_300000_hot_customer_id → C_1000000_hot_customer_id | 142.029 → 327.005 | 82.875 → 379.543 |
| 索引 | C_1000000_hot_merchant_id → C_index_only | 312.262 → 330.012 | 46.805 → 51.151 |
| work_mem | C_1000000_hot_customer_id → C_work_mem_64kB | 327.005 → 239.945 | 379.543 → 382.809 |
| work_mem | C_1000000_hot_customer_id → C_work_mem_64MB | 327.005 → 380.983 | 379.543 → 158.547 |
| 日期范围 | A_narrow → A_wide | 6.241 → 690.844 | 7.903 → 523.342 |
索引实验只增加 orders(merchant_id) B-tree，不重新采样表统计；work_mem 实验保持额外索引不存在。其他设置记录于环境段。
不同设置之间为依次执行的独立采样，并非交错随机化的因果实验；缓存、并行调度与系统噪声仍可能影响结果。只描述观测差异，不据多个同时变化的因素归因。
D 直接复用 A_narrow/A_wide：同一 300000 均匀数据、索引及 4MB work_mem，单日判定 regressed，全年判定 improved。没有为追求加速筛掉不利结果。
C_1000000_hot_customer_id：判定 inconclusive，推荐 keep_original，溢出原/候选 True/False。保留未溢出结果，没有强制 HashAggregate 来制造溢出。
C_work_mem_64kB：判定 regressed，推荐 keep_original，溢出原/候选 False/False。保留未溢出结果，没有强制 HashAggregate 来制造溢出。
C_work_mem_64MB：判定 improved，推荐 use_candidate，溢出原/候选 False/False。保留未溢出结果，没有强制 HashAggregate 来制造溢出。

## 逐场景 SQL、计划与原始样本

### A_narrow

复现 R1 后读取 JSON cases 中 `A_narrow`。基础订单 300000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}], "candidate": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}]}`。

original SQL：
```sql
SELECT u.region, p.category, SUM(i.quantity*p.price) AS amount,
 COUNT(*)::bigint AS line_count, COUNT(DISTINCT o.order_id)::bigint AS order_count
FROM public.orders o
JOIN public.order_items i ON i.order_id=o.order_id
JOIN public.products p ON p.product_id=i.product_id
JOIN public.users u ON u.customer_id=o.customer_id
WHERE o.created_at >= %s AND o.created_at < %s
GROUP BY u.region,p.category
```
SQL 文本表出现顺序：`[{"table": "public.orders AS o", "sql_offset": 159}, {"table": "public.order_items AS i", "sql_offset": 180}, {"table": "public.products AS p", "sql_offset": 231}, {"table": "public.users AS u", "sql_offset": 283}]`。

candidate SQL：
```sql
WITH filtered_orders AS MATERIALIZED (
 SELECT order_id,customer_id FROM public.orders WHERE created_at >= %s AND created_at < %s
), per_order_category AS (
 SELECT i.order_id,p.category,SUM(i.quantity*p.price) AS amount,COUNT(*) AS line_count
 FROM public.order_items i JOIN public.products p ON p.product_id=i.product_id
 JOIN filtered_orders f ON f.order_id=i.order_id
 GROUP BY i.order_id,p.category
)
SELECT u.region,c.category,SUM(c.amount) AS amount,
 SUM(c.line_count)::bigint AS line_count,COUNT(*)::bigint AS order_count
FROM filtered_orders f JOIN per_order_category c ON c.order_id=f.order_id
JOIN public.users u ON u.customer_id=f.customer_id
GROUP BY u.region,c.category
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 80}, {"table": "public.order_items AS i", "sql_offset": 257}, {"table": "public.products AS p", "sql_offset": 283}, {"table": "filtered_orders AS f", "sql_offset": 329}, {"table": "filtered_orders AS f", "sql_offset": 536}, {"table": "per_order_category AS c", "sql_offset": 559}, {"table": "public.users AS u", "sql_offset": 617}]`。
完整对账：`{"original_rows": 154, "candidate_rows": 154, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[6.241, 6.278, 6.197, 6.267, 6.24, 6.146, 6.317]`。
候选 7 个原始样本 ms：`[8.807, 7.98, 7.728, 7.973, 7.747, 7.79, 7.903]`。
P50：`{'original': 6.241, 'rewritten': 7.903}`；P95：`{'original': 6.317, 'rewritten': 8.807}`。
性能：`{'verdict': 'regressed', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 6.146, 'max': 6.317}, 'candidate_range_ms': {'min': 7.728, 'max': 8.807}, 'median_ms': {'original': 6.241, 'rewritten': 7.903}, 'median_difference_ms': 1.662, 'median_change_percent': 26.630347700688993, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'keep_original', 'reason_code': 'performance_regressed', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "users",
      "u",
      "0.0.0.0.0.0"
    ],
    [
      "orders",
      "o",
      "0.0.0.0.0.1.0"
    ],
    [
      "order_items",
      "i",
      "0.0.0.0.1"
    ],
    [
      "products",
      "p",
      "0.0.0.1.0"
    ]
  ],
  "joins": [
    {
      "path": "0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 1085,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Nested Loop",
      "Join Type": "Inner",
      "Actual Rows": 1085,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 812,
      "Actual Loops": 1
    }
  ],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "u.region",
        "p.category"
      ],
      "Actual Rows": 154,
      "Actual Loops": 1
    }
  ],
  "sorts": [
    {
      "path": "0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 1085,
      "Actual Loops": 1
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ],
    [
      "filtered_orders",
      "f_1",
      "0.1.0.0.0.0"
    ],
    [
      "order_items",
      "i",
      "0.1.0.0.0.1"
    ],
    [
      "products",
      "p",
      "0.1.0.0.1.0"
    ],
    [
      "filtered_orders",
      "f",
      "0.1.1.0.0"
    ],
    [
      "users",
      "u",
      "0.1.1.0.1.0"
    ]
  ],
  "joins": [
    {
      "path": "0.1",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 1085,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 1085,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0.0",
      "Node Type": "Nested Loop",
      "Join Type": "Inner",
      "Actual Rows": 1085,
      "Actual Loops": 1
    },
    {
      "path": "0.1.1.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 812,
      "Actual Loops": 1
    }
  ],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "u.region",
        "p.category"
      ],
      "Actual Rows": 154,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    },
    {
      "path": "0.1.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "i.order_id",
        "p.category"
      ],
      "Actual Rows": 1085,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### A_wide

复现 R1 后读取 JSON cases 中 `A_wide`。基础订单 300000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": [{"timestamp": "2025-01-01T00:00:00"}, {"timestamp": "2026-01-01T00:00:00"}], "candidate": [{"timestamp": "2025-01-01T00:00:00"}, {"timestamp": "2026-01-01T00:00:00"}]}`。

original SQL：
```sql
SELECT u.region, p.category, SUM(i.quantity*p.price) AS amount,
 COUNT(*)::bigint AS line_count, COUNT(DISTINCT o.order_id)::bigint AS order_count
FROM public.orders o
JOIN public.order_items i ON i.order_id=o.order_id
JOIN public.products p ON p.product_id=i.product_id
JOIN public.users u ON u.customer_id=o.customer_id
WHERE o.created_at >= %s AND o.created_at < %s
GROUP BY u.region,p.category
```
SQL 文本表出现顺序：`[{"table": "public.orders AS o", "sql_offset": 159}, {"table": "public.order_items AS i", "sql_offset": 180}, {"table": "public.products AS p", "sql_offset": 231}, {"table": "public.users AS u", "sql_offset": 283}]`。

candidate SQL：
```sql
WITH filtered_orders AS MATERIALIZED (
 SELECT order_id,customer_id FROM public.orders WHERE created_at >= %s AND created_at < %s
), per_order_category AS (
 SELECT i.order_id,p.category,SUM(i.quantity*p.price) AS amount,COUNT(*) AS line_count
 FROM public.order_items i JOIN public.products p ON p.product_id=i.product_id
 JOIN filtered_orders f ON f.order_id=i.order_id
 GROUP BY i.order_id,p.category
)
SELECT u.region,c.category,SUM(c.amount) AS amount,
 SUM(c.line_count)::bigint AS line_count,COUNT(*)::bigint AS order_count
FROM filtered_orders f JOIN per_order_category c ON c.order_id=f.order_id
JOIN public.users u ON u.customer_id=f.customer_id
GROUP BY u.region,c.category
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 80}, {"table": "public.order_items AS i", "sql_offset": 257}, {"table": "public.products AS p", "sql_offset": 283}, {"table": "filtered_orders AS f", "sql_offset": 329}, {"table": "filtered_orders AS f", "sql_offset": 536}, {"table": "per_order_category AS c", "sql_offset": 559}, {"table": "public.users AS u", "sql_offset": 617}]`。
完整对账：`{"original_rows": 168, "candidate_rows": 168, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[751.373, 696.582, 686.242, 687.64, 690.844, 711.948, 683.172]`。
候选 7 个原始样本 ms：`[527.163, 515.194, 514.539, 520.992, 533.655, 523.342, 529.074]`。
P50：`{'original': 690.844, 'rewritten': 523.342}`；P95：`{'original': 751.373, 'rewritten': 533.655}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 683.172, 'max': 751.373}, 'candidate_range_ms': {'min': 514.539, 'max': 533.655}, 'median_ms': {'original': 690.844, 'rewritten': 523.342}, 'median_difference_ms': -167.502, 'median_change_percent': -24.245994754242638, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "order_items",
      "i",
      "0.0.0.0.0.0"
    ],
    [
      "orders",
      "o",
      "0.0.0.0.0.1.0"
    ],
    [
      "products",
      "p",
      "0.0.0.0.1.0"
    ],
    [
      "users",
      "u",
      "0.0.0.1.0"
    ]
  ],
  "joins": [
    {
      "path": "0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 396462,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 396462,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 396462,
      "Actual Loops": 1
    }
  ],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "u.region",
        "p.category"
      ],
      "Actual Rows": 168,
      "Actual Loops": 1
    }
  ],
  "sorts": [
    {
      "path": "0.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 396462,
      "Actual Loops": 1
    }
  ],
  "spill_observed": true,
  "temp_read_blocks": 4728,
  "temp_written_blocks": 4736
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ],
    [
      "order_items",
      "i",
      "0.1.0.0.0.0.0"
    ],
    [
      "products",
      "p",
      "0.1.0.0.0.0.1.0"
    ],
    [
      "filtered_orders",
      "f_1",
      "0.1.0.0.0.1.0.0"
    ],
    [
      "filtered_orders",
      "f",
      "0.1.1.0.0.0"
    ],
    [
      "users",
      "u",
      "0.1.1.0.0.1.0"
    ]
  ],
  "joins": [
    {
      "path": "0.1",
      "Node Type": "Merge Join",
      "Join Type": "Inner",
      "Actual Rows": 396462,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0.0",
      "Node Type": "Merge Join",
      "Join Type": "Inner",
      "Actual Rows": 396462,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0.0.0",
      "Node Type": "Nested Loop",
      "Join Type": "Inner",
      "Actual Rows": 400002,
      "Actual Loops": 1
    },
    {
      "path": "0.1.1.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 297347,
      "Actual Loops": 1
    }
  ],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "u.region",
        "p.category"
      ],
      "Actual Rows": 168,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    },
    {
      "path": "0.1.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "i.order_id",
        "p.category"
      ],
      "Actual Rows": 396462,
      "Actual Loops": 1
    }
  ],
  "sorts": [
    {
      "path": "0.1.0.0",
      "Node Type": "Incremental Sort",
      "Actual Rows": 396462,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0.0.1.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 297347,
      "Actual Loops": 1
    },
    {
      "path": "0.1.1.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 297347,
      "Actual Loops": 1
    }
  ],
  "spill_observed": true,
  "temp_read_blocks": 2201,
  "temp_written_blocks": 2207
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### B_correct

复现 R1 后读取 JSON cases 中 `B_correct`。基础订单 300000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}, {"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}], "candidate": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}]}`。

original SQL：
```sql
SELECT u.customer_id,u.region,
 COALESCE(s.sales,0)::numeric AS sales,COALESCE(r.refund,0)::numeric AS refund
FROM public.users u
LEFT JOIN (
 SELECT o.customer_id,SUM(i.quantity*p.price) AS sales
 FROM public.orders o JOIN public.order_items i ON i.order_id=o.order_id
 JOIN public.products p ON p.product_id=i.product_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) s ON s.customer_id=u.customer_id
LEFT JOIN (
 SELECT o.customer_id,SUM(r.amount) AS refund
 FROM public.orders o JOIN public.refunds r ON r.order_id=o.order_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) r ON r.customer_id=u.customer_id
```
SQL 文本表出现顺序：`[{"table": "public.users AS u", "sql_offset": 122}, {"table": "public.orders AS o", "sql_offset": 210}, {"table": "public.order_items AS i", "sql_offset": 231}, {"table": "public.products AS p", "sql_offset": 283}, {"table": "public.orders AS o", "sql_offset": 520}, {"table": "public.refunds AS r", "sql_offset": 541}]`。

candidate SQL：
```sql
WITH filtered_orders AS MATERIALIZED (
 SELECT order_id,customer_id FROM public.orders WHERE created_at >= %s AND created_at < %s AND status='paid'
), line_totals AS (
 SELECT i.order_id,SUM(i.quantity*p.price) AS sales
 FROM public.order_items i JOIN public.products p ON p.product_id=i.product_id
 JOIN filtered_orders f ON f.order_id=i.order_id GROUP BY i.order_id
), refund_totals AS (
 SELECT r.order_id,SUM(r.amount) AS refund FROM public.refunds r
 JOIN filtered_orders f ON f.order_id=r.order_id GROUP BY r.order_id
), totals AS (
 SELECT f.customer_id,SUM(COALESCE(l.sales,0)) AS sales,SUM(COALESCE(r.refund,0)) AS refund
 FROM filtered_orders f LEFT JOIN line_totals l ON l.order_id=f.order_id
 LEFT JOIN refund_totals r ON r.order_id=f.order_id GROUP BY f.customer_id
)
SELECT u.customer_id,u.region,COALESCE(t.sales,0)::numeric AS sales,
 COALESCE(t.refund,0)::numeric AS refund
FROM public.users u LEFT JOIN totals t ON t.customer_id=u.customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 80}, {"table": "public.order_items AS i", "sql_offset": 233}, {"table": "public.products AS p", "sql_offset": 259}, {"table": "filtered_orders AS f", "sql_offset": 305}, {"table": "public.refunds AS r", "sql_offset": 445}, {"table": "filtered_orders AS f", "sql_offset": 461}, {"table": "filtered_orders AS f", "sql_offset": 637}, {"table": "line_totals AS l", "sql_offset": 665}, {"table": "refund_totals AS r", "sql_offset": 715}, {"table": "public.users AS u", "sql_offset": 903}, {"table": "totals AS t", "sql_offset": 921}]`。
完整对账：`{"original_rows": 100002, "candidate_rows": 100002, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[17.643, 14.122, 14.771, 15.02, 14.441, 14.198, 14.165]`。
候选 7 个原始样本 ms：`[13.663, 12.774, 13.026, 12.921, 12.374, 12.33, 12.299]`。
P50：`{'original': 14.441, 'rewritten': 12.774}`；P95：`{'original': 17.643, 'rewritten': 13.663}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 14.122, 'max': 17.643}, 'candidate_range_ms': {'min': 12.299, 'max': 13.663}, 'median_ms': {'original': 14.441, 'rewritten': 12.774}, 'median_difference_ms': -1.667, 'median_change_percent': -11.543521916764776, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "users",
      "u",
      "0.0.0"
    ],
    [
      "orders",
      "o",
      "0.0.1.0.0.0.0.0.0"
    ],
    [
      "order_items",
      "i",
      "0.0.1.0.0.0.0.0.1"
    ],
    [
      "products",
      "p",
      "0.0.1.0.0.0.0.1.0"
    ],
    [
      "refunds",
      "r_1",
      "0.1.0.0.0.0.0"
    ],
    [
      "orders",
      "o_1",
      "0.1.0.0.0.0.1.0"
    ]
  ],
  "joins": [
    {
      "path": "0",
      "Node Type": "Hash Join",
      "Join Type": "Left",
      "Actual Rows": 100002,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Hash Join",
      "Join Type": "Left",
      "Actual Rows": 100002,
      "Actual Loops": 1
    },
    {
      "path": "0.0.1.0.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 929,
      "Actual Loops": 1
    },
    {
      "path": "0.0.1.0.0.0.0.0",
      "Node Type": "Nested Loop",
      "Join Type": "Inner",
      "Actual Rows": 929,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 140,
      "Actual Loops": 1
    }
  ],
  "aggregates": [
    {
      "path": "0.0.1.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "o.customer_id"
      ],
      "Actual Rows": 464,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "o_1.customer_id"
      ],
      "Actual Rows": 138,
      "Actual Loops": 1
    }
  ],
  "sorts": [
    {
      "path": "0.0.1.0.0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 929,
      "Actual Loops": 1
    },
    {
      "path": "0.1.0.0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 140,
      "Actual Loops": 1
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ],
    [
      "users",
      "u",
      "0.1"
    ],
    [
      "filtered_orders",
      "f",
      "0.2.0.0.0.0.0"
    ],
    [
      "filtered_orders",
      "f_1",
      "0.2.0.0.0.0.1.0.0.0.0"
    ],
    [
      "refunds",
      "r_1",
      "0.2.0.0.0.0.1.0.0.0.1.0"
    ],
    [
      "filtered_orders",
      "f_2",
      "0.2.0.0.0.1.0.0.0.0.0"
    ],
    [
      "order_items",
      "i",
      "0.2.0.0.0.1.0.0.0.0.1"
    ],
    [
      "products",
      "p",
      "0.2.0.0.0.1.0.0.0.1.0"
    ]
  ],
  "joins": [
    {
      "path": "0",
      "Node Type": "Hash Join",
      "Join Type": "Left",
      "Actual Rows": 100002,
      "Actual Loops": 1
    },
    {
      "path": "0.2.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Left",
      "Actual Rows": 695,
      "Actual Loops": 1
    },
    {
      "path": "0.2.0.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Left",
      "Actual Rows": 695,
      "Actual Loops": 1
    },
    {
      "path": "0.2.0.0.0.0.1.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 140,
      "Actual Loops": 1
    },
    {
      "path": "0.2.0.0.0.1.0.0.0",
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Actual Rows": 929,
      "Actual Loops": 1
    },
    {
      "path": "0.2.0.0.0.1.0.0.0.0",
      "Node Type": "Nested Loop",
      "Join Type": "Inner",
      "Actual Rows": 929,
      "Actual Loops": 1
    }
  ],
  "aggregates": [
    {
      "path": "0.2.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "f.customer_id"
      ],
      "Actual Rows": 695,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    },
    {
      "path": "0.2.0.0.0.0.1.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "r_1.order_id"
      ],
      "Actual Rows": 138,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    },
    {
      "path": "0.2.0.0.0.1.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "i.order_id"
      ],
      "Actual Rows": 464,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### B_wrong_fanout

复现 R1 后读取 JSON cases 中 `B_wrong_fanout`。基础订单 300000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}, {"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}], "candidate": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}]}`。

original SQL：
```sql
SELECT u.customer_id,u.region,
 COALESCE(s.sales,0)::numeric AS sales,COALESCE(r.refund,0)::numeric AS refund
FROM public.users u
LEFT JOIN (
 SELECT o.customer_id,SUM(i.quantity*p.price) AS sales
 FROM public.orders o JOIN public.order_items i ON i.order_id=o.order_id
 JOIN public.products p ON p.product_id=i.product_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) s ON s.customer_id=u.customer_id
LEFT JOIN (
 SELECT o.customer_id,SUM(r.amount) AS refund
 FROM public.orders o JOIN public.refunds r ON r.order_id=o.order_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) r ON r.customer_id=u.customer_id
```
SQL 文本表出现顺序：`[{"table": "public.users AS u", "sql_offset": 122}, {"table": "public.orders AS o", "sql_offset": 210}, {"table": "public.order_items AS i", "sql_offset": 231}, {"table": "public.products AS p", "sql_offset": 283}, {"table": "public.orders AS o", "sql_offset": 520}, {"table": "public.refunds AS r", "sql_offset": 541}]`。

candidate SQL：
```sql
SELECT u.customer_id,u.region,
 COALESCE(SUM(i.quantity*p.price),0)::numeric AS sales,COALESCE(SUM(r.amount),0)::numeric AS refund
FROM public.users u LEFT JOIN public.orders o ON o.customer_id=u.customer_id
 AND o.created_at >= %s AND o.created_at < %s AND o.status='paid'
LEFT JOIN public.order_items i ON i.order_id=o.order_id
LEFT JOIN public.products p ON p.product_id=i.product_id
LEFT JOIN public.refunds r ON r.order_id=o.order_id
GROUP BY u.customer_id,u.region
```
SQL 文本表出现顺序：`[{"table": "public.users AS u", "sql_offset": 143}, {"table": "public.orders AS o", "sql_offset": 168}, {"table": "public.order_items AS i", "sql_offset": 291}, {"table": "public.products AS p", "sql_offset": 347}, {"table": "public.refunds AS r", "sql_offset": 404}]`。
完整对账：`{"original_rows": 100002, "candidate_rows": 100002, "columns_equal": true, "rows_and_multiplicities_equal": false, "equivalent": false, "missing_from_candidate": [{"row": [416, "region-1", {"decimal": "172.95"}, {"decimal": "9.90"}], "copies": 1}, {"row": [2496, "region-1", {"decimal": "136.95"}, {"decimal": "4.70"}], "copies": 1}, {"row": [3286, "region-1", {"decimal": "81.45"}, {"decimal": "9.60"}], "copies": 1}, {"row": [16556, "region-1", {"decimal": "209.95"}, {"decimal": "3.30"}], "copies": 1}, {"row": [17346, "region-1", {"decimal": "154.45"}, {"decimal": "8.20"}], "copies": 1}], "extra_in_candidate": [{"row": [416, "region-1", {"decimal": "172.95"}, {"decimal": "29.70"}], "copies": 1}, {"row": [2496, "region-1", {"decimal": "136.95"}, {"decimal": "14.10"}], "copies": 1}, {"row": [3286, "region-1", {"decimal": "81.45"}, {"decimal": "28.80"}], "copies": 1}, {"row": [16556, "region-1", {"decimal": "209.95"}, {"decimal": "9.90"}], "copies": 1}, {"row": [17346, "region-1", {"decimal": "154.45"}, {"decimal": "24.60"}], "copies": 1}]}`。
**结果不一致，在完整结果比较阶段拒绝；benchmark=null，无预热/计时/实际计划采集。**

### B_wrong_drops_users

复现 R1 后读取 JSON cases 中 `B_wrong_drops_users`。基础订单 300000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}, {"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}], "candidate": [{"timestamp": "2025-06-15T00:00:00"}, {"timestamp": "2025-06-16T00:00:00"}]}`。

original SQL：
```sql
SELECT u.customer_id,u.region,
 COALESCE(s.sales,0)::numeric AS sales,COALESCE(r.refund,0)::numeric AS refund
FROM public.users u
LEFT JOIN (
 SELECT o.customer_id,SUM(i.quantity*p.price) AS sales
 FROM public.orders o JOIN public.order_items i ON i.order_id=o.order_id
 JOIN public.products p ON p.product_id=i.product_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) s ON s.customer_id=u.customer_id
LEFT JOIN (
 SELECT o.customer_id,SUM(r.amount) AS refund
 FROM public.orders o JOIN public.refunds r ON r.order_id=o.order_id
 WHERE o.created_at >= %s AND o.created_at < %s AND o.status='paid' GROUP BY o.customer_id
) r ON r.customer_id=u.customer_id
```
SQL 文本表出现顺序：`[{"table": "public.users AS u", "sql_offset": 122}, {"table": "public.orders AS o", "sql_offset": 210}, {"table": "public.order_items AS i", "sql_offset": 231}, {"table": "public.products AS p", "sql_offset": 283}, {"table": "public.orders AS o", "sql_offset": 520}, {"table": "public.refunds AS r", "sql_offset": 541}]`。

candidate SQL：
```sql
WITH filtered_orders AS MATERIALIZED (
 SELECT order_id,customer_id FROM public.orders WHERE created_at >= %s AND created_at < %s AND status='paid'
), line_totals AS (
 SELECT i.order_id,SUM(i.quantity*p.price) AS sales
 FROM public.order_items i JOIN public.products p ON p.product_id=i.product_id
 JOIN filtered_orders f ON f.order_id=i.order_id GROUP BY i.order_id
), refund_totals AS (
 SELECT r.order_id,SUM(r.amount) AS refund FROM public.refunds r
 JOIN filtered_orders f ON f.order_id=r.order_id GROUP BY r.order_id
), totals AS (
 SELECT f.customer_id,SUM(COALESCE(l.sales,0)) AS sales,SUM(COALESCE(r.refund,0)) AS refund
 FROM filtered_orders f LEFT JOIN line_totals l ON l.order_id=f.order_id
 LEFT JOIN refund_totals r ON r.order_id=f.order_id GROUP BY f.customer_id
)
SELECT u.customer_id,u.region,COALESCE(t.sales,0)::numeric AS sales,
 COALESCE(t.refund,0)::numeric AS refund
FROM public.users u LEFT JOIN totals t ON t.customer_id=u.customer_id
WHERE t.customer_id IS NOT NULL
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 80}, {"table": "public.order_items AS i", "sql_offset": 233}, {"table": "public.products AS p", "sql_offset": 259}, {"table": "filtered_orders AS f", "sql_offset": 305}, {"table": "public.refunds AS r", "sql_offset": 445}, {"table": "filtered_orders AS f", "sql_offset": 461}, {"table": "filtered_orders AS f", "sql_offset": 637}, {"table": "line_totals AS l", "sql_offset": 665}, {"table": "refund_totals AS r", "sql_offset": 715}, {"table": "public.users AS u", "sql_offset": 903}, {"table": "totals AS t", "sql_offset": 921}]`。
完整对账：`{"original_rows": 100002, "candidate_rows": 695, "columns_equal": true, "rows_and_multiplicities_equal": false, "equivalent": false, "missing_from_candidate": [{"row": [1, "region-1", {"decimal": "0"}, {"decimal": "0"}], "copies": 1}, {"row": [2, "region-2", {"decimal": "0"}, {"decimal": "0"}], "copies": 1}, {"row": [3, "region-3", {"decimal": "0"}, {"decimal": "0"}], "copies": 1}, {"row": [4, "region-4", {"decimal": "0"}, {"decimal": "0"}], "copies": 1}, {"row": [5, "region-0", {"decimal": "0"}, {"decimal": "0"}], "copies": 1}], "extra_in_candidate": []}`。
**结果不一致，在完整结果比较阶段拒绝；benchmark=null，无预热/计时/实际计划采集。**

### C_300000_uniform_merchant_id

复现 R1 后读取 JSON cases 中 `C_300000_uniform_merchant_id`。基础订单 300000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT merchant_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT merchant_id,customer_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id,customer_id) t GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT merchant_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100, "candidate_rows": 100, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[151.15, 156.121, 155.41, 150.027, 152.605, 152.04, 149.359]`。
候选 7 个原始样本 ms：`[19.958, 20.501, 20.357, 20.828, 19.705, 19.848, 19.806]`。
P50：`{'original': 152.04, 'rewritten': 19.958}`；P95：`{'original': 156.121, 'rewritten': 20.828}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 149.359, 'max': 156.121}, 'candidate_range_ms': {'min': 19.705, 'max': 20.828}, 'median_ms': {'original': 152.04, 'rewritten': 19.958}, 'median_difference_ms': -132.082, 'median_change_percent': -86.87319126545646, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 300000,
      "Actual Loops": 1,
      "HashAgg Batches": 37,
      "Disk Usage": 11496
    }
  ],
  "sorts": [],
  "spill_observed": true,
  "temp_read_blocks": 2162,
  "temp_written_blocks": 3277
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 2,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [
    {
      "path": "0.0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 100,
      "Actual Loops": 2
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_300000_uniform_customer_id

复现 R1 后读取 JSON cases 中 `C_300000_uniform_customer_id`。基础订单 300000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT customer_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT customer_id,merchant_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id,merchant_id) t GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT customer_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100000, "candidate_rows": 100000, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[166.349, 163.179, 163.638, 165.156, 244.964, 187.844, 164.139]`。
候选 7 个原始样本 ms：`[84.058, 85.769, 81.852, 81.468, 86.417, 101.933, 81.874]`。
P50：`{'original': 165.156, 'rewritten': 84.058}`；P95：`{'original': 244.964, 'rewritten': 101.933}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 163.179, 'max': 244.964}, 'candidate_range_ms': {'min': 81.468, 'max': 101.933}, 'median_ms': {'original': 165.156, 'rewritten': 84.058}, 'median_difference_ms': -81.098, 'median_change_percent': -49.10387754607765, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 300000,
      "Actual Loops": 1
    }
  ],
  "sorts": [
    {
      "path": "0.0.0",
      "Node Type": "Incremental Sort",
      "Actual Rows": 300000,
      "Actual Loops": 1
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_300000_hot_merchant_id

复现 R1 后读取 JSON cases 中 `C_300000_hot_merchant_id`。基础订单 300000，hot，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT merchant_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT merchant_id,customer_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id,customer_id) t GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT merchant_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100, "candidate_rows": 100, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[116.632, 113.191, 117.194, 107.133, 109.082, 113.511, 108.975]`。
候选 7 个原始样本 ms：`[18.38, 18.326, 19.856, 17.996, 18.053, 18.296, 20.312]`。
P50：`{'original': 113.191, 'rewritten': 18.326}`；P95：`{'original': 117.194, 'rewritten': 20.312}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 107.133, 'max': 117.194}, 'candidate_range_ms': {'min': 17.996, 'max': 20.312}, 'median_ms': {'original': 113.191, 'rewritten': 18.326}, 'median_difference_ms': -94.865, 'median_change_percent': -83.80966684630403, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 140000,
      "Actual Loops": 1,
      "HashAgg Batches": 21,
      "Disk Usage": 11224
    }
  ],
  "sorts": [],
  "spill_observed": true,
  "temp_read_blocks": 1402,
  "temp_written_blocks": 2518
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 2,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [
    {
      "path": "0.0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 100,
      "Actual Loops": 2
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_300000_hot_customer_id

复现 R1 后读取 JSON cases 中 `C_300000_hot_customer_id`。基础订单 300000，hot，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT customer_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT customer_id,merchant_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id,merchant_id) t GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT customer_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100000, "candidate_rows": 100000, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[142.029, 144.513, 139.373, 142.408, 139.716, 141.168, 149.481]`。
候选 7 个原始样本 ms：`[82.875, 83.366, 81.174, 82.803, 86.17, 82.194, 88.948]`。
P50：`{'original': 142.029, 'rewritten': 82.875}`；P95：`{'original': 149.481, 'rewritten': 88.948}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 139.373, 'max': 149.481}, 'candidate_range_ms': {'min': 81.174, 'max': 88.948}, 'median_ms': {'original': 142.029, 'rewritten': 82.875}, 'median_difference_ms': -59.154, 'median_change_percent': -41.64924064803667, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 140000,
      "Actual Loops": 1
    }
  ],
  "sorts": [
    {
      "path": "0.0.0",
      "Node Type": "Incremental Sort",
      "Actual Rows": 300000,
      "Actual Loops": 1
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_1000000_uniform_merchant_id

复现 R1 后读取 JSON cases 中 `C_1000000_uniform_merchant_id`。基础订单 1000000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT merchant_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT merchant_id,customer_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id,customer_id) t GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT merchant_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100, "candidate_rows": 100, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[657.172, 675.298, 637.05, 639.37, 658.062, 680.668, 630.802]`。
候选 7 个原始样本 ms：`[50.185, 52.437, 48.348, 48.829, 55.75, 49.261, 48.729]`。
P50：`{'original': 657.172, 'rewritten': 49.261}`；P95：`{'original': 680.668, 'rewritten': 55.75}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 630.802, 'max': 680.668}, 'candidate_range_ms': {'min': 48.348, 'max': 55.75}, 'median_ms': {'original': 657.172, 'rewritten': 49.261}, 'median_difference_ms': -607.911, 'median_change_percent': -92.50409329673207, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 1000000,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 333333,
      "Actual Loops": 3,
      "HashAgg Batches": 37,
      "Disk Usage": 15208
    }
  ],
  "sorts": [
    {
      "path": "0.0.0.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 333333,
      "Actual Loops": 3
    }
  ],
  "spill_observed": true,
  "temp_read_blocks": 19355,
  "temp_written_blocks": 23138
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 3,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [
    {
      "path": "0.0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 100,
      "Actual Loops": 3
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_1000000_uniform_customer_id

复现 R1 后读取 JSON cases 中 `C_1000000_uniform_customer_id`。基础订单 1000000，uniform，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT customer_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT customer_id,merchant_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id,merchant_id) t GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT customer_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100000, "candidate_rows": 100000, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[696.242, 718.095, 1040.756, 815.167, 695.813, 729.354, 659.134]`。
候选 7 个原始样本 ms：`[423.635, 378.759, 506.569, 495.466, 413.759, 434.619, 506.087]`。
P50：`{'original': 718.095, 'rewritten': 434.619}`；P95：`{'original': 1040.756, 'rewritten': 506.569}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 659.134, 'max': 1040.756}, 'candidate_range_ms': {'min': 378.759, 'max': 506.569}, 'median_ms': {'original': 718.095, 'rewritten': 434.619}, 'median_difference_ms': -283.476, 'median_change_percent': -39.47611388465314, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：较大波动：至少一组极差超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 1000000,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 333333,
      "Actual Loops": 3,
      "HashAgg Batches": 53,
      "Disk Usage": 15448
    }
  ],
  "sorts": [
    {
      "path": "0.0.0.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 333333,
      "Actual Loops": 3
    }
  ],
  "spill_observed": true,
  "temp_read_blocks": 19365,
  "temp_written_blocks": 23127
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_1000000_hot_merchant_id

复现 R1 后读取 JSON cases 中 `C_1000000_hot_merchant_id`。基础订单 1000000，hot，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT merchant_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT merchant_id,customer_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id,customer_id) t GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT merchant_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100, "candidate_rows": 100, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[304.897, 303.497, 308.672, 375.837, 327.684, 312.262, 407.152]`。
候选 7 个原始样本 ms：`[43.932, 43.385, 46.805, 61.351, 50.276, 44.433, 62.296]`。
P50：`{'original': 312.262, 'rewritten': 46.805}`；P95：`{'original': 407.152, 'rewritten': 62.296}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 303.497, 'max': 407.152}, 'candidate_range_ms': {'min': 43.385, 'max': 62.296}, 'median_ms': {'original': 312.262, 'rewritten': 46.805}, 'median_difference_ms': -265.457, 'median_change_percent': -85.0109843656929, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 280000,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 145166,
      "Actual Loops": 3,
      "HashAgg Batches": 21,
      "Disk Usage": 11704
    }
  ],
  "sorts": [
    {
      "path": "0.0.0.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 145166,
      "Actual Loops": 3
    }
  ],
  "spill_observed": true,
  "temp_read_blocks": 9684,
  "temp_written_blocks": 13040
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 3,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [
    {
      "path": "0.0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 100,
      "Actual Loops": 3
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_1000000_hot_customer_id

复现 R1 后读取 JSON cases 中 `C_1000000_hot_customer_id`。基础订单 1000000，hot，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT customer_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT customer_id,merchant_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id,merchant_id) t GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT customer_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100000, "candidate_rows": 100000, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[346.248, 337.619, 310.015, 361.665, 327.005, 314.763, 310.072]`。
候选 7 个原始样本 ms：`[391.675, 381.364, 379.543, 403.453, 374.317, 354.461, 365.307]`。
P50：`{'original': 327.005, 'rewritten': 379.543}`；P95：`{'original': 361.665, 'rewritten': 403.453}`。
性能：`{'verdict': 'inconclusive', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 310.015, 'max': 361.665}, 'candidate_range_ms': {'min': 354.461, 'max': 403.453}, 'median_ms': {'original': 327.005, 'rewritten': 379.543}, 'median_difference_ms': 52.538, 'median_change_percent': 16.066421002736963, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'keep_original', 'reason_code': 'performance_inconclusive', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 280000,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 145322,
      "Actual Loops": 3,
      "HashAgg Batches": 21,
      "Disk Usage": 11656
    }
  ],
  "sorts": [
    {
      "path": "0.0.0.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 145322,
      "Actual Loops": 3
    }
  ],
  "spill_observed": true,
  "temp_read_blocks": 9676,
  "temp_written_blocks": 13036
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_index_only

复现 R1 后读取 JSON cases 中 `C_index_only`。基础订单 1000000，hot，work_mem 4MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT merchant_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT merchant_id,customer_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id,customer_id) t GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT merchant_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY merchant_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100, "candidate_rows": 100, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[326.769, 332.949, 318.43, 455.112, 400.319, 330.012, 326.821]`。
候选 7 个原始样本 ms：`[69.782, 44.879, 45.295, 62.351, 51.92, 49.04, 51.151]`。
P50：`{'original': 330.012, 'rewritten': 51.151}`；P95：`{'original': 455.112, 'rewritten': 69.782}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 318.43, 'max': 455.112}, 'candidate_range_ms': {'min': 44.879, 'max': 69.782}, 'median_ms': {'original': 330.012, 'rewritten': 51.151}, 'median_difference_ms': -278.861, 'median_change_percent': -84.50026059658437, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 280000,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.merchant_id",
        "orders.customer_id"
      ],
      "Actual Rows": 145119,
      "Actual Loops": 3,
      "HashAgg Batches": 21,
      "Disk Usage": 11672
    }
  ],
  "sorts": [
    {
      "path": "0.0.0.0",
      "Node Type": "Sort",
      "Sort Method": "external merge",
      "Sort Space Type": "Disk",
      "Actual Rows": 145119,
      "Actual Loops": 3
    }
  ],
  "spill_observed": true,
  "temp_read_blocks": 9729,
  "temp_written_blocks": 13089
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "merchant_id"
      ],
      "Actual Rows": 100,
      "Actual Loops": 3,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [
    {
      "path": "0.0.0",
      "Node Type": "Sort",
      "Sort Method": "quicksort",
      "Sort Space Type": "Memory",
      "Actual Rows": 100,
      "Actual Loops": 3
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_work_mem_64kB

复现 R1 后读取 JSON cases 中 `C_work_mem_64kB`。基础订单 1000000，hot，work_mem 64kB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT customer_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT customer_id,merchant_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id,merchant_id) t GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT customer_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100000, "candidate_rows": 100000, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[242.846, 277.766, 239.945, 229.256, 229.626, 228.503, 258.143]`。
候选 7 个原始样本 ms：`[382.809, 366.198, 412.278, 387.459, 379.758, 388.907, 366.776]`。
P50：`{'original': 239.945, 'rewritten': 382.809}`；P95：`{'original': 277.766, 'rewritten': 412.278}`。
性能：`{'verdict': 'regressed', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 228.503, 'max': 277.766}, 'candidate_range_ms': {'min': 366.198, 'max': 412.278}, 'median_ms': {'original': 239.945, 'rewritten': 382.809}, 'median_difference_ms': 142.864, 'median_change_percent': 59.5403113213445, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'keep_original', 'reason_code': 'performance_regressed', 'advisory_only': True}`。
样本波动：本次两组极差均不超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0.0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 280000,
      "Actual Loops": 1
    },
    {
      "path": "0.0.0.0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 93333,
      "Actual Loops": 3
    }
  ],
  "sorts": [
    {
      "path": "0.0.0.0.0",
      "Node Type": "Incremental Sort",
      "Actual Rows": 333333,
      "Actual Loops": 3
    }
  ],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Sorted",
      "Group Key": [
        "customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

### C_work_mem_64MB

复现 R1 后读取 JSON cases 中 `C_work_mem_64MB`。基础订单 1000000，hot，work_mem 64MB。
原/候选绑定参数：`{"original": null, "candidate": null}`。

original SQL：
```sql
SELECT customer_id,SUM(subtotal) AS amount,SUM(n)::bigint AS order_count
FROM (SELECT customer_id,merchant_id,SUM(amount) AS subtotal,COUNT(*) AS n
 FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id,merchant_id) t GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 161}]`。

candidate SQL：
```sql
SELECT customer_id,SUM(amount) AS amount,COUNT(*)::bigint AS order_count
FROM public.orders WHERE order_id < 900000000 GROUP BY customer_id
```
SQL 文本表出现顺序：`[{"table": "public.orders", "sql_offset": 85}]`。
完整对账：`{"original_rows": 100000, "candidate_rows": 100000, "columns_equal": true, "rows_and_multiplicities_equal": true, "equivalent": true, "missing_from_candidate": [], "extra_in_candidate": []}`。
原查询 7 个原始样本 ms：`[340.117, 335.69, 340.123, 668.056, 380.983, 385.286, 384.854]`。
候选 7 个原始样本 ms：`[153.038, 155.87, 155.356, 231.48, 173.437, 240.013, 158.547]`。
P50：`{'original': 380.983, 'rewritten': 158.547}`；P95：`{'original': 668.056, 'rewritten': 240.013}`。
性能：`{'verdict': 'improved', 'rule': 'strict_sample_interval_separation', 'original_range_ms': {'min': 335.69, 'max': 668.056}, 'candidate_range_ms': {'min': 153.038, 'max': 240.013}, 'median_ms': {'original': 380.983, 'rewritten': 158.547}, 'median_difference_ms': -222.436, 'median_change_percent': -58.384757325129996, 'sample_counts': {'original': 7, 'rewritten': 7}, 'scope': 'This sampling run only; not a general performance guarantee'}`；推荐：`{'decision': 'use_candidate', 'reason_code': 'validated_and_improved', 'advisory_only': True}`。
样本波动：较大波动：至少一组极差超过中位数的 50%。

original 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    },
    {
      "path": "0.0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "orders.customer_id",
        "orders.merchant_id"
      ],
      "Actual Rows": 280000,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```

candidate 自然实际计划摘要：
```json
{
  "scans": [
    [
      "orders",
      "orders",
      "0.0"
    ]
  ],
  "joins": [],
  "aggregates": [
    {
      "path": "0",
      "Node Type": "Aggregate",
      "Strategy": "Hashed",
      "Group Key": [
        "customer_id"
      ],
      "Actual Rows": 100000,
      "Actual Loops": 1,
      "HashAgg Batches": 1,
      "Disk Usage": 0
    }
  ],
  "sorts": [],
  "spill_observed": false,
  "temp_read_blocks": 0,
  "temp_written_blocks": 0
}
```
路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。

## 手工语义样本与固定计时控制

A 的补充只读 VALUES 夹具：同订单、同 NULL 品类的三条明细合计 40，line_count=3、order_count=1；另一个全 NULL 价格组 SUM 保持 NULL。两条模板完整对账通过，不作为性能样本。
```json
[
  {
    "row": [
      null,
      null,
      {
        "decimal": "40"
      },
      3,
      1
    ],
    "copies": 1
  },
  {
    "row": [
      null,
      "all_null",
      null,
      1,
      1
    ],
    "copies": 1
  }
]
```

```json
{
  "expected": [
    {
      "row": [
        100001,
        "manual",
        {
          "decimal": "30"
        },
        {
          "decimal": "10"
        }
      ],
      "copies": 1
    },
    {
      "row": [
        100002,
        null,
        {
          "decimal": "0"
        },
        {
          "decimal": "0"
        }
      ],
      "copies": 1
    }
  ],
  "observed": {
    "original": [
      {
        "row": [
          100001,
          "manual",
          {
            "decimal": "30.00"
          },
          {
            "decimal": "10.00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          null,
          {
            "decimal": "0"
          },
          {
            "decimal": "0"
          }
        ],
        "copies": 1
      }
    ],
    "candidate": [
      {
        "row": [
          100001,
          "manual",
          {
            "decimal": "30.00"
          },
          {
            "decimal": "10.00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          null,
          {
            "decimal": "0"
          },
          {
            "decimal": "0"
          }
        ],
        "copies": 1
      }
    ],
    "fanout": [
      {
        "row": [
          100001,
          "manual",
          {
            "decimal": "90.00"
          },
          {
            "decimal": "20.00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          null,
          {
            "decimal": "0"
          },
          {
            "decimal": "0"
          }
        ],
        "copies": 1
      }
    ]
  },
  "correct_queries_pass": true,
  "wrong_fanout_expected_sales": 90,
  "wrong_fanout_expected_refund": 20
}
```
两条正确 SQL 都返回用户 100001 销售 30、退款 10；用户 100002 无交易且 region=NULL，返回 0/0。错误 2×3 JOIN 返回 90/20。

- **固定样本，非 PostgreSQL 实测** `overlap_lower_median`：原 [8, 10, 12, 9, 11, 10, 10]，候选 [7, 8, 9, 9, 10, 11, 12]；判定 `inconclusive`，推荐 `keep_original`。

- **固定样本，非 PostgreSQL 实测** `regressed`：原 [1, 2, 3, 2, 2, 2, 2]，候选 [4, 5, 6, 5, 5, 5, 5]；判定 `regressed`，推荐 `keep_original`。
重叠控制的候选中位数更低仍 keep_original；明确退化控制也 keep_original。

## 回归与限制

带隔离 PostgreSQL 的全量 pytest：333 passed，11 subtests passed；1 条已有 Starlette/httpx 弃用警告。pip check 无冲突，git diff --check 通过。
默认离线 pytest 跳过 6 个数据库用例；仅显式 ADVANCED_BENCHMARK_ENV 配置、只读账号且数据库标记一致时开放这些集成连接。其余测试与全部 HTTP 仍受原离线保护。
报告不含环境文件、凭据或真实业务数据。合成数据的同余规则存在相关性，不作为生产分布或普遍优化收益证据；每次运行应以新的自然计划、区间和推荐为准。
本轮未创建 PR、未合并、未发布 v0.2.2。
