---
title: v1 运行记录实施报告 — Q1–Q4 查询与批量写入
type: report
status: review
created: 2026-11-05
updated: 2026-11-05
scope: 资源目录数据库化方案阶段 4 / production_run / 运行查询
related:
  - docs/implementation/plans/resource-catalog-database-and-knowledge-graph.md
  - docs/implementation/reports/v1-catalog-persistence.md
---

# v1 运行记录实施报告：Q1–Q4 查询与批量写入

本报告记录
[资源目录数据库化与知识图谱实施方案](../plans/resource-catalog-database-and-knowledge-graph.md)
阶段 4 的实施结果。实施范围按用户确认的查询口径执行：

- Q1 运行汇总与验收；
- Q2 成品物料谱系；
- Q3 资源等待与竞争；
- Q4 工序时间线与失败取证；
- 提供 CLI JSON 查询和直接 SQL；
- 只记录状态转换/首次 WAIT，不逐 tick 写库；
- 失败/中止运行也记录；
- 全部保留，不做自动清理；
- 写库失败只在运行报告标记持久化失败，不回滚仿真、不自动重放。

## 1. 代码落点

| 模块 | 内容 |
|---|---|
| `persistence/run_models.py`（新增） | `production_run`、`operation_instance`、`material_instance`、`material_genealogy`、`resource_event`、`run_event`，以及工序输入/输出关系表 |
| `persistence/migrations/versions/0003_run_records.py`（新增） | 运行记录 schema、状态/范围 CHECK、复合外键和查询索引 |
| `persistence/runs.py`（新增） | `RunRecorder`（开始一行 + 结束一个批事务）与 Q1–Q4 只读查询 |
| `application/report.py`（新增） | CLI 查询用例封装 |
| `application/run.py`（修改） | 运行开始/结束接入 recorder；异常/中止路径也写失败终态 |
| `domain/resources.py`（修改） | `ResourceEvent` 增加 `holder`，用于等待原因查询；仍按首次 WAIT 去重 |
| `cli.py`（扩展） | `run --run-records on/off`；`report runs/run/genealogy/waits/timeline` |
| `tests/test_v1_run_records.py`（新增） | 写入、Q1–Q4、失败/中止、写库失败、CLI |

## 2. 写入边界与失败语义

```text
run start ──► INSERT production_run(status=RUNNING)
               └─ 仿真循环: 不写库
run end   ──► 一个事务:
               INSERT material_instance
               INSERT operation_instance (+input/output)
               INSERT material_genealogy
               INSERT resource_event / run_event
               UPDATE production_run(status=DONE|FAILED|ABORTED, result_json)
```

- `RunRecorder.start` 幂等升级 schema（含 `0003_run_records`）并写 RUNNING。
- `RunRecorder.finish` 在单一事务内写全部子表；重复调用返回第一次结果。
- 写失败：事务回滚，随后尽力把 RUNNING 行标为 `FAILED` 并写
  `failure_reason`；运行报告顶层 `run_record.ok=false`。仿真结果和
  `all_ok` 不被改写，也不自动重放。
- 进程被强杀时，RUNNING 行会保留，表示“运行未完成记录”；查询
  `report runs` 可发现。当前不自动推断或清理。
- `run --run-records off` 关闭写入；数据库不可用时运行继续并记录持久化失败。

## 3. 表与关系

- `production_run`：批次、场景、工件、目录版本/来源、状态、仿真/墙钟时间、
  `all_ok`、失败原因和紧凑 `result_json`。
- `operation_instance`：每个工序实例的模板、配方、路线、任务键、UID、
  选定设备、最终状态、attempts、created/started/finished/committed、
  waiting/blocked 时刻、失败原因和 evidence。
- `operation_instance_input` / `operation_instance_output`：显式关联到
  `material_instance`，不用 JSON 数组替代关系。
- `material_instance`：最终 UID/类型/状态/区域/来源/目的地/工序阶段；
  不保存 CoppeliaSim handle。
- `material_genealogy`：`parent_uid → child_uid` 边，关联产生该边的
  `operation_instance`；当前由 E 岛配对产生。
- `resource_event`：ACQUIRE/WAIT/RELEASE/RELEASE_DENIED/ORDER_VIOLATION，
  保存 sim_time、owner、task_id、holder、reason 和可解析的 instance_id。
- `run_event`：协调器事件 + 物料事件 + 合成的 RUN_START/RUN_END；
  保存 kind、UID、区域、设备、evidence_id、instance_id 和 detail JSON。

## 4. 查询命令

```powershell
flexible-factory report runs --db var/catalog/catalog.db
flexible-factory report run --db var/catalog/catalog.db --run-id <run_id>
flexible-factory report genealogy --db var/catalog/catalog.db --run-id <run_id> --uid <final_uid>
flexible-factory report waits --db var/catalog/catalog.db --run-id <run_id> [--resource arm_A]
flexible-factory report timeline --db var/catalog/catalog.db --run-id <run_id> [--instance-id <id>]
```

不传 `--run-id` 时使用最近一次运行。输出为 JSON，可直接用 SQLite 进一步
查询；数据库仍是本地权威记录，不依赖服务器。

Q1 的 `counts` 给出工序状态、物料类型/状态、资源动作和事件类型计数；
Q2 返回递归父树、节点、边和 UID 相关事件；Q3 返回每个资源的等待/持有
统计、等待明细和争用排名；Q4 返回工序时间线、输入/输出 UID、资源等待、
事件和失败列表。

## 5. 验证记录

| 项目 | 结果 |
|---|---|
| 迁移 `0002` → `0003` | 通过 |
| 空库 → head → base → head | 通过 |
| `alembic check` | `No new upgrade operations detected.` |
| 新增运行记录测试 | 14 passed |
| 目录/知识/运行/边界组合测试 | 47 passed |
| 相关聚焦回归 | 157 passed |
| Q1 | 终态、FINAL UID、工序/物料/资源/事件计数 |
| Q2 | FINAL 3 → 父 UID 1/2，2 条 JOIN 边，事件可追溯 |
| Q3 | `arm_A` WAIT 1.0 s，holder/owner/task/instance 可追溯 |
| Q4 | 工序耗时、ready wait、输入/输出 UID、资源等待、失败列表 |
| 失败/中止 | `ABORTED` / `FAILED` 终态和 `RUN_END` 事件 |
| 写库失败 | 运行报告 `run_record.ok=false`；行标为 FAILED；不抛到仿真 |

**真实 `full_flow` 首次运行记录（2026-11-05）**：用户手动运行
`full_flow` 仿真无误：`run_id=run_20261105_160845_346206`、
`all_ok=true`、`final_count=1`、`operations_failed=0`、`steps=6726`、
`sim_time=337.15`。运行记录写入失败，`production_run` 状态为 `FAILED`，
原因为 `OperationRuntime` 缺少 `instances()` 方法。该 API 已补为只读查询
接口（返回 `graph.instances()`），并新增真实 `OperationRuntime` 回归测试。
**修复后尚未重跑真实 `full_flow` 生成成功运行记录**；因此不声明实机运行
记录已完成验收。

证据摘要：
[evidence/v1_run_record_persistence_failure_20261105.json](../evidence/v1_run_record_persistence_failure_20261105.json)。

## 6. 与方案的差异

- 方案 §2.2 只列了 `operation_instance`；为满足“不得用 JSON 数组替代外键
  关系”，额外增加 `operation_instance_input` / `operation_instance_output`
  两张关系表。
- 运行记录不建到具体 catalog release 的外键；运行事实保存
  `catalog_version`/`catalog_source`，因为默认运行使用代码目录而非发布行。
- `resource_event.holder` 是对 `ResourceEvent` 的小扩展，用于 Q3；WAIT
  仍由 `ResourceTable` 去重，不是每 tick 记录。
- 全部保留，暂无删除/保留期命令；阶段 6 的备份/恢复演练仍需单独实施。

## 7. 限制

- 只记录最终快照和事件，不记录逐 tick 状态；无法从运行记录回放完整物理过程。
- 强杀进程会留下 RUNNING 行；没有自动恢复标记。
- Q5（设备利用率/调度决策）和 Q6（跨运行对比）未实现；`dispatch_decisions`
  仍只在报告/内存中，`run_event` 中的 `SCHEDULE_*` 事件可用于后续扩展。
- 运行的 `result_json` 是紧凑摘要，不是完整 `v1_run_*.json`；完整报告仍由
  `application/run.py` 写入 `docs/implementation/runs/`。
