# 工作台前端改版验收

## 基线与范围

- 基线：`0bba14a12762391ef31d8b9c9f31e5d9efaf96fa`，从最新 origin/main 创建 `feat/workbench-ui-refresh`。
- 仅修改现有 HTML/CSS/JavaScript 的布局与交互反馈，未修改 API、权限、模型客户端或 worker 实现。
- 全新隔离项目 `sql-agent-ui-refresh`，优化库 127.0.0.1:64510，审核库 127.0.0.1:64511，API 64512。
- 复用本地已验收 0.2.0 镜像初始化 PostgreSQL 16 数据，主机 Python 3.12 运行当前分支 API 和独立 worker。
- 所有候选使用模拟模型响应；真实模型请求为 0，原数据库和工作区保留。

## 实际浏览器验收

使用真实 in-app Chromium 页面与 FastAPI，未用 API 替代浏览器提交和审核操作。

1. 修改前打开工作台、登录并保存 `workbench_before.jpg`。
2. 新页面空任务/空报告、登录状态、加载提示与禁用操作均可见。
3. 浏览器文件选择器提交三种支持形态与 `unsupported_select.sql`。
   - 任务 `15bb3ae7dfa64e8c9dc27f5fbeafaa04`，归档批次 `51f4f39142526f8fd5d51a242038af9d20e38c41bbe7c3331ccf18b0c055fb4e`。
   - 完成 3、拒绝 1、失败 0；刷新后任务和报告仍可访问。
   - 单日 825/825，明细计数 551/551、双向 ID 差集 0/0，多日 1648/1648。
   - 本轮三个候选采样均为 improved、推荐 use_candidate；自然计划、14 个样本及各中位数保存于 workbench_ui_batch.json。
   - 仅本次隔离数据上的性能结果，不作为普遍性能保证。
4. 从任务进入归档，摘要先显示推荐、校验、性能中位数；展开 SQL Diff 可见参数占位符。
5. 两个独立页面分别使用两位 reviewer 身份，均读取条目版本 0。
   - A 提交暂缓成功，B 提交收到 409。
   - B 页面读取版本 1 与历史，原理由保留，显示专门冲突提示，未自动重试。
   - B 再次确认后成功，版本 2，两条审核记录均保留。
6. 拒绝项的「采纳」禁用；viewer 不显示任务提交、报告导入、审核表单。
   - 补充实际 HTTP viewer POST /jobs 返回 403，服务端权限保持有效。
7. viewer 通过浏览器下载原始 JSON，字节与 API 原始报告完全一致。
   - SHA-256：`5d58309baa522778dceb89eb09295c1032e360fd71f5ff6f3c03299538f46353`。
   - 审核后原始报告和系统推荐均保持原样。
8. 桌面 1440px 检查双栏；390px 窄屏检查上下布局。
   - document clientWidth = scrollWidth = 390，没有整页横向溢出。
   - SQL pre clientWidth 318，scrollWidth 376，长 SQL 在自身区域滚动。
9. 短暂停止本轮隔离 API，浏览器刷新显示请求失败；理由保留。恢复 API 后可重试。
10. 在隔离审核库中明确植入一个 interrupted 状态夹具（非真实 worker 崩溃测试），
    浏览器点击「人工重提」生成新任务 `5516a70768934003b51fe265fa65392c`，随后刷新确认完成 3、拒绝 1、失败 0。

## 实际性能摘要（ms）

| 场景 | 原查询中位数 | 候选中位数 | 判定 | 推荐 |
| --- | ---: | ---: | --- | --- |
| 单日过滤 | 7.560 | 0.079 | improved | use_candidate |
| 明细计数 | 0.431 | 0.366 | improved | use_candidate |
| 多日区间 | 7.786 | 0.117 | improved | use_candidate |

## 截图与证据

- workbench_before.jpg：原工作台。
- workbench_after_tasks.jpg：桌面任务与数量。
- workbench_after_reports.jpg：桌面报告、SQL Diff 与两条审核历史。
- workbench_after_mobile.jpg：390px 实际窄屏报告。
- workbench_conflict.jpg：409 提示及保留理由。
- workbench_request_error.jpg：实际连接失败提示。
- workbench_ui_http_evidence.json：下载 hash、拒写与审核历史。
- workbench_ui_batch.json：实际归档报告与性能证据。

## v0.2.1 发布收尾核对

- 注释标签 v0.2.1 指向上述基线，项目版本为 0.2.1。
- Actions https://github.com/Boxer-M/SQL-Optimization-Agent/actions/runs/45132055814
  的 verify、amd64/arm64 smoke、publish 均为 success。
- GHCR manifest 含 linux/amd64、linux/arm64；digest 为
  `sha256:077b4eccea1bd4e6c50c594f0d6fbf14516f96158007c06859aa929fc666dfa1`。
- GitHub `/releases/tags/v0.2.1` 返回 404，目前未发现公开发布的该版本 Release。
- 按用户要求跳过 Mac 镜像拉取；Mac 按 digest 拉取并启动验收仍待补，不以 CI 冒烟代替。
- 未创建 GitHub Release，Release Notes 由使用方粘贴发布。

## 回归检查

```bash
PYTHONPATH=src /private/tmp/sql-agent-release-v021/.venv/bin/python -m pytest -q
node --check src/sql_optimization_agent/web/app.js
git diff --check
```

检查结果：317 passed，11 subtests passed；1 条已有 Starlette/httpx 弃用警告。
JS 语法与 git diff --check 通过。报告脱敏检查通过，无环境文件、密码或令牌提交。
