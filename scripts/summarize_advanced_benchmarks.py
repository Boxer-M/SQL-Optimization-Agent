"""Render a sanitized advanced benchmark JSON report offline; no DB/model calls."""
import argparse
import json
from pathlib import Path


def render(report):
    out=[]
    def add(line=''):out.append(line)
    add('# 复杂 JOIN 与大规模聚合实测报告')
    add();add(f"基线 `{report['baseline_sha']}`；确定性候选，真实千问请求 **0**。")
    add('结果只描述本次合成数据、自然计划和暖缓存采样，验收不以加速为条件。未做吞吐测试，不报告 QPS。')
    if report.get('execution_notes'):add(report['execution_notes'])
    add();add('## 环境、口径与复现')
    add();add('```json');add(json.dumps({'host':report['host'],'postgresql':report['postgresql'],'settings':report['database_settings'],'budgets':report['budgets'],'docker_runtime':report.get('docker_runtime','not recorded')},ensure_ascii=False,indent=2));add('```')
    add('每对查询在一个只读 REPEATABLE READ 快照比较列名/类型及完整多重集，原生保留 NULL、Decimal 和重复次数。')
    add('对账通过后复用现有 measure_pair：各预热一次，原/候选交替各 7 次。样本为 TIMING OFF 的服务端 EXPLAIN 执行时间，不含客户端传输。')
    add('P50 是样本中位数；P95 是最近秩法，n=7 时等于最大样本，不代表稳定尾延迟。自然计划不禁用扫描、连接、聚合或并行策略。')
    add('另行采集 EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)，timing-on 计划不计入 7 个样本；完整计划树、实际行数、loops、临时块均保存在同名 JSON。')
    add();add('完整复现命令 R1（先按 README 启动独立实例，仅操作带标记的测试库）：')
    add('```bash');add('.venv/bin/python scripts/benchmark_advanced.py --env-file .env.advanced --initialize \\');add('  --sizes 300000 1000000 --seed 20261125 --report reports/advanced_sql_benchmarks.json');add('.venv/bin/python scripts/summarize_advanced_benchmarks.py \\');add('  reports/advanced_sql_benchmarks.json --output reports/advanced_sql_benchmarks.md');add('ADVANCED_BENCHMARK_ENV=.env.advanced .venv/bin/python -m pytest -q');add('git diff --check');add('```')
    add('`--initialize` 拒绝非空且未标记的库；不要指向日常优化库。脚本不自动读取项目普通 .env。')
    add();add('## 数据分布与可重复装载')
    add();add('| 基础订单 | 分布 | 总订单 | 明细 | 用户 | 商品 | 退款 | 商家1占比 | 最大商家占比 | 客户组 | 商家组 | 地区×品类组 |')
    add('| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |')
    for d in report['datasets']:
        n=d['row_counts'];add(f"| {d['requested_base_orders']} | {d['distribution']} | {n['orders']} | {n['order_items']} | {n['users']} | {n['products']} | {n['refunds']} | {d['merchant_1_share']:.4%} | {d['largest_merchant_share']:.4%} | {d['customer_group_cardinality']} | {d['merchant_group_cardinality']} | {d['region_category_group_cardinality']} |")
    add();add('每档多 1 笔手工订单、2 条手工明细和 3 条退款；C 按 order_id < 900000000 排除手工订单，正好测 300000 / 1000000 基础订单。')
    add('均匀分布覆盖 100 商家；热点分布也是 100 商家，其中商家 1 占 80%，其余分散到 99 商家。客户分组基数固定 100000。')
    add(f"重复装载 300000 均匀数据，完整统计与订单内容指纹一致：**{report['repeat_seed']['identical']}**。所有五表行数不增长。")
    add('固定种子 20261125 的整数同余规则、日期范围、金额总和、订单指纹及 NULL 统计均在 JSON datasets 字段。')
    for d in report['datasets']:
        add(f"- {d['requested_base_orders']}/{d['distribution']}：NULL 日期 {d['null_dates']}，NULL 金额 {d['null_order_amounts']}；NULL 维度 {d['null_dimensions']}；指纹 `{d['order_fingerprint']}`。")
    add();add('基础索引（各档一致；额外索引只在独立 index 实验存在）：')
    add('```sql')
    for index in report['datasets'][0]['indexes']:add(index['definition']+';')
    add('```')
    add();add('## 全场景对账与性能总表')
    add();add('| case_id | 数据引用 | 原/候选结果行 | 对账 | P50 原/候选 ms | P95 原/候选 ms | 判定 | 推荐 | 溢出 原/候选 |')
    add('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for c in report['cases']:
        d=report['datasets'][c['dataset_index']];b=c.get('benchmark');p=c.get('performance_assessment') or {};v=c['comparison'];plans=c.get('plans',{})
        med=b['median_ms'] if b else {};p95=b['p95_ms'] if b else {}
        add(f"| {c['case_id']} | {d['requested_base_orders']}/{d['distribution']} | {v['original_rows']}/{v['candidate_rows']} | {v['equivalent']} | {med.get('original','—')}/{med.get('rewritten','—')} | {p95.get('original','—')}/{p95.get('rewritten','—')} | {p.get('verdict','跳过')} | {c['recommendation']['decision']} | {plans.get('original',{}).get('spill_observed','—')}/{plans.get('candidate',{}).get('spill_observed','—')} |")
    add();add('B 的成交口径只含范围内 paid 订单，销售额取明细数量×商品价格，退款独立求和；无交易用户返回 0/0。')
    add('A 汇总范围内全部订单，金额同样来自明细；按地区、品类逐组验证 amount、line_count、order_count。')
    add('C 基线为先按商家+客户聚合、再按目标键汇总；候选消除多余一级聚合。两者均保留 NULL SUM 语义。')
    add();add('## 单变量对照与收益边界')
    cases={c['case_id']:c for c in report['cases']}
    contrasts=[]
    sizes=sorted({d['requested_base_orders'] for d in report['datasets']})
    largest=sizes[-1]
    for size in sizes:
        for key in ('merchant_id','customer_id'):
            contrasts.append(('分布',f'C_{size}_uniform_{key}',f'C_{size}_hot_{key}'))
        for dist in ('uniform','hot'):
            contrasts.append(('分组键',f'C_{size}_{dist}_merchant_id',f'C_{size}_{dist}_customer_id'))
    for dist in ('uniform','hot'):
        for key in ('merchant_id','customer_id'):
            contrasts.append(('规模',f'C_{sizes[0]}_{dist}_{key}',f'C_{largest}_{dist}_{key}'))
    contrasts += [('索引',f'C_{largest}_hot_merchant_id','C_index_only'),('work_mem',f'C_{largest}_hot_customer_id','C_work_mem_64kB'),('work_mem',f'C_{largest}_hot_customer_id','C_work_mem_64MB'),('日期范围','A_narrow','A_wide')]
    add();add('| 仅改变 | 参照 → 对照 | 原 SQL P50 ms | 候选 SQL P50 ms |')
    add('| --- | --- | --- | --- |')
    for variable,left,right in contrasts:
        if left not in cases or right not in cases:continue
        l=cases[left]['benchmark']['median_ms'];r=cases[right]['benchmark']['median_ms']
        add(f"| {variable} | {left} → {right} | {l['original']} → {r['original']} | {l['rewritten']} → {r['rewritten']} |")
    add('索引实验只增加 orders(merchant_id) B-tree，不重新采样表统计；work_mem 实验保持额外索引不存在。其他设置记录于环境段。')
    add('不同设置之间为依次执行的独立采样，并非交错随机化的因果实验；缓存、并行调度与系统噪声仍可能影响结果。只描述观测差异，不据多个同时变化的因素归因。')
    if 'A_narrow' in cases and 'A_wide' in cases:
        add(f"D 直接复用 A_narrow/A_wide：同一 {sizes[0]} 均匀数据、索引及 4MB work_mem，单日判定 {cases['A_narrow']['performance_assessment']['verdict']}，全年判定 {cases['A_wide']['performance_assessment']['verdict']}。没有为追求加速筛掉不利结果。")
    for cid in (f'C_{largest}_hot_customer_id','C_work_mem_64kB','C_work_mem_64MB'):
        if cid in cases:
            case=cases[cid]
            add(f"{cid}：判定 {case['performance_assessment']['verdict']}，推荐 {case['recommendation']['decision']}，溢出原/候选 {case['plans']['original']['spill_observed']}/{case['plans']['candidate']['spill_observed']}。保留未溢出结果，没有强制 HashAggregate 来制造溢出。")
    add();add('## 逐场景 SQL、计划与原始样本')
    for c in report['cases']:
        add();add('### '+c['case_id']);add();d=report['datasets'][c['dataset_index']]
        add(f"复现 R1 后读取 JSON cases 中 `{c['case_id']}`。基础订单 {d['requested_base_orders']}，{d['distribution']}，work_mem {c['work_mem']}。")
        add('原/候选绑定参数：`'+json.dumps(c['params'],ensure_ascii=False)+'`。')
        for side in ('original','candidate'):
            add();add(side+' SQL：');add('```sql');add(c['sql'][side]);add('```')
            add('SQL 文本表出现顺序：`'+json.dumps(c['sql_text_table_order'][side],ensure_ascii=False)+'`。')
        comp=c['comparison'];add('完整对账：`'+json.dumps({k:comp[k] for k in ('original_rows','candidate_rows','columns_equal','rows_and_multiplicities_equal','equivalent','missing_from_candidate','extra_in_candidate')},ensure_ascii=False)+'`。')
        if not c.get('benchmark'):
            add('**结果不一致，在完整结果比较阶段拒绝；benchmark=null，无预热/计时/实际计划采集。**')
            continue
        b=c['benchmark'];add('原查询 7 个原始样本 ms：`'+str(b['execution_ms']['original'])+'`。')
        add('候选 7 个原始样本 ms：`'+str(b['execution_ms']['rewritten'])+'`。')
        add('P50：`'+str(b['median_ms'])+'`；P95：`'+str(b['p95_ms'])+'`。')
        add('性能：`'+str(c['performance_assessment'])+'`；推荐：`'+str(c['recommendation'])+'`。')
        add('样本波动：'+b['variability_note']+'。')
        for side,p in c['plans'].items():
            add();add(f"{side} 自然实际计划摘要：")
            summary={'scans':p['scan_leaf_order'],
                     'joins':[{k:n[k] for k in ('path','Node Type','Join Type','Actual Rows','Actual Loops') if k in n} for n in p['join_nodes']],
                     'aggregates':[{k:n[k] for k in ('path','Node Type','Strategy','Group Key','Actual Rows','Actual Loops','HashAgg Batches','Disk Usage') if k in n} for n in p['aggregate_nodes']],
                     'sorts':[{k:n[k] for k in ('path','Node Type','Sort Method','Sort Space Type','Actual Rows','Actual Loops') if k in n} for n in p['sort_nodes']],
                     'spill_observed':p['spill_observed'],'temp_read_blocks':p['root_temp_read_blocks'],'temp_written_blocks':p['root_temp_written_blocks']}
            add('```json');add(json.dumps(summary,ensure_ascii=False,indent=2));add('```')
        add('路径表示 outer/inner 连接拓扑，不是墙钟事件顺序；哈希构建和并行执行不可仅由叶子遍历推断先后。')
    add();add('## 手工语义样本与固定计时控制')
    if report.get('A_semantic_fixture'):
        add();add('A 的补充只读 VALUES 夹具：同订单、同 NULL 品类的三条明细合计 40，line_count=3、order_count=1；另一个全 NULL 价格组 SUM 保持 NULL。两条模板完整对账通过，不作为性能样本。')
        add('```json');add(json.dumps(report['A_semantic_fixture']['expected'],ensure_ascii=False,indent=2));add('```')
    add();add('```json');add(json.dumps(report['manual_fixture'],ensure_ascii=False,indent=2));add('```')
    add('两条正确 SQL 都返回用户 100001 销售 30、退款 10；用户 100002 无交易且 region=NULL，返回 0/0。错误 2×3 JOIN 返回 90/20。')
    for row in report['fixed_controls']:
        add();add(f"- **固定样本，非 PostgreSQL 实测** `{row['case_id']}`：原 {row['benchmark']['execution_ms']['original']}，候选 {row['benchmark']['execution_ms']['rewritten']}；判定 `{row['performance_assessment']['verdict']}`，推荐 `{row['recommendation']['decision']}`。")
    add('重叠控制的候选中位数更低仍 keep_original；明确退化控制也 keep_original。')
    add();add('## 回归与限制')
    add();add(report.get('verification',{}).get('summary','本报告尚未记录 pytest/依赖/差异检查结果；请按复现命令执行。'))
    add('默认离线 pytest 跳过 6 个数据库用例；仅显式 ADVANCED_BENCHMARK_ENV 配置、只读账号且数据库标记一致时开放这些集成连接。其余测试与全部 HTTP 仍受原离线保护。')
    add('报告不含环境文件、凭据或真实业务数据。合成数据的同余规则存在相关性，不作为生产分布或普遍优化收益证据；每次运行应以新的自然计划、区间和推荐为准。')
    add('本轮未创建 PR、未合并、未发布 v0.2.2。')
    return '\n'.join(out)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report');parser.add_argument('--output',required=True);args=parser.parse_args()
    report=json.loads(Path(args.report).read_text())
    if report.get('status')!='completed':parser.error('A completed advanced benchmark report is required')
    Path(args.output).write_text(render(report))


if __name__=='__main__':main()
