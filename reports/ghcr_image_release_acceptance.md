# GHCR 镜像流程本地验收

- 基线：`c9d4ee5aca384aea69b3faf4ed51b02d357da8c1`（v0.2.0）。
- 分支：`ci/ghcr-image-release`；复用已验收 Dockerfile，无版本或标签变更。
- 实际平台：Docker Desktop Linux/arm64；Python 测试使用 Python 3.12。
- 镜像：`sql-optimization-agent:ghcr-ci-acceptance`，从本次独立检出目录构建。
- 隔离随机 Compose 项目、独立数据库卷、随机本地凭据；完成后仅清理本次项目。
- 原有工作区、数据库、v0.2.0 标签与 Release 保留。

## 实际执行

```bash
docker build -t sql-optimization-agent:ghcr-ci-acceptance .
python scripts/smoke_compose.py --image sql-optimization-agent:ghcr-ci-acceptance --report reports/ghcr_compose_smoke.json
python -m pytest -q
python -m pip check
git diff --check
```

- 构建通过；Compose 配置校验通过。
- 一次性迁移退出 0，API `/workbench` 可访问。
- 模拟任务完成并自动归档：total=1、completed=1、rejected=0、failed=0。
- 日期过滤完整多重集比较：825/825，结果一致。
- 自然执行计划、原始计时、性能判定及系统推荐保存在 JSON 证据。
- migrate/API/worker 三个容器的镜像 ID 一致。
- 真实模型请求 0；本次报告不包含令牌、密码或环境文件。
- pytest：317 passed，11 subtests passed；1 条已有 Starlette/httpx 弃用警告。
- pip check：No broken requirements found；git diff --check 通过。
- 工作流 YAML 语法解析通过；标签/版本一致及不一致路径有自动测试。
- 复现时发现 Compose run 不支持 `--no-build`，已改用预构建镜像与 `--pull never`，重新执行完整冒烟通过。

## 尚未执行的外部验证

本次未推送任何 GHCR 镜像。PR 创建后检查两个原生 runner 的 amd64/arm64 构建及
Compose 冒烟结果。后续由使用方触发与项目版本一致的新版本标签，发布作业在全部
测试通过后才写入 GHCR；作业摘要与 image-release artifact 提供 manifest digest 和
来源 SHA。本地 arm64 验收不代表 Actions、amd64 或 GHCR 发布已经通过。
