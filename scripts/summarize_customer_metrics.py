"""Offline rendering of live and fixed customer-report evidence; no service calls."""
import argparse,json
from pathlib import Path
import platform


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports-dir',default='reports');parser.add_argument('--baseline',required=True)
    parser.add_argument('--output',default='reports/customer_metrics_acceptance.md');args=parser.parse_args()
    root=Path(args.reports_dir)
    def read(name):return json.loads((root/f'customer_metrics_{name}.json').read_text())
    data=read('dataset');repeat=read('seed_repeat')
    cases={name:read(name) for name in ('qwen_live','controlled_main','controlled_narrow','wrong_inner','no_rewrite','invalid')}
    live=cases['qwen_live'];lines=[]
    def add(text=''):lines.append(text)
    def block(value,kind='json'):
        add('```'+kind);add(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2));add('```')
    add('# 百万订单客户经营报表优化验收')
    add();add(f"开发基线 `{args.baseline}`，分支 feat/customer-metrics-optimization。高级基准未合并时，先合并 test/advanced-sql-benchmarks，再合并本分支。v0.2.2 发布独立推进。")
    add();add('## 数据与语义')
    add();add(f"固定种子 {data['seed']}；实际订单 **{data['total_orders']}**，客户 **{data['total_customers']}**。")
    add(f"有订单客户 {data['customers_with_any_orders']}；NULL 金额订单 {data['null_amount_orders']}；NULL 时间订单 {data['null_timestamp_orders']}。")
    add('999992 基础订单加 8 条手工样本。全年范围为 [2025-01-01,2026-01-01)，timestamp without time zone。')
    add('订单数统计全部状态；成交额仅 SUM(paid orders.amount)，保留 NULL；最近下单时间是所有范围内订单 MAX(created_at)。无订单为 0/NULL/NULL，全 NULL 金额客户为 2/NULL/具体最后时间。')
    add('年度范围内每客户订单数分布（订单数,客户数）：');block(data['annual_orders_per_customer_histogram'])
    add(f"重复装载一致：{repeat['identical']}；订单指纹 `{data['order_fingerprint']}`，客户指纹 `{data['user_fingerprint']}`。重复造数未重跑模型。")
    add();add('## 环境与公平性')
    add();block({'host':{'system':platform.system(),'machine':platform.machine(),'python':platform.python_version()},'postgresql':data['postgresql'],'settings':data['settings']})
    add('独立 Compose 项目 sql-agent-customer-metrics，专用端口 64630 与独立数据卷；原优化库和原工作区未重置。')
    add('原查询与候选共用以下索引，并先 VACUUM ANALYZE。两侧实测订单索引访问 Heap Fetches=0，没有故意让原查询回表或缺索引。')
    block('\n'.join(i['definition']+';' for i in data['indexes']),'sql')
    add('执行时 work_mem=4MB，60 秒 statement_timeout；同一只读 REPEATABLE READ 快照完成全部客户的列元数据与多重集比较，NULL、Decimal 和重复次数不被转换成浮点或集合。')
    add('取数预算 120000 行、64 MiB、30 秒。对账通过后各预热一次，严格交替各 30 次。服务端 EXPLAIN Execution Time、TIMING OFF，不含客户端传输；P50 为中位数，P95 用最近秩 ceil(.95*n)。')
    add('另行采集 EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) 自然计划，不混入 30 次样本。不强制 join/scan/aggregate，不测吞吐，不报告 QPS 或生产 SLA。')
    add();add('## 真实千问证据')
    add();block({k:live.get(k) for k in ('source','status','stage','requested_model','model_id','model_requests','model_request_ms','received_content','suggestion','reason')})
    add('模型仅接收原 SQL、年度/客户范围、相关字段/索引和估算计划，没有发送凭据或客户结果行。允许动作只有 aggregate_once / no_rewrite；复用严格 JSON 字段/重复键/理由校验，再映射到仓库模板。')
    add('只发一次真实模型请求，无连通性测试、重试或修复请求。模拟及错误候选的结果单独保存，不冒充真实千问效果。')
    add();add('## 实测总表')
    add();add('| 来源 | 状态 | 对账原/候选客户 | P50 原/候选 ms | P95 原/候选 ms | 判定 | 推荐 | 模型请求 |')
    add('| --- | --- | --- | --- | --- | --- | --- | ---: |')
    labels={'qwen_live':'真实千问全年','controlled_main':'模拟建议全年','controlled_narrow':'固定单客户','wrong_inner':'固定错误 INNER JOIN','no_rewrite':'模拟 no_rewrite','invalid':'模拟非法动作'}
    for name,r in cases.items():
        c=r.get('comparison')or{};b=r.get('benchmark')or{};p=r.get('performance_assessment')or{}
        med=b.get('median_ms',{});tail=b.get('p95_ms',{})
        add(f"| {labels[name]} | {r['status']} | {c.get('original_rows','—')}/{c.get('candidate_rows','—')} | {med.get('original','—')}/{med.get('rewritten','—')} | {tail.get('original','—')}/{tail.get('rewritten','—')} | {p.get('verdict','未计时')} | {r['recommendation']['decision']} | {r.get('model_requests',0)} |")
    add('窄范围仅改变客户绑定范围为 [1,1]，日期、数据、索引与会话配置相同。即使候选中位数更低，样本区间重叠仍 keep_original。')
    add('错误 INNER JOIN 丢失无订单客户 100002：100004 对 100003，在完整结果比较阶段拒绝；没有预热、样本或候选实际计划采集。')
    add();add('## 原查询与受控候选')
    source=live if live.get('candidate') else cases['controlled_main']
    block(source['diagnostic_context']['original_sql'],'sql');block(source['candidate']['sql'],'sql')
    add('实际绑定参数（时间均使用半开年度范围）：');block(source['parameters'])
    add('原 SQL 三个相关子查询与诊断、结果比较、采样共用同一常量模板。候选 SQL 与理由文本无字符串拼接关系。')
    if source.get('sql_diff'):block(source['sql_diff'],'diff')
    add();add('## 完整对账、语义哨兵与原始样本')
    for name in ('qwen_live','controlled_main','controlled_narrow','wrong_inner'):
        r=cases[name];add();add('### '+labels[name]);add()
        if r.get('comparison'):block(r['comparison'])
        if r.get('sentinels'):block(r['sentinels'])
        if r.get('benchmark'):
            b=r['benchmark'];block({'original_30_samples_ms':b['execution_ms']['original'],'candidate_30_samples_ms':b['execution_ms']['rewritten'],'P50_ms':b['median_ms'],'P95_ms':b['p95_ms'],'P50_reduction_percent':b['p50_reduction_percent'],'performance_assessment':r['performance_assessment'],'recommendation':r['recommendation']})
            add('自然计划中的订单访问与 SubPlan：');block(r['order_access_and_subplans'])
            add('计划根节点 / 临时块：');block({side:{'root':p['raw']['Plan']['Node Type'],'temp_read_blocks':p['root_temp_read_blocks'],'temp_written_blocks':p['root_temp_written_blocks'],'spill':p['spill_observed'],'shared_hit_blocks':p['raw']['Plan'].get('Shared Hit Blocks'),'shared_read_blocks':p['raw']['Plan'].get('Shared Read Blocks')} for side,p in r['plans'].items()})
        else:add('未计时；benchmark=null。')
    accesses=source.get('order_access_and_subplans',{})
    original_nodes=accesses.get('original',[])
    candidate_nodes=accesses.get('candidate',[])
    subplans=[(n.get('Subplan Name'),n.get('Actual Loops')) for n in original_nodes if n.get('Parent Relationship')=='SubPlan']
    scans=[n.get('Actual Loops',0) for n in original_nodes if n.get('Relation Name')=='orders']
    candidate_scans=[n.get('Actual Loops',0) for n in candidate_nodes if n.get('Relation Name')=='orders']
    add(f"所用主案例来源 {source['source']}：相关子计划 {subplans}；原查询订单节点 loops {scans}，合计 {sum(scans)}；候选订单节点 loops {candidate_scans}。MAX 的 InitPlan 是同一个相关指标的内部实现，不另算第四个相关指标。")
    add('SQL 的 LEFT JOIN 在自然计划中被优化为 Hash Right Join（调换物理输入），仍保留全部客户。loops 表示执行器调用次数，不等于磁盘 I/O；完整 BUFFERS 与父子路径见 JSON。')
    add();add('## 复现命令')
    add();block("""docker compose -p sql-agent-customer-metrics --env-file .env.customer-metrics -f compose.benchmarks.yaml up -d --wait
.venv/bin/python scripts/seed_customer_metrics.py --env-file .env.customer-metrics --initialize
.venv/bin/python scripts/optimize_orders.py --scenario customer_metrics_2025 --config-file .env.customer-metrics --fixture customer-aggregate > reports/customer_metrics_controlled_main.json
.venv/bin/python scripts/optimize_orders.py --scenario customer_metrics_2025 --config-file .env.customer-metrics --fixture customer-aggregate --customer-scope narrow > reports/customer_metrics_controlled_narrow.json
.venv/bin/python scripts/check_customer_metrics_negative.py --config-file .env.customer-metrics
# 显式运行一次真实请求；复现时会使用你自己的额度，不自动重试
.venv/bin/python scripts/optimize_orders.py --scenario customer_metrics_2025 --config-file .env.customer-metrics --qwen > reports/customer_metrics_qwen_live.json
CUSTOMER_METRICS_TEST_ENV=.env.customer-metrics ADVANCED_BENCHMARK_ENV=.env.customer-metrics .venv/bin/python -m pytest -q
.venv/bin/python -m pip check
git diff --check""",'bash')
    add('配置文件只在本地且被 Git 忽略。脚本仅接受专用带隔离标记的库；默认 HTTP/MCP 场景未扩展。')
    add();add('## 回归与可引用成果')
    verification_path=root/'customer_metrics_verification.json'
    add();add(json.loads(verification_path.read_text())['summary'] if verification_path.exists() else '尚未记录回归检查结果，请运行上述 pytest / pip check / diff 检查。')
    if live.get('status')=='completed' and (live.get('suggestion')or{}).get('action')=='aggregate_once' and (live.get('performance_assessment')or{}).get('verdict')=='improved':
        b=live['benchmark'];med=b['median_ms'];count=live['comparison']['original_rows']
        add(f"「在 {data['total_orders']/10000:g} 万订单的客户经营报表测试中，千问选择将三个相关子查询改为一次按客户聚合，平台验证 {count} 位客户的三个指标全部一致，查询耗时中位数由 {med['original']:.4f} ms 降至 {med['rewritten']:.4f} ms，降低 {b['p50_reduction_percent']:.2f}%。」")
    else:add('本次真实模型未形成已验证且改善的候选，不以受控结果编写真模型成果描述。')
    add('该成果只针对上述合成数据与本次采样，不保证任意客户范围或生产负载都会获益；窄范围无明确收益的结果保留。')
    Path(args.output).write_text('\n'.join(lines)+'\n')


if __name__=='__main__':main()
