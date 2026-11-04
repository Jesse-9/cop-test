---
title: 柔性工业车间 v1 实施报告
type: report
status: review
created: 2026-11-03
updated: 2026-11-03
scope: v1 收敛与迁移
---

# 柔性工业车间 v1 实施报告

本报告记录 [V1 收敛与迁移方案](V1_CONSOLIDATION_PLAN.md) 的实施结果。
只记录已执行并核对的事实；未执行项和已知限制在 §6 明确列出。

## 1. 交付范围

- 产品包：`flexible-factory` 1.0.0，Python 包 `flexible_factory`，唯一
  console script `flexible-factory`。
- 唯一用户运行模式：`full_flow`；其他 scenario 仅通过显式 `--scenario`
  供内部回归/诊断。
- v1 场景：五岛 `L5_UFLOW`，由已验收 stage4 工件行为等价迁移。
- v1 工件命名空间：`docs/scene_artifacts/v1/`，manifest schema `2.0`，
  契约 `v1_contract_20261103`。
- MCP 不纳入 v1；`.mcp.json` 注册与旧服务器实现已删除。

## 2. 代码迁移

`src/flexible_factory/` 的模块按职责归并，算法、参数、容差、步进顺序和
碰撞/安全集合保持与阶段四一致。迁移采用行为等价复制 + 导入重写，未在
迁移中引入新控制行为。

| 目标模块 | 来源 |
|---|---|
| `domain/rules.py` | stage4 config 中的物料/任务/资源/指标规则 |
| `domain/materials.py` | `stage4/materials.py` |
| `domain/tasks.py` | `stage4/tasks.py`（阶段 0-1 的 `OperationInstance` 投影） |
| `domain/resources.py` | `stage3/resources.py` + `stage4/resources.py` 合并 |
| `domain/metrics.py` | `stage4/metrics.py` |
| `simulation/config_*.py` | stage1/2/3/4 配置等价迁移；v1 契约版本集中在 `spec.py` |
| `simulation/scene*.py` | stage1/3/4 场景构建/加载后 adopt |
| `simulation/layout*.py` | stage1/3/4 布局与几何 |
| `simulation/agv.py` | `stage4/agv_map.py` + `stage3/agv_route.py` |
| `simulation/runtime*.py` | stage3/4 影子臂、碰撞集合、规划器装配 |
| `simulation/unit_controller*.py` | stage1/2/3/4 单岛状态机 |
| `simulation/motion/*` | stage2 接触/碰撞/规划/轨迹/安全/吸附/停带 |
| `application/coordinator.py` | `stage4/coordinator.py`（阶段 0-1 改造后：从工艺目录读取任务字段） |
| `domain/process_model.py` | `stage4/process_model.py` |
| `domain/default_process.py` | `stage4/default_process.py` |
| `domain/operation_runtime.py` | `stage4/operation_runtime.py` |
| `application/lifecycle.py` | `stage1/lifecycle.py` 的 v1 收敛版本 |
| `application/run.py` | `stage4/run.py` |
| `artifacts/store.py` | `stage1/scene_artifact.py` 的 v1 schema 版本 |
| `artifacts/migration.py` | 新增 stage4 → v1 迁移 |
| `cli.py` | 新增唯一 v1 命令入口 |

阶段 0-1 的静态工艺目录/`OperationInstance` 移植细节见
[V1 阶段 0-1 移植报告](V1_STAGE0_1_REPORT.md)；它只在任务字段来源和状态投影层面
改变代码，不改变运动、吸附或资源行为。

依赖边界由 `tests/test_v1_import_boundary.py` 自动检查：

- CLI 导入时不加载 `stage1`–`stage4`、`stage1_1`、`reference`；
- `domain` 导入时不加载 `simulation`；
- `simulation` 导入时不加载 `application` 或 `cli`。

## 3. v1 工件与迁移

| 项目 | 值 |
|---|---|
| v1 工件 | `v1/L5_UFLOW/v001_20261103_004317` |
| 来源 | `stage4/L5_UFLOW/v004_20261029_133249` |
| 源 manifest SHA-256 | `e5f4d75c33c866394c60d2e444c029148a5de80051b4e15a0187f0a73fca5c3a` |
| 场景 SHA-256（源=v1） | `bf7e4df6681abf8027e3f04e3bb214fc5a1f707d1be8922a733fce424a2a352c` |
| manifest schema | `2.0` |
| product version | `1.0.0`（v001 记在 `extra.v1.product_version`；新 manifest 同时写顶层） |
| 契约版本 | `v1_contract_20261103` |
| 迁移时只读校验 | 452 项通过, 0 差异（保存于工件 `validation.json`） |
| 最终代码独立 `validate` | 454 项通过, 0 差异（新增版本字段检查） |

迁移验证：

1. `flexible-factory migrate --source <stage4 工件>` 完成复制、manifest/契约
   生成、重载、全量校验和 `v1/current.json` 原子更新。
2. 再次执行同一迁移返回 `migrated: false`，未创建新版本（幂等）。
3. 源 stage4 工件 manifest 与场景摘要未变化。
4. `flexible-factory load` → `validate` 在实机 CoppeliaSim 4.10 上通过
   454 项检查、0 差异，契约 `extra.v1` 通过（迁移期保存的
   `validation.json` 为 452 项；最终代码新增 product/contract 版本检查）。

> 迁移是“复制已验收场景 + 重写 manifest/契约”，不重跑场景构建；因此
> manifest `builder.module` 仍记录原始 `stage4.scene`，`provenance` 记录
> 来源工件与摘要。该字段是溯源信息，不是 v1 运行路径。

## 4. 测试与安装

| 检查 | 命令 | 结果 |
|---|---|---|
| v1 单元/契约/边界测试 | `python -m pytest -q tests/test_v1_*.py` | 340 passed |
| 干净安装 | 新建 venv + `pip install -e .` | 成功 |
| 唯一入口 | `flexible-factory --version` / `list` | `1.0.0` / 正确列出 v1 工件 |
| 全量测试 | `python -m pytest -q` | 340 passed |
| 导入边界 | `tests/test_v1_import_boundary.py` | 4 passed |

> 上表是 1.0.0 发布时（阶段 0-1）的计数。2026-11-03 完成阶段 2 后全量为
> 397 passed / 2 skipped，2026-11-04 完成阶段 3 后全量为
> 426 passed / 2 skipped（focused 设备验收需 `FLEXIBLE_FACTORY_SIM=1`），
> 见 [阶段 2 报告](V1_STAGE2_REPORT.md) 与
> [阶段 3 报告](V1_STAGE3_OPERATION_DAG_REPORT.md)。

覆盖的迁移回归包括：domain 物料/任务/资源/指标；阶段 0-1 工艺目录/任务投影；布局与 AGV 路网；
stage2 接触/碰撞/规划/轨迹/安全/吸附/停带；stage1 基础配置/布局/TCP/单元
状态机/传送带/差速驱动/吸附/IK/物料生命周期；生命周期状态机与运行锁；
v1 工件仓库 schema/current/保护。

## 5. 仿真验收

### 5.1 启动冒烟（20 s 仿真时间上限）

- 实机加载/启动点校验通过；五台 IK 与影子臂/碰撞集合构建完成。
- 20 s 上限触发 `SIM_TIME_LIMIT` 冻结，符合预期；冻结路径、任务失败证据
  与安全保持行为正常。
- 该次运行同时复现了历史 stage4 full_flow 中已知的 A 工位 `pre_place`
  IK_FAILED → `horizontal_detour_+0.30` 的规划路径（v1 tv=9.388965，
  历史 tv=9.388634），说明迁移未改变规划行为。
- 冻结后 `runtime.destroy()` 完整清理影子臂/碰撞集合（`leftover: []`），
  报告写入 `docs/implementation/runs/v1_run_full_flow_20261103_005913.json`
  （该次 `all_ok=false` 仅因人为 20 s 上限）。

### 5.2 `full_flow` 完整运行

复现命令（CoppeliaSim 4.10 已启动并开启 ZMQ server）：

    flexible-factory load
    flexible-factory run --scenario full_flow --time 700

运行结果（2026-11-03 01:09–01:40，报告
`docs/implementation/runs/v1_run_full_flow_20261103_010923.json`）：

| 字段 | 值 |
|---|---|
| `all_ok` | **true** |
| `sim_time` / `steps` | 344.25 s / 6868 |
| `tasks_done` | true；`any_task_failed=false`、`any_task_blocked=false` |
| FINAL | 1 件（UID 4），父 UID `[1, 3]`；`unpaired_semi_ce=1` |
| 安全/资源 | `any_safety_latch=false`；`resources_released=true` |
| AGV | `loaded_routes=2`；`route_ok=true`；`priority_ok=true` |
| 运行时清理 | `runtime_destroy` 无错误 |

任务完成时刻与历史 stage4 `L5_UFLOW/v004` 基线对照（仿真秒）：

| 任务 | v1 | stage4 v004 | 差值 |
|---|---:|---:|---:|
| `a_conveyor_uid1` | 48.85 | 48.90 | -0.05 |
| `b_conveyor_uid2` | 94.20 | 94.25 | -0.05 |
| `c_handoff_uid1` | 94.35 | 94.40 | -0.05 |
| `transport_ba_uid2` | 106.15 | 106.20 | -0.05 |
| `e_conveyor_uid1` | 124.35 | 124.15 | +0.20 |
| `a_agv_uid2` | 150.10 | 143.20 | +6.90 |
| `c_handoff_uid2` | 198.45 | 191.65 | +6.80 |
| `reposition_uid3_toD` | 211.55 | 204.65 | +6.90 |
| `d_conveyor_uid3` | 259.60 | 252.55 | +7.05 |
| `transport_de_uid3` | 282.65 | 275.65 | +7.00 |
| `e_agv_uid3` / `FINAL_CREATED` | 312.85 | 305.85 | +7.00 |
| `e_conveyor_uid2` | 344.25 | 337.30 | +6.95 |

前五个任务与历史基线相差 ≤0.2 s；A 工位从 AGV 卸料的任务比历史多约
6.9 s，之后所有任务保持同一偏移。最终物料 UID/谱系、FINAL 数量、未配对
数量、AGV 航线与安全/资源断言全部与历史验收口径一致。该偏移属于运行间
物理/规划候选差异，不影响验收结论；v1 未改变参数、容差或控制顺序。

证据摘要：[`v1_full_flow_acceptance_20261103_010923.json`](evidence/v1_full_flow_acceptance_20261103_010923.json)。

阶段 0-1 工艺目录移植后再次运行同一 `full_flow`：
337.45 s、13/13 任务 DONE、1 个 FINAL（父 UID 1+3）、1 个未配对
SEMI_CE、两条载货路线；证据
[`v1_stage0_1_full_flow_acceptance_20261103_132919.json`](evidence/v1_stage0_1_full_flow_acceptance_20261103_132919.json)。
任务/FINAL/物料/路线结果与上表一致。

### 5.3 代表性故障回归：`conveyor_fault`

命令：

    flexible-factory run --scenario conveyor_fault --time 400

结果（2026-11-03 01:42–01:51，报告
`docs/implementation/runs/v1_run_conveyor_fault_20261103_014225.json`）：

| 字段 | 值 |
|---|---|
| `all_ok` / 验收 `ok` | **true** |
| `sim_time` / `steps` | 95.45 s / 1892 |
| 故障注入 | `FAULT_INJECT conveyor_stop`，`line_ce` 停带 |
| 冻结 | `frozen=true`，`stop_reason=CONVEYOR_FAULT: line_ce 停止` |
| 任务 | `e_conveyor_uid1` 进入 `TASK_BLOCK: CONVEYOR_FAULT`；`D` 任务失败并安全保持 |
| FINAL | 0 件（故障后不生成） |
| 资源/安全 | `resources_released=true`；无安全锁存 |
| 运行时清理 | `runtime_destroy` 无错误 |

该回归验证了停带故障下冻结现场、阻塞受影响任务、不生成 FINAL、释放普通
调度资源且不自动恢复的行为。

证据摘要：[`v1_conveyor_fault_acceptance_20261103_014225.json`](evidence/v1_conveyor_fault_acceptance_20261103_014225.json)。

## 6. 未执行项与已知限制

- `full_flow` 已完成实机验收（§5.2）；`conveyor_fault` 代表性故障回归
  已通过（§5.3）。`agv_blocked` 未执行，发布前应补齐或明确接受该覆盖缺口；
  不得把未执行场景报告为通过。
- 无 Windows + CoppeliaSim CI agent；集成验收由受控本地流程执行。
- 阶段 0-1 静态工艺目录已移植到 v1。阶段 2（设备绑定与执行适配器）已于
  2026-11-03 完成，阶段 3（配方实例化与工序 DAG）已于 2026-11-04 完成并
  通过 `full_flow` 实机验收，见 [阶段 2 报告](V1_STAGE2_REPORT.md) 与
  [阶段 3 报告](V1_STAGE3_OPERATION_DAG_REPORT.md)；阶段 4–5（能力匹配、
  调度优化/恢复）仍未实现，规划文档保留在 06-22–06-23。
- 阶段 3 只覆盖 `full_flow`；`no_pair`/`staggered_batch`/故障注入场景未按
  阶段 3 逐项重跑，不得报告为通过。
- 阶段三安全锁存仍不自动恢复；统一故障恢复未实现。
- v1 运行期保留已验证类名 `Stage4Scene`/`Stage4UnitController`/
  `Stage4Coordinator`，以行为等价为先；重命名为 v1 中性名称属于后续清理，
  不影响产品/工件标识。
- `simulation/unit_controller*.py` 仍保留已验证的继承链
  （Stage1 → Stage2 → Stage3 → Stage4）。方案 §4 建议用组合替代继承；
  组合化会改变状态机调用顺序，未在本次行为等价迁移中实施，列为后续重构项，
  当前不声称已完成。
- `application/coordinator.py` 仍是多岛编排整体，业务规则已抽到
  `domain/`，但“领域决策/仿真动作端口”的进一步拆分未实施；当前以行为等价
  和可运行交付为先。
- 历史 `src/stage1`–`stage4`、`src/stage1_1`、`src/reference`、旧
  `src/run_stage*.py` / `src/run_scene_lifecycle.py` 包装入口、旧阶段测试、
  报告、测量资料和录制工具已删除，v1 唯一入口是 `flexible-factory`。
- MCP 已移除；如后续恢复，必须新增 v1 适配器，禁止再调用 reference 后端。

## 7. 里程碑状态

| 里程碑 | 状态 | 证据/说明 |
|---|---|---|
| M0 冻结范围与基线 | 完成 | [V1_BASELINE.md](V1_BASELINE.md)；492 passed 基线 |
| M1 v1 包骨架 + domain | 完成 | `src/flexible_factory/domain/`；domain 导入边界测试 |
| M2 simulation 公共能力 | 完成 | 行为等价复制；差分对比除日志/版本标识外无代码差异；340 项 v1 测试 |
| M3 独立工件契约与迁移 | 完成 | v1 工件、452 项校验、幂等迁移、源工件摘要不变 |
| M4 统一入口、打包与接入 | 完成 | `flexible-factory` console script、干净安装、`uv.lock`、MCP 注册与实现删除 |
| M5 文档与验收 | 完成 | 文档已更新；`full_flow` 与 `conveyor_fault` 通过；`agv_blocked` 未执行（§6） |
| M6 移除旧入口并发布 | 完成 | 安装包只含 v1；旧源码、旧入口、旧报告/证据、MCP 实现均已删除；仅保留迁移来源工件 |
