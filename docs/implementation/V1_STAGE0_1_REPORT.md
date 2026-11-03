---
title: v1 阶段 0-1 工艺目录移植报告
type: report
status: review
created: 2026-11-03
updated: 2026-11-03
scope: v1 UFLOW / 阶段 0-1
---

# v1 阶段 0-1 工艺目录移植报告

本报告记录把阶段四 `src/stage4/` 中的阶段 0-1 静态工艺目录接入 v1
`src/flexible_factory/` 的结果。阶段 0-1 最初基于原四个阶段实现；v1 收敛
移除了旧 `stage1`–`stage4` 包，因此本次不是文件级合并，而是保持行为等价
的代码移植。

未实现的混线生产阶段 2–5 规划仍保留在
[06-18](../wiki/06-工业案例/柔性车间/当前规划/06-18-柔性车间混线生产工序与设备调度演进规划.md)
等文档中，不代表 v1 已具备这些能力。

## 1. 移植范围

新增/调整 v1 模块：

| v1 模块 | 职责 |
|---|---|
| `domain/process_model.py` | `RecipeSpec`、`OperationTemplate`、`RouteVariant`、`OrderEntry`、`ProcessCatalog` 与校验 |
| `domain/default_process.py` | 默认配方 `mixed_line_v1`；`PROCESS_MODEL_VERSION = v1_process_20261103` |
| `domain/operation_runtime.py` | `OperationInstance` 单一运行时状态 |
| `domain/tasks.py` | `Stage4Task` 旧字段只读投影、`make_task_from_operation`、任务队列 |
| `domain/rules.py` | 增加 `UNIT_IDS`、`SOURCE_SPECS`，供纯 domain 目录校验 |
| `application/coordinator.py` | 删除固定 `UNIT_TASKS`/`TASK_RESOURCES`/`TASK_TYPES`/`FOLLOWUP` 等映射，改读默认目录 |
| `simulation/contract.py` | manifest `task_flow` 记录 process model 版本、recipe 和路线摘要 |

保持不变的执行层：`simulation/unit_controller.py`、
`simulation/agv.py`、`simulation/motion/*`、`domain/materials.py`、
`domain/resources.py`。工艺目录只改变任务字段来源和状态投影，不改变
IK、路径、吸附、碰撞、安全锁存或物料事务。

## 2. 工艺对象

默认配方 `mixed_line_v1` 包含 10 个工序：

```text
housing_direct_A → housing_finish → housing_to_E_buffer
housing_alt_B → transport_BA → housing_alt_A → housing_finish
              → housing_to_E_buffer
bracket_process → transport_DE → bracket_to_E
kit_join: buffer_e_c:SEMI_CE + buffer_e_d:PART_DE → output_e:FINAL
```

- `housing_direct` / `housing_alt` 为同组互斥路线；`housing_finish` 用
  `predecessor_policy=any` 表示任一路线终端完成即可。
- `kit_join` 用 `predecessor_policy=all` 表达 E 岛 AND 合流。
- `kit_join` 是逻辑合流 successor，不创建 `Stage4Task`；由
  `_check_join`/`MaterialRegistry.commit_join` 执行。
- `OperationInstance` 是任务状态唯一存储；`Stage4Task.status/phase/时间/
  证据/资源` 为受控代理，旧字段只在序列化边界输出。

术语和逐工序/设备映射见
[06-24](../wiki/06-工业案例/柔性车间/当前规划/06-24-混线生产术语与对象模型.md)、
[06-25](../wiki/06-工业案例/柔性车间/当前规划/06-25-混线生产工艺流程与设备映射.md)。

## 3. 兼容与版本

- v1 产品版本仍为 `1.0.0`；工艺模型版本为 `v1_process_20261103`。
- v1 场景契约 `v1_contract_20261103` 只在 manifest `task_flow` 中新增
  `process_model_version`/`recipe_id`/`process_routes`；现有 v1 工件缺少
  这些字段时按“存在才校验”处理，仍可加载。
- 旧阶段 config 值已迁入 `domain/rules.py`；`TASK_FLOW`/`TASK_PRIORITY`/
  `PROCESS_TRANSITIONS`/`JOIN_*` 保持冻结数值，目录启动时核对。
- v1 工件 `v1/L5_UFLOW/v001_20261103_004317` 和来源 stage4 工件均未修改。

## 4. 验证

- v1 全量纯测试：`python -m pytest -q`，**340 passed**。新增/移植测试：
  - `tests/test_v1_process_model.py`：目录正向校验、路线/AND 合流、旧映射
    等价、任务字段投影及负向校验；
  - `tests/test_v1_coordinator_process.py`：协调器从目录读取字段，逻辑合流
    继任不创建任务；
  - `tests/test_v1_layout_agv.py`：资源顺序改读默认目录。
- 既有 v1 `full_flow` 验收（移植前）：
  `docs/implementation/evidence/v1_full_flow_acceptance_20261103_010923.json`，
  13/13 任务 DONE、1 个 FINAL（父 UID 1+3）、1 个未配对 SEMI_CE、
  两条载货路线。
- 移植后 `full_flow` 端到端复核：见 §5。

## 5. 端到端复核

移植后在 v1 工件上运行：

```powershell
flexible-factory run --scenario full_flow --time 700
```

- 运行报告：`docs/implementation/runs/v1_run_full_flow_20261103_132919.json`
- 证据摘要：
  `docs/implementation/evidence/v1_stage0_1_full_flow_acceptance_20261103_132919.json`
- 结果：`all_ok=true`；仿真 337.45 s / 6732 steps；13/13 任务 DONE；
  1 个 FINAL（父 UID 1+3）；1 个未配对 SEMI_CE；两条载货路线
  `B_to_A_loaded`、`D_to_E_loaded`，`route_ok=true`；无安全锁存，
  资源全部释放；运行时清理无错误。
- `summary.process_model.version = v1_process_20261103`，
  `recipe_id=mixed_line_v1`。
- 与移植前 v1 `full_flow`（344.25 s，13/13 DONE，FINAL 父件 1+3）
  的任务/FINAL/物料/路线结果一致；仿真时间差异属于运行波动。

第一次移植运行已完成全部任务，但在 `coordinator.summary()` 中仍使用
旧的 `application.metrics` 导入，导致报告写入失败；已改为
`domain.metrics` 并单独提交，第二次运行通过。该问题说明端到端运行
不能由纯测试完全替代。

## 6. 边界

- 阶段 2–5（设备执行适配器、工序 DAG、能力匹配、调度优化/恢复）未实现；
  规划文档保留在 `06-20`–`06-23`。
- 未恢复 `src/stage1`–`src/stage4`；v1 运行路径只使用
  `src/flexible_factory/`。
- 未改变运动、吸附、碰撞、安全、故障冻结和物料谱系语义。
