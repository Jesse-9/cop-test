---
title: v1 混线生产灵活性扩展示范实施报告 — 有限批次配比
type: report
status: review
created: 2026-11-05
updated: 2026-11-06
scope: v1 UFLOW / full_flow / WP1-WP2
related:
  - docs/implementation/plans/v1-mixed-production-flexibility-demo.md
  - ../../wiki/06-工业案例/柔性车间/混线生产业务与代码运行说明.md
  - ../../wiki/06-工业案例/柔性车间/当前规划/06-22-混线生产阶段4产品变体与能力匹配调度.md
  - docs/implementation/reports/v1-stage-04-capability-scheduling.md
  - docs/implementation/evidence/v1_full_flow_default_20261105_225825.json
  - docs/implementation/evidence/v1_full_flow_direct_heavy_20261105_233212.json
  - docs/implementation/evidence/v1_full_flow_detour_heavy_20261106_003836.json
  - docs/implementation/evidence/v1_full_flow_detour_heavy_rejected_0_2_2_20261106_000425.json
---

# v1 混线生产灵活性扩展示范实施报告：有限批次配比

本报告记录
[混线生产灵活性扩展示范方案](../plans/v1-mixed-production-flexibility-demo.md)
WP1（有限批次输入）与 WP2（批次灵活性验证）的实施结果。WP3（等价设备
替代）和 WP4（同一物料动态选路）仍是条件工作包，未立项、未实施。

结论：WP1 已实施；WP2 的纯逻辑与实机验收均完成。默认 `full_flow`、
`direct_heavy`、`detour_heavy` 三组实机运行 `all_ok=true`；初版
`detour_heavy=0/2/2` 在 300 s 背压上限处失败，作为可行性边界保留，并
收窄为 `0/1/1` 后重跑通过。WP3（等价设备替代）和 WP4（同一物料动态
选路）仍未立项、未实施。纯逻辑回归 **531 passed / 3 skipped**；实机
为 3 组通过 + 1 组失败边界。

## 1. 代码落点

| 文件 | 改动 |
|---|---|
| `src/flexible_factory/application/batch.py` | 新增：批次校验、命名 profile、默认解析、物料数量期望 |
| `src/flexible_factory/application/coordinator.py` | 新增可选 `batch` 参数；start event、summary、acceptance 记录实际批次；验收数量由批次推导 |
| `src/flexible_factory/application/run.py` | `run_scenario_v1` / `run_loaded_scene_v1` 接收批次或 profile；快照与运行报告记录实际生效批次 |
| `src/flexible_factory/cli.py` | 新增 `run --batch-profile`；在连接仿真和取得运行锁之前校验批次 |
| `src/flexible_factory/spec.py` | 新增 `BATCH_INPUT_VERSION = "v1_batch_20261105"` |
| `tests/test_v1_batch_profiles.py` | 新增 24 项纯逻辑测试 |
| `tests/test_v1_coordinator_dag.py` | 增加默认批次、显式批次来源生成、摘要/验收批次字段测试 |
| `tests/test_v1_coordinator_process.py` | 增加非法批次在协调器构造期拒绝的测试 |

未修改 `src/flexible_factory/simulation/config.py`、场景工件、
manifest、`config_digest`、工艺目录或 `PRODUCT_VERSION`。批次是单次运行
输入，不进入冻结配置摘要。

### 1.1 基线核对

按方案 WP0 引用现有工件与证据，不为建立基线重跑仿真：

| 项 | 值 |
|---|---|
| 当前工件 | `v1/L5_UFLOW/v001_20261103_004317` |
| `manifest_digest` | `1bc46e1bf036578e6da4ae2dec800778781b54855f0771b837cec5076cb7d38e` |
| `scene_sha256` | `bf7e4df6681abf8027e3f04e3bb214fc5a1f707d1be8922a733fce424a2a352c` |
| manifest `config.digest` | `e292c37f6d2a611763a9d76f508ccb219e16c089e3ce6e6e67f9c700495b6c7c` |
| 当前代码 `config_digest()` | `e292c37f6d2a611763a9d76f508ccb219e16c089e3ce6e6e67f9c700495b6c7c`（一致） |
| `PRODUCT_VERSION` / schema | `1.0.0` / `2.0` |
| `SCENE_CONTRACT_VERSION` | `v1_contract_20261103` |
| 默认 `full_flow` 批次 | `RAW_A=1, RAW_B=1, RAW_D=1` |
| 阶段 4 正式证据 | `evidence/v1_stage4_full_flow_20261104_210605.json`（`all_ok=true`，FINAL 1，父 UID `[1,3]`） |

阶段 4 证据早于本次代码改动，只作为默认路径的历史基线引用；本次未重跑
实机，默认路径的回归结论仅来自纯逻辑测试，不能把历史证据当作新代码的
实机验收。

## 2. 行为语义

### 2.1 默认路径

- `resolve_batch(scenario)` 严格返回 `SCENARIO_BATCH[scenario]` 的副本；
  `Stage4Coordinator` 不传 `batch` 时同样读取场景默认值。
- 现有 scenario、来源生成顺序、固定 route variant、设备候选、资源互锁
  和物料提交路径均未改变。
- `full_flow` 默认批次仍为 `RAW_A=1, RAW_B=1, RAW_D=1`，验收期望仍为
  1 个 FINAL、1 个未配对 SEMI_CE。

### 2.2 显式批次与命名 profile

- 应用层可传 `batch={"RAW_A": 2, "RAW_B": 0, "RAW_D": 2}`；缺省的已知
  原料类型按 `0` 处理，未知键、负数、浮点数、字符串和布尔值在任何任务
  创建前由 `BatchError` 拒绝。
- CLI 新增有限命名 profile：

| profile | RAW_A | RAW_B | RAW_D | 业务含义 |
|---|---:|---:|---:|---|
| `default` | 场景默认 | 场景默认 | 场景默认 | 不改变现有运行 |
| `direct_heavy` | 2 | 0 | 2 | 壳体直达路线占比提高 |
| `detour_heavy` | 0 | 1 | 1 | 壳体 AGV 绕行占比提高到 100% |

- 非默认 profile 只允许用于 `full_flow`，避免改变内部回归/诊断 scenario
  的验收口径；CLI 在连接仿真之前拒绝非法组合。
- 两个非默认 profile 按批次推导分别为 2 个 FINAL（`direct_heavy`）和
  1 个 FINAL（`detour_heavy`），未配对均为 0；变化的是壳体路线比例，
  不是单个 UID 的动态选路。初版 `detour_heavy` 曾定义为 `0/2/2`，实机
  在 413.4 s 触发 `BACKPRESSURE_TIMEOUT`（第二个 `RAW_B` 等待 AGV 空载
  调机达到 300 s 上限），因此收窄为 `0/1/1`；失败记录保留在本报告
  §4.3。

### 2.3 验收期望

`expected_flow_counts()` 按 E 岛 1:1 合流推导：`SEMI_CE` 数量为
`RAW_A + RAW_B`，`PART_DE` 数量为 `RAW_D`，FINAL 为两者较小值，未配对
`SEMI_CE` 为多出的壳体数量。该函数只用于验收期望和报告；协调器仍以物料
账本提交为唯一成功边界。

## 3. 证据字段

- `COORDINATOR_START.detail.batch`、`summary()["batch"]`、
  `acceptance()["batch"]` 三者相等。
- `summary()` / `acceptance()` 增加 `batch_input_version`；
  `acceptance()["batch_expectation"]` 记录由批次推导的期望数量。
- 场景快照 `config.batch`、`config.batch_profile`、
  `config.batch_input_version` 记录实际生效值。
- `run_scenario_v1()` 证据顶层增加 `batch`、`batch_profile`、
  `batch_input_version`，避免运行报告与静态 scenario 批次混淆。

## 4. 测试与实机验收结果

### 4.1 纯逻辑

执行命令：

    python -m pytest -q -p no:cacheprovider

结果：**531 passed, 3 skipped**。3 个 skip 为
`tests/test_v1_stage2_arm_sim.py`、`tests/test_v1_stage2_agv_sim.py`、
`tests/test_v1_stage4_scheduler_sim.py`，需要 CoppeliaSim 4.10 + ZMQ
23000，未纳入本轮 pytest 实机用例。

新增测试覆盖：

1. 未提供批次时，各 scenario 的默认批次与 `SCENARIO_BATCH` 完全一致。
2. profile 表不可变、键覆盖全部来源物料类型、解析返回独立副本。
3. 部分批次缺省类型补 0；未知键、负数、浮点、字符串、布尔值、非映射
   均在仿真动作前拒绝。
4. 非默认 profile 在非 `full_flow` scenario 和未知 profile 名下拒绝；
   `batch` 与 `profile` 不能同时指定。
5. 显式批次下来源只生成已声明的入口，UID 按固定 route variant 建图；
   数量为 0 的入口不生成物料或工序链。
6. start event、summary、acceptance 与 snapshot 的批次字段一致。
7. CLI `--batch-profile` 解析，以及非法组合在连接前返回 `BATCH_INPUT`。

### 4.2 实机验收

环境：CoppeliaSim 4.10、ZMQ 23000、当前工件
`v1/L5_UFLOW/v001_20261103_004317`。每次运行由 `run` 加载工件并完成
启动点只读校验后执行。

| profile | batch (A/B/D) | 仿真时间 | FINAL / 父 UID | 未配对 | 工序实例 | 调度决策 / 等待 | 载货航线 | `all_ok` | 证据 |
|---|---|---:|---|---:|---:|---:|---:|---|---|
| `default` | 1/1/1 | 337.70 s | 1 / `[1,3]` | 1 | 12 | 11 / 1 | 2 | true | [JSON](../evidence/v1_full_flow_default_20261105_225825.json) |
| `direct_heavy` | 2/0/2 | 290.50 s | 2 / `[1,2]`,`[3,4]` | 0 | 14 | 12 / 2 | 2 | true | [JSON](../evidence/v1_full_flow_direct_heavy_20261105_233212.json) |
| `detour_heavy` | 0/1/1 | 275.95 s | 1 / `[1,2]` | 0 | 9 | 8 / 0 | 2 | true | [JSON](../evidence/v1_full_flow_detour_heavy_20261106_003836.json) |

三组通过运行均满足：`tasks_done=true`、`graph_complete=true`、
`any_safety_latch=false`、`resources_released=true`、`scheduler_ok=true`、
`route_ok=true`，运行记录状态 `DONE`，`runtime_destroy.leftover` 为空。
默认批次与阶段 4 基线一致：FINAL 父 UID `[1,3]`、12 个工序实例、
11 条调度决策；历史阶段 4 证据见
`evidence/v1_stage4_full_flow_20261104_210605.json`。

`direct_heavy` 的两条载货航线均为 `D_to_E_loaded`；`detour_heavy` 的
载货航线为 `B_to_A_loaded` 与 `D_to_E_loaded`，对应 100% 壳体绕行。
批次来源、固定 route variant、物料谱系、资源归还和运行记录均与批次
语义一致。

### 4.3 失败边界：初版 `detour_heavy = 0/2/2`

初版 profile 使用 `RAW_A=0, RAW_B=2, RAW_D=2`，实机结果：

| 项 | 值 |
|---|---|
| 结果 | `all_ok=false`，`BACKPRESSURE_TIMEOUT` |
| 仿真时间 | 414.35 s |
| FINAL | 1 件，父 UID `[1,2]` |
| 工序实例 | 17，其中 1 个 FAILED |
| 调度决策 / 等待 | 10 / 2 |
| 资源/安全 | 资源已释放，无安全锁存 |
| 运行记录 | `ABORTED` |
| 证据 | [JSON](../evidence/v1_full_flow_detour_heavy_rejected_0_2_2_20261106_000425.json) |

失败链：第二个 `RAW_B`（`b_conveyor_uid3`）在 113.35 s 创建后等待 AGV
到 dock B；AGV 先服务 `transport_de_uid2` 与 E 岛放置，376.05 s 才从 E
空载调机去 B，航线距离 7.35 m、耗时约 37 s；任务在 413.35 s 达到
`BACKPRESSURE_MAX_WAIT_S = TASK_TIMEOUT = 300 s` 被标记失败，协调器
冻结并中止。该结果说明此配比超出当前阶段 4 AGV 调度与背压上限的可行
范围，**不是 WP1 批次输入代码的回归**；按方案不调整冻结超时或调度策略，
而是把 `detour_heavy` 收窄为 `0/1/1`（绕行占比 100%，AGV 负载与默认
批次相同），重跑通过。

### 4.4 运行环境稳定性

初版 `0/2/2` 失败后，在同一 CoppeliaSim 会话进行第 4 次运行尝试时，
进程以 `0xC0000409`（十进制 `3221226505`）退出，运行端在连接阶段挂起，
已终止；重启全新 CoppeliaSim 进程后 `detour_heavy=0/1/1` 通过。本次
实机结论因此以每次运行的有效进程状态为准；该会话退出未归因于批次代码，
也不宣称“同一会话连续多次运行”已稳定。

## 5. 未执行与限制

- WP3 未开始：默认工艺仍为每个工序单一固定设备候选；批次变化不开放
  设备替代。进入 WP3 需要业务指定一个目标工序和一个候选设备。
- WP4 未开始：`RAW_A` 与 `RAW_B` 仍是不同来料/入口的固定路线，不能把
  批次配比解释为同一 UID 的动态选路。
- 每个通过的 profile 只执行一次实机运行；不宣称重复性、统计稳定性或
  节拍优化。
- 运动余量复核仍沿用阶段 4 的限制；本报告未重新审计 AGV 停靠/接触
  余量，`stop_error` 通过只表示本次运行在既有阈值内。
- 同一 CoppeliaSim 会话连续运行存在上述进程退出风险；正式验收建议
  使用全新进程。
- 产品版本仍为 `1.0.0`；本次只新增运行输入契约版本
  `BATCH_INPUT_VERSION`，与仓库既有“Unreleased 累积、发布时提升产品
  版本”的做法一致。

## 6. 后续门槛

1. WP3：只有业务指定具体工序与候选设备后，才做绑定/资源检查和 focused
   几何验收；默认 `full_flow` 回归不能代替 focused evidence。
2. WP4：只有业务明确要求同一 UID 在直达与绕行路线间择一时，才定义共同
   来料、可行入口与确定性选择规则。
3. 若业务需要更多 `RAW_B` 同时在场，应先评估阶段 5 的 AGV 调度/背压
   策略，而不是放宽 300 s 超时或继续增大 profile。
4. 实机验收使用全新 CoppeliaSim 进程，并为每个 profile 单独保存 evidence；
   失败运行保留为边界证据，不按通过报告。
