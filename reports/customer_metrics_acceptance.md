# 百万订单客户经营报表优化验收

开发基线 `ca0162f20370756ff3aa203ab7f59e2a4ff4ae70`，分支 feat/customer-metrics-optimization。高级基准未合并时，先合并 test/advanced-sql-benchmarks，再合并本分支。v0.2.2 发布独立推进。

## 数据与语义

固定种子 20261125；实际订单 **1000000**，客户 **100004**。
有订单客户 100003；NULL 金额订单 7875；NULL 时间订单 8850。
999992 基础订单加 8 条手工样本。全年范围为 [2025-01-01,2026-01-01)，timestamp without time zone。
订单数统计全部状态；成交额仅 SUM(paid orders.amount)，保留 NULL；最近下单时间是所有范围内订单 MAX(created_at)。无订单为 0/NULL/NULL，全 NULL 金额客户为 2/NULL/具体最后时间。
年度范围内每客户订单数分布（订单数,客户数）：
```json
[
  [
    0,
    1
  ],
  [
    1,
    1
  ],
  [
    2,
    2
  ],
  [
    9,
    8857
  ],
  [
    10,
    91143
  ]
]
```
重复装载一致：True；订单指纹 `a866042204d5511a7ee1752caec4428a`，客户指纹 `7c27d7aa9d8c194e000df24774b10dbe`。重复造数未重跑模型。

## 环境与公平性

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
    "shared_buffers": {
      "setting": "16384",
      "unit": "8kB"
    },
    "work_mem": {
      "setting": "4096",
      "unit": "kB"
    }
  }
}
```
独立 Compose 项目 sql-agent-customer-metrics，专用端口 64630 与独立数据卷；原优化库和原工作区未重置。
原查询与候选共用以下索引，并先 VACUUM ANALYZE。两侧实测订单索引访问 Heap Fetches=0，没有故意让原查询回表或缺索引。
```sql
CREATE INDEX orders_created_at_idx ON public.orders USING btree (created_at);
CREATE INDEX orders_customer_created_cover_idx ON public.orders USING btree (customer_id, created_at) INCLUDE (amount, status);
CREATE INDEX orders_customer_idx ON public.orders USING btree (customer_id);
CREATE UNIQUE INDEX orders_pkey ON public.orders USING btree (order_id);
CREATE UNIQUE INDEX users_pkey ON public.users USING btree (customer_id);
```
执行时 work_mem=4MB，60 秒 statement_timeout；同一只读 REPEATABLE READ 快照完成全部客户的列元数据与多重集比较，NULL、Decimal 和重复次数不被转换成浮点或集合。
取数预算 120000 行、64 MiB、30 秒。对账通过后各预热一次，严格交替各 30 次。服务端 EXPLAIN Execution Time、TIMING OFF，不含客户端传输；P50 为中位数，P95 用最近秩 ceil(.95*n)。
另行采集 EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) 自然计划，不混入 30 次样本。不强制 join/scan/aggregate，不测吞吐，不报告 QPS 或生产 SLA。

## 真实千问证据

```json
{
  "source": "live_qwen_customer_metrics",
  "status": "completed",
  "stage": "complete",
  "requested_model": "qwen3.7-plus",
  "model_id": "qwen3.7-plus",
  "model_requests": 1,
  "model_request_ms": 6488.526,
  "received_content": "{\n  \"action\": \"aggregate_once\",\n  \"reason\": \"The original query executes three separate index-only scans per user row, resulting in redundant I/O and high total cost due to the large number of users. The covering index orders_customer_created_cover_idx supports a single scan that can compute COUNT, conditional SUM, and MAX simultaneously via aggregation, significantly reducing execution time while preserving NULL/0 semantics through LEFT JOIN.\"\n}",
  "suggestion": {
    "action": "aggregate_once",
    "reason": "The original query executes three separate index-only scans per user row, resulting in redundant I/O and high total cost due to the large number of users. The covering index orders_customer_created_cover_idx supports a single scan that can compute COUNT, conditional SUM, and MAX simultaneously via aggregation, significantly reducing execution time while preserving NULL/0 semantics through LEFT JOIN."
  },
  "reason": null
}
```
模型仅接收原 SQL、年度/客户范围、相关字段/索引和估算计划，没有发送凭据或客户结果行。允许动作只有 aggregate_once / no_rewrite；复用严格 JSON 字段/重复键/理由校验，再映射到仓库模板。
只发一次真实模型请求，无连通性测试、重试或修复请求。模拟及错误候选的结果单独保存，不冒充真实千问效果。

## 实测总表

| 来源 | 状态 | 对账原/候选客户 | P50 原/候选 ms | P95 原/候选 ms | 判定 | 推荐 | 模型请求 |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| 真实千问全年 | completed | 100004/100004 | 352.1285/121.9925 | 363.347/130.112 | improved | use_candidate | 1 |
| 模拟建议全年 | completed | 100004/100004 | 352.9085/121.80850000000001 | 385.735/139.268 | improved | use_candidate | 0 |
| 固定单客户 | completed | 1/1 | 0.019/0.011 | 0.028/0.021 | inconclusive | keep_original | 0 |
| 固定错误 INNER JOIN | result_mismatch | 100004/100003 | —/— | —/— | 未计时 | keep_original | 0 |
| 模拟 no_rewrite | no_rewrite | —/— | —/— | —/— | 未计时 | keep_original | 0 |
| 模拟非法动作 | invalid_suggestion | —/— | —/— | —/— | 未计时 | keep_original | 0 |
窄范围仅改变客户绑定范围为 [1,1]，日期、数据、索引与会话配置相同。即使候选中位数更低，样本区间重叠仍 keep_original。
错误 INNER JOIN 丢失无订单客户 100002：100004 对 100003，在完整结果比较阶段拒绝；没有预热、样本或候选实际计划采集。

## 原查询与受控候选
```sql
SELECT u.customer_id,
 (SELECT COUNT(*) FROM public.orders o WHERE o.customer_id=u.customer_id
   AND o.created_at >= %s AND o.created_at < %s) AS order_count,
 (SELECT SUM(o.amount) FROM public.orders o WHERE o.customer_id=u.customer_id
   AND o.created_at >= %s AND o.created_at < %s AND o.status='paid') AS revenue,
 (SELECT MAX(o.created_at) FROM public.orders o WHERE o.customer_id=u.customer_id
   AND o.created_at >= %s AND o.created_at < %s) AS last_order_at
FROM public.users u WHERE u.customer_id BETWEEN %s AND %s
```
```sql
WITH customer_totals AS (
 SELECT customer_id,COUNT(*) AS order_count,
   SUM(amount) FILTER (WHERE status='paid') AS revenue,MAX(created_at) AS last_order_at
 FROM public.orders WHERE created_at >= %s AND created_at < %s GROUP BY customer_id
)
SELECT u.customer_id,COALESCE(t.order_count,0)::bigint AS order_count,
 t.revenue,t.last_order_at
FROM public.users u LEFT JOIN customer_totals t ON t.customer_id=u.customer_id
WHERE u.customer_id BETWEEN %s AND %s
```
实际绑定参数（时间均使用半开年度范围）：
```json
{
  "original": [
    {
      "timestamp": "2025-01-01T00:00:00"
    },
    {
      "timestamp": "2026-01-01T00:00:00"
    },
    {
      "timestamp": "2025-01-01T00:00:00"
    },
    {
      "timestamp": "2026-01-01T00:00:00"
    },
    {
      "timestamp": "2025-01-01T00:00:00"
    },
    {
      "timestamp": "2026-01-01T00:00:00"
    },
    1,
    2147483647
  ],
  "candidate": [
    {
      "timestamp": "2025-01-01T00:00:00"
    },
    {
      "timestamp": "2026-01-01T00:00:00"
    },
    1,
    2147483647
  ]
}
```
原 SQL 三个相关子查询与诊断、结果比较、采样共用同一常量模板。候选 SQL 与理由文本无字符串拼接关系。
```diff
--- original.sql
+++ candidate.sql
@@ -1,8 +1,9 @@
-SELECT u.customer_id,
- (SELECT COUNT(*) FROM public.orders o WHERE o.customer_id=u.customer_id
-   AND o.created_at >= %s AND o.created_at < %s) AS order_count,
- (SELECT SUM(o.amount) FROM public.orders o WHERE o.customer_id=u.customer_id
-   AND o.created_at >= %s AND o.created_at < %s AND o.status='paid') AS revenue,
- (SELECT MAX(o.created_at) FROM public.orders o WHERE o.customer_id=u.customer_id
-   AND o.created_at >= %s AND o.created_at < %s) AS last_order_at
-FROM public.users u WHERE u.customer_id BETWEEN %s AND %s
+WITH customer_totals AS (
+ SELECT customer_id,COUNT(*) AS order_count,
+   SUM(amount) FILTER (WHERE status='paid') AS revenue,MAX(created_at) AS last_order_at
+ FROM public.orders WHERE created_at >= %s AND created_at < %s GROUP BY customer_id
+)
+SELECT u.customer_id,COALESCE(t.order_count,0)::bigint AS order_count,
+ t.revenue,t.last_order_at
+FROM public.users u LEFT JOIN customer_totals t ON t.customer_id=u.customer_id
+WHERE u.customer_id BETWEEN %s AND %s

```

## 完整对账、语义哨兵与原始样本

### 真实千问全年

```json
{
  "original_rows": 100004,
  "candidate_rows": 100004,
  "columns_equal": true,
  "rows_and_multiplicities_equal": true,
  "equivalent": true,
  "reason": "在本次测试数据上结果一致",
  "missing_from_candidate": [],
  "extra_in_candidate": [],
  "columns": [
    [
      "customer_id",
      23,
      4,
      null,
      null
    ],
    [
      "order_count",
      20,
      8,
      null,
      null
    ],
    [
      "revenue",
      1700,
      null,
      null,
      null
    ],
    [
      "last_order_at",
      1114,
      8,
      null,
      null
    ]
  ]
}
```
```json
{
  "original": {
    "passed": true,
    "rows": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30.00"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18.00"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ],
    "expected": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ]
  },
  "candidate": {
    "passed": true,
    "rows": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30.00"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18.00"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ],
    "expected": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ]
  }
}
```
```json
{
  "original_30_samples_ms": [
    348.788,
    353.02,
    349.68,
    356.712,
    350.838,
    345.115,
    348.998,
    353.101,
    352.392,
    349.57,
    352.702,
    354.01,
    365.139,
    352.783,
    357.873,
    363.347,
    355.966,
    356.332,
    355.849,
    347.923,
    347.96,
    345.442,
    349.099,
    348.556,
    359.428,
    349.79,
    346.818,
    349.4,
    352.143,
    352.114
  ],
  "candidate_30_samples_ms": [
    122.747,
    121.007,
    126.375,
    130.112,
    120.59,
    120.93,
    120.611,
    122.727,
    121.148,
    121.364,
    125.148,
    122.451,
    119.785,
    122.685,
    119.835,
    131.13,
    127.057,
    123.541,
    122.611,
    125.582,
    128.117,
    119.313,
    120.778,
    120.892,
    123.635,
    120.639,
    121.215,
    123.62,
    121.503,
    121.534
  ],
  "P50_ms": {
    "original": 352.1285,
    "rewritten": 121.9925
  },
  "P95_ms": {
    "original": 363.347,
    "rewritten": 130.112
  },
  "P50_reduction_percent": 65.35568691543001,
  "performance_assessment": {
    "verdict": "improved",
    "rule": "strict_sample_interval_separation",
    "original_range_ms": {
      "min": 345.115,
      "max": 365.139
    },
    "candidate_range_ms": {
      "min": 119.313,
      "max": 131.13
    },
    "median_ms": {
      "original": 352.1285,
      "rewritten": 121.9925
    },
    "median_difference_ms": -230.136,
    "median_change_percent": -65.35568691543001,
    "sample_counts": {
      "original": 30,
      "rewritten": 30
    },
    "scope": "This sampling run only; not a general performance guarantee"
  },
  "recommendation": {
    "decision": "use_candidate",
    "reason_code": "validated_and_improved",
    "advisory_only": true
  }
}
```
自然计划中的订单访问与 SubPlan：
```json
{
  "original": [
    {
      "Node Type": "Aggregate",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 1",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "path": "0.0"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 10,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "Heap Fetches": 0,
      "path": "0.0.0"
    },
    {
      "Node Type": "Aggregate",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 2",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.002,
      "path": "0.1"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 8,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "Heap Fetches": 0,
      "path": "0.1.0"
    },
    {
      "Node Type": "Result",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 4",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "path": "0.2"
    },
    {
      "Node Type": "Limit",
      "Parent Relationship": "InitPlan",
      "Subplan Name": "InitPlan 3 (returns $3)",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "path": "0.2.0"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "Heap Fetches": 0,
      "path": "0.2.0.0"
    }
  ],
  "candidate": [
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 991148,
      "Actual Loops": 1,
      "Actual Total Time": 47.708,
      "Heap Fetches": 0,
      "path": "0.0.0"
    }
  ]
}
```
计划根节点 / 临时块：
```json
{
  "original": {
    "root": "Seq Scan",
    "temp_read_blocks": 0,
    "temp_written_blocks": 0,
    "spill": false,
    "shared_hit_blocks": 911618,
    "shared_read_blocks": 0
  },
  "candidate": {
    "root": "Hash Join",
    "temp_read_blocks": 0,
    "temp_written_blocks": 0,
    "spill": false,
    "shared_hit_blocks": 6708,
    "shared_read_blocks": 0
  }
}
```

### 模拟建议全年

```json
{
  "original_rows": 100004,
  "candidate_rows": 100004,
  "columns_equal": true,
  "rows_and_multiplicities_equal": true,
  "equivalent": true,
  "reason": "在本次测试数据上结果一致",
  "missing_from_candidate": [],
  "extra_in_candidate": [],
  "columns": [
    [
      "customer_id",
      23,
      4,
      null,
      null
    ],
    [
      "order_count",
      20,
      8,
      null,
      null
    ],
    [
      "revenue",
      1700,
      null,
      null,
      null
    ],
    [
      "last_order_at",
      1114,
      8,
      null,
      null
    ]
  ]
}
```
```json
{
  "original": {
    "passed": true,
    "rows": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30.00"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18.00"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ],
    "expected": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ]
  },
  "candidate": {
    "passed": true,
    "rows": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30.00"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18.00"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ],
    "expected": [
      {
        "row": [
          100001,
          1,
          {
            "decimal": "30"
          },
          {
            "timestamp": "2025-06-15T00:00:00"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100002,
          0,
          null,
          null
        ],
        "copies": 1
      },
      {
        "row": [
          100003,
          2,
          null,
          {
            "timestamp": "2025-06-15T23:59:59.999999"
          }
        ],
        "copies": 1
      },
      {
        "row": [
          100004,
          2,
          {
            "decimal": "18"
          },
          {
            "timestamp": "2025-12-31T23:59:59.999999"
          }
        ],
        "copies": 1
      }
    ]
  }
}
```
```json
{
  "original_30_samples_ms": [
    365.174,
    351.092,
    358.52,
    349.988,
    355.166,
    352.252,
    356.577,
    356.748,
    400.699,
    354.771,
    353.714,
    349.728,
    354.724,
    357.066,
    351.382,
    349.598,
    345.957,
    353.138,
    350.673,
    352.679,
    351.117,
    369.121,
    350.244,
    354.142,
    348.129,
    351.355,
    350.891,
    348.653,
    385.735,
    361.91
  ],
  "candidate_30_samples_ms": [
    139.268,
    120.4,
    125.978,
    120.045,
    119.493,
    119.947,
    121.318,
    129.728,
    123.32,
    121.481,
    125.32,
    129.147,
    126.955,
    121.686,
    121.961,
    119.395,
    117.788,
    123.615,
    121.681,
    121.613,
    120.775,
    126.145,
    124.75,
    120.895,
    121.931,
    119.468,
    119.793,
    124.639,
    172.233,
    122.315
  ],
  "P50_ms": {
    "original": 352.9085,
    "rewritten": 121.80850000000001
  },
  "P95_ms": {
    "original": 385.735,
    "rewritten": 139.268
  },
  "P50_reduction_percent": 65.48439609700532,
  "performance_assessment": {
    "verdict": "improved",
    "rule": "strict_sample_interval_separation",
    "original_range_ms": {
      "min": 345.957,
      "max": 400.699
    },
    "candidate_range_ms": {
      "min": 117.788,
      "max": 172.233
    },
    "median_ms": {
      "original": 352.9085,
      "rewritten": 121.80850000000001
    },
    "median_difference_ms": -231.1,
    "median_change_percent": -65.48439609700532,
    "sample_counts": {
      "original": 30,
      "rewritten": 30
    },
    "scope": "This sampling run only; not a general performance guarantee"
  },
  "recommendation": {
    "decision": "use_candidate",
    "reason_code": "validated_and_improved",
    "advisory_only": true
  }
}
```
自然计划中的订单访问与 SubPlan：
```json
{
  "original": [
    {
      "Node Type": "Aggregate",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 1",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "path": "0.0"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 10,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "Heap Fetches": 0,
      "path": "0.0.0"
    },
    {
      "Node Type": "Aggregate",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 2",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.002,
      "path": "0.1"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 8,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "Heap Fetches": 0,
      "path": "0.1.0"
    },
    {
      "Node Type": "Result",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 4",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "path": "0.2"
    },
    {
      "Node Type": "Limit",
      "Parent Relationship": "InitPlan",
      "Subplan Name": "InitPlan 3 (returns $3)",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "path": "0.2.0"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 1,
      "Actual Loops": 100004,
      "Actual Total Time": 0.001,
      "Heap Fetches": 0,
      "path": "0.2.0.0"
    }
  ],
  "candidate": [
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 991148,
      "Actual Loops": 1,
      "Actual Total Time": 49.378,
      "Heap Fetches": 0,
      "path": "0.0.0"
    }
  ]
}
```
计划根节点 / 临时块：
```json
{
  "original": {
    "root": "Seq Scan",
    "temp_read_blocks": 0,
    "temp_written_blocks": 0,
    "spill": false,
    "shared_hit_blocks": 911618,
    "shared_read_blocks": 0
  },
  "candidate": {
    "root": "Hash Join",
    "temp_read_blocks": 0,
    "temp_written_blocks": 0,
    "spill": false,
    "shared_hit_blocks": 6708,
    "shared_read_blocks": 0
  }
}
```

### 固定单客户

```json
{
  "original_rows": 1,
  "candidate_rows": 1,
  "columns_equal": true,
  "rows_and_multiplicities_equal": true,
  "equivalent": true,
  "reason": "在本次测试数据上结果一致",
  "missing_from_candidate": [],
  "extra_in_candidate": [],
  "columns": [
    [
      "customer_id",
      23,
      4,
      null,
      null
    ],
    [
      "order_count",
      20,
      8,
      null,
      null
    ],
    [
      "revenue",
      1700,
      null,
      null,
      null
    ],
    [
      "last_order_at",
      1114,
      8,
      null,
      null
    ]
  ]
}
```
```json
{
  "original_30_samples_ms": [
    0.022,
    0.019,
    0.018,
    0.018,
    0.028,
    0.019,
    0.017,
    0.025,
    0.02,
    0.018,
    0.026,
    0.019,
    0.025,
    0.038,
    0.019,
    0.019,
    0.022,
    0.019,
    0.018,
    0.018,
    0.021,
    0.019,
    0.018,
    0.018,
    0.018,
    0.017,
    0.018,
    0.019,
    0.022,
    0.021
  ],
  "candidate_30_samples_ms": [
    0.013,
    0.021,
    0.015,
    0.011,
    0.011,
    0.013,
    0.011,
    0.012,
    0.011,
    0.01,
    0.016,
    0.017,
    0.01,
    0.025,
    0.011,
    0.01,
    0.011,
    0.01,
    0.011,
    0.02,
    0.013,
    0.011,
    0.01,
    0.011,
    0.01,
    0.011,
    0.011,
    0.011,
    0.012,
    0.011
  ],
  "P50_ms": {
    "original": 0.019,
    "rewritten": 0.011
  },
  "P95_ms": {
    "original": 0.028,
    "rewritten": 0.021
  },
  "P50_reduction_percent": 42.10526315789473,
  "performance_assessment": {
    "verdict": "inconclusive",
    "rule": "strict_sample_interval_separation",
    "original_range_ms": {
      "min": 0.017,
      "max": 0.038
    },
    "candidate_range_ms": {
      "min": 0.01,
      "max": 0.025
    },
    "median_ms": {
      "original": 0.019,
      "rewritten": 0.011
    },
    "median_difference_ms": -0.008,
    "median_change_percent": -42.10526315789473,
    "sample_counts": {
      "original": 30,
      "rewritten": 30
    },
    "scope": "This sampling run only; not a general performance guarantee"
  },
  "recommendation": {
    "decision": "keep_original",
    "reason_code": "performance_inconclusive",
    "advisory_only": true
  }
}
```
自然计划中的订单访问与 SubPlan：
```json
{
  "original": [
    {
      "Node Type": "Aggregate",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 1",
      "Actual Rows": 1,
      "Actual Loops": 1,
      "Actual Total Time": 0.003,
      "path": "0.0"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 10,
      "Actual Loops": 1,
      "Actual Total Time": 0.002,
      "Heap Fetches": 0,
      "path": "0.0.0"
    },
    {
      "Node Type": "Aggregate",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 2",
      "Actual Rows": 1,
      "Actual Loops": 1,
      "Actual Total Time": 0.01,
      "path": "0.1"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 8,
      "Actual Loops": 1,
      "Actual Total Time": 0.009,
      "Heap Fetches": 0,
      "path": "0.1.0"
    },
    {
      "Node Type": "Result",
      "Parent Relationship": "SubPlan",
      "Subplan Name": "SubPlan 4",
      "Actual Rows": 1,
      "Actual Loops": 1,
      "Actual Total Time": 0.004,
      "path": "0.2"
    },
    {
      "Node Type": "Limit",
      "Parent Relationship": "InitPlan",
      "Subplan Name": "InitPlan 3 (returns $3)",
      "Actual Rows": 1,
      "Actual Loops": 1,
      "Actual Total Time": 0.003,
      "path": "0.2.0"
    },
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 1,
      "Actual Loops": 1,
      "Actual Total Time": 0.003,
      "Heap Fetches": 0,
      "path": "0.2.0.0"
    }
  ],
  "candidate": [
    {
      "Node Type": "Index Only Scan",
      "Relation Name": "orders",
      "Index Name": "orders_customer_created_cover_idx",
      "Parent Relationship": "Outer",
      "Actual Rows": 11,
      "Actual Loops": 1,
      "Actual Total Time": 0.004,
      "Heap Fetches": 0,
      "path": "0.1.0"
    }
  ]
}
```
计划根节点 / 临时块：
```json
{
  "original": {
    "root": "Index Only Scan",
    "temp_read_blocks": 0,
    "temp_written_blocks": 0,
    "spill": false,
    "shared_hit_blocks": 15,
    "shared_read_blocks": 0
  },
  "candidate": {
    "root": "Merge Join",
    "temp_read_blocks": 0,
    "temp_written_blocks": 0,
    "spill": false,
    "shared_hit_blocks": 7,
    "shared_read_blocks": 0
  }
}
```

### 固定错误 INNER JOIN

```json
{
  "original_rows": 100004,
  "candidate_rows": 100003,
  "columns_equal": true,
  "rows_and_multiplicities_equal": false,
  "equivalent": false,
  "reason": "Column metadata or full row multiplicities differ; performance test skipped",
  "missing_from_candidate": [
    {
      "row": [
        100002,
        0,
        null,
        null
      ],
      "copies": 1
    }
  ],
  "extra_in_candidate": [],
  "columns": [
    [
      "customer_id",
      23,
      4,
      null,
      null
    ],
    [
      "order_count",
      20,
      8,
      null,
      null
    ],
    [
      "revenue",
      1700,
      null,
      null,
      null
    ],
    [
      "last_order_at",
      1114,
      8,
      null,
      null
    ]
  ]
}
```
未计时；benchmark=null。
所用主案例来源 live_qwen_customer_metrics：相关子计划 [('SubPlan 1', 100004), ('SubPlan 2', 100004), ('SubPlan 4', 100004)]；原查询订单节点 loops [100004, 100004, 100004]，合计 300012；候选订单节点 loops [1]。MAX 的 InitPlan 是同一个相关指标的内部实现，不另算第四个相关指标。
SQL 的 LEFT JOIN 在自然计划中被优化为 Hash Right Join（调换物理输入），仍保留全部客户。loops 表示执行器调用次数，不等于磁盘 I/O；完整 BUFFERS 与父子路径见 JSON。

## 复现命令

```bash
docker compose -p sql-agent-customer-metrics --env-file .env.customer-metrics -f compose.benchmarks.yaml up -d --wait
.venv/bin/python scripts/seed_customer_metrics.py --env-file .env.customer-metrics --initialize
.venv/bin/python scripts/optimize_orders.py --scenario customer_metrics_2025 --config-file .env.customer-metrics --fixture customer-aggregate > reports/customer_metrics_controlled_main.json
.venv/bin/python scripts/optimize_orders.py --scenario customer_metrics_2025 --config-file .env.customer-metrics --fixture customer-aggregate --customer-scope narrow > reports/customer_metrics_controlled_narrow.json
.venv/bin/python scripts/check_customer_metrics_negative.py --config-file .env.customer-metrics
# 显式运行一次真实请求；复现时会使用你自己的额度，不自动重试
.venv/bin/python scripts/optimize_orders.py --scenario customer_metrics_2025 --config-file .env.customer-metrics --qwen > reports/customer_metrics_qwen_live.json
CUSTOMER_METRICS_TEST_ENV=.env.customer-metrics ADVANCED_BENCHMARK_ENV=.env.customer-metrics .venv/bin/python -m pytest -q
.venv/bin/python -m pip check
git diff --check
```
配置文件只在本地且被 Git 忽略。脚本仅接受专用带隔离标记的库；默认 HTTP/MCP 场景未扩展。

## 回归与可引用成果

全量 pytest：352 passed，11 subtests passed，含真实 PostgreSQL 语义测试；1 条已有 Starlette/httpx 弃用警告。pip check、git diff --check 通过。离线 CI 不需要密钥或数据库，默认跳过 8 个显式本地集成用例。
「在 100 万订单的客户经营报表测试中，千问选择将三个相关子查询改为一次按客户聚合，平台验证 100004 位客户的三个指标全部一致，查询耗时中位数由 352.1285 ms 降至 121.9925 ms，降低 65.36%。」
该成果只针对上述合成数据与本次采样，不保证任意客户范围或生产负载都会获益；窄范围无明确收益的结果保留。
