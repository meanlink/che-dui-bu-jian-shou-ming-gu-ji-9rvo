# 分期放行验收 · 检查记录

环境：本机 Linux、Python 3.11（SQLite 隔离库），未启动 Docker Compose。以下只记录实际执行过的检查。

## 已执行

| 检查 | 命令 | 结果 |
|---|---|---|
| 业务规则单测（判定数、额度上限、收齐、补报、同号异值、跨期等待、封案、重试） | `python3 -m pytest -q test-artifacts/gb-1017/tests` | 18 passed |
| 旧库迁移（reports.job_id 由 NOT NULL 放开，保留历史分发） | 手工构造旧 SQLite schema 后连续执行 `migrations.initial.migrate` | 历史行与 deliveries 保留；重复执行幂等 |
| 演示 seed 幂等 | 连续两次 `scripts.seed_demo.seed()` | 4 案 / 8 期，第二次计数不变 |
| 并发同请求号 5 线程 | 临时脚本 | 5 次均返回同一 `passed` 结论，样本 30、报期记录 1 |
| 并发不同请求号 6 线程 | 临时脚本 | 1 次生效，其余 5 次返回 409，样本 30、报期记录 1、额度只消耗一次 |
| worker 分发达标验收案 | 建立计划后 `src.worker.tick()` | 生成 CSV/JSON 归档，CSV 含逐期依据 |
| 页面渲染（DOM 桩） | 临时 Node 脚本渲染列表/详情/分发/总览 | 4 案四种状态均渲染，达标+批准案出现分发入口 |

## 未执行

- 未构建或启动 Docker Compose 五服务（本机无 Docker）。
- 未在 PostgreSQL 上执行；`FOR UPDATE` 行锁路径与 `ALTER COLUMN` 迁移未实测。
- 未做真实浏览器截图与端到端点击。

源码不含日志与截图；结论以本文件为准。
