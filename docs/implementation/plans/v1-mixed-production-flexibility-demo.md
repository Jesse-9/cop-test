---
title: v1 混线生产灵活性扩展示范实施方案
type: plan
status: proposed
created: 2026-11-05
updated: 2026-11-05
scope: v1 UFLOW / L5_UFLOW / full_flow
related:
  - [[wiki/06-工业案例/柔性车间/混线生产业务与代码运行说明]]
  - [[wiki/06-工业案例/柔性车间/当前规划/06-22-混线生产阶段4产品变体与能力匹配调度]]
  - [[wiki/06-工业案例/柔性车间/当前规划/06-23-混线生产阶段5调度优化与有限恢复]]
  - [[implementation/reports/v1-stage-04-capability-scheduling]]
---

# v1 混线生产灵活性扩展示范实施方案

## 1. 目标与边界

在当前五岛 `L5_UFLOW` 场景和既有 `full_flow` 验收基线上，用尽量小的改动展示混线生产的业务灵活性。优先通过有限批次配置改变现有产品流的混合比例；只有完成具体工序的运动与资源验收后，才增加设备替代候选。

本方案不修改受保护的 `docs/scene_artifacts/v1/` 场景工件，不新增仿真 scenario，不引入 MES/订单服务、用户可编辑 recipe、通用 DSL、全局求解器、故障自动恢复或安全阈值调整。默认批次、设备映射、工序 DAG、物料提交和资源互锁必须保持原行为。

## 2. 当前实现与精确语义

- 场景为五岛 A–E，拥有五台 UR5、一个共享 AGV、四条物流线、A/C 交接区和 E 双输入缓冲；用户运行入口仍为 `full_flow`。
- `config.py` 的 `SCENARIO_BATCH` 以 `RAW_A`、`RAW_B`、`RAW_D` 记录有限输入数量。Coordinator 初始化时复制当前 scenario 的批次，`_poll_sources()` 按入口物料类型取计划数量。
- `default_process.py` 声明 `housing_direct`、`housing_alt` 与 `bracket_route`。`OrderEntry` 把入口、物料类型和 `route_variant_id` 固定绑定；`OperationRuntime.instantiate_entry()` 按绑定路线创建整条 UID 工序链。
- `RAW_A` 直达路线与 `RAW_B` AGV 绕行路线是不同来料/工序子图，最终汇入相同的壳体后续工序。调整两者数量能展示路线混批比例变化，但不表示相同物料可在运行中改道。
- 阶段 4 调度器能评估 recipe 声明的多个设备候选，但设备能力相同不等于输入/输出 zone、线体、dock、资源、几何和控制器都兼容。未验证的动态几何组合会被拒绝；当前物理工序各只有一个候选设备。
- 当前阶段 4 通过一次 `full_flow` 实机验收；报告注明等价设备候选扩展和 AGV 停靠/接触余量复核尚未完成。该基线不构成新候选的物理证据。

因此本方案严格区分两种结果：**批次组合灵活性**是第一阶段可做的最小扩展；**同一工序的多设备替代**是需要候选验证的条件工作包。若业务目标要求同一 UID 在多条路线间实时选择，则需额外定义相同输入物料可进入的替代入口/路线及其物理物流接驳，本方案第一阶段不声称支持该能力。

## 3. 推荐实施顺序

### WP0：固定基线

实施前保存以下基线信息，作为新旧运行对照：

1. 当前工件 ID、manifest、`config_digest`、`layout_digest` 和 CoppeliaSim 版本。
2. 默认 `full_flow` 批次 `RAW_A=1, RAW_B=1, RAW_D=1`。
3. 阶段 4 正式 evidence 中的完成数、路线、父 UID、派工决策和资源释放结果。
4. 当前工作区已有未提交规划修改必须保留；本工作包只新增本方案涉及的文件。

不为建立基线而重跑长时间仿真；现有证据仍匹配当前代码/工件时直接引用。若代码或场景已变化导致证据不适用，再补跑相应检查。

### WP1：增加有限批次输入

**目的：**在保持 `full_flow` 和固定工艺路线语义不变的情况下，用一个受控输入展示不同原料组合及相应路线的交错生产。

**建议接口：**在应用运行边界传入可选的 `batch` 映射，默认 `None`。未提供时严格使用 `SCENARIO_BATCH[scenario]`。配置只作为本次内存中的运行输入，不写入场景、manifest 或冻结 config digest。

**修改步骤：**

1. 在 `application/coordinator.py` 的 `Stage4Coordinator.__init__` 增加可选批次参数；当参数缺省时沿用当前 `dict(cfg.SCENARIO_BATCH[scenario])`。不要改变 scenario 名称、默认批次或 source spawn 顺序。
2. 在 coordinator 边界验证批次：键必须来自现有 source item types；值必须是非负整数且拒绝布尔值；缺省的已知类型按 `0` 处理。遇到未知键、负数或非整数时，在创建仿真任务前给出定位清楚的错误。
3. 将 `batch` 从 `application/run.py` 的 `run_scenario_v1()` 传给 coordinator。若要求通过产品 CLI 演示，再单独将有限配置名称接入 `cli.py`、`cmd_run()` 和 `run_loaded_scene_v1()`；首轮可以先通过应用层/测试注入，不开放任意 JSON、recipe 编辑或新 scenario。
4. 检查 coordinator 启动事件和运行快照均记录实际生效的批次。当前 coordinator start event 已记录 batch；runner 的 snapshot 目前直接读取静态 scenario batch，修改后必须改为记录传入并验证过的实际批次，避免报告与运行不一致。
5. 保持 `_poll_sources()` 根据 `OrderEntry.item_type` 读取批次数量，并继续调用现有 `_spawn_source()` / `instantiate_entry()`。不要在 source loop 中另写路线选择逻辑。
6. 为业务演示提供少量命名批次 profile，例如默认配比、壳体直达占比提高、AGV 绕行占比提高。profile 仅是现有 `RAW_A/RAW_B/RAW_D` 数量的有限集合；不同 profile 仍运行同一个 `full_flow`。

**优先修改文件：**

- `src/flexible_factory/application/coordinator.py`
- `src/flexible_factory/application/run.py`
- 如需 CLI 选择 profile，再改 `src/flexible_factory/cli.py`
- `tests/test_v1_coordinator_process.py` 与对应 runner/CLI 测试
- `tests/test_v1_stage4_scheduler_sim.py`（仅在新增实机 profile 验收时扩展）

避免仅为一个映射创建复杂 profile 框架。首轮可使用简单、不可变的命名 profile 表和一个校验函数；没有 CLI 演示需求时，不扩 CLI。

### WP2：验证批次灵活性

**纯逻辑测试：**

1. 未提供 batch 时，默认 `full_flow` 的批次数与当前完全一致。
2. 为 profile 指定 `RAW_A=2, RAW_B=1, RAW_D=2` 等输入时，各入口生成数量吻合，不同 UID 各自实例化正确的固定路线。
3. 某原料数量为 0 时不生成该入口物料，不产生空工序链或额外 AGV 任务。
4. 未知物料类型、负数、浮点数、字符串和布尔值均在仿真动作开始前拒绝。
5. 运行摘要、start event 与 snapshot 的实际 batch 字段相等；route summary 与该入口的静态 route mapping 相符。

**实机场景验收：**

1. 先跑现有默认 `full_flow` 验收，确认基线不回归。
2. 再用一个非默认 profile 跑一次 `full_flow`，检查完成/未配对数量与输入业务含义一致。
3. 核对每条 UID 的 route、工序图完成、最终 UID/父 UID、物料区域与物理状态、AGV 路线、调度事件和资源归还。
4. 如果仿真环境不可用，记录纯逻辑结果并将实机验收标为未执行，不报告为通过。

**成功标准：**默认运行保持现有验收结果；新 profile 的来源数量、固定路线实例化、工序提交与物料谱系相互一致；没有资源泄漏、重复 UID 或误报完成。路线占比变化只证明批次组合能力，不证明同一订单/UID 的动态选路能力。

### WP3：评估真实的备用设备用例（条件包）

此包只有在业务指定一个等价工序和一个候选设备后才开始。不得通过把五台机械臂都声明为同一 capability 来批量开放替代。

1. 从实际工序选择一个目标，不预先假定 `housing_finish`、A 或 C 可互换。
2. 对照 `OperationTemplate` 的输入/输出端口、source/sink、dock、zone 和资源，检查候选 `DeviceSpec` 与 `DeviceBinding` 是否完整覆盖。
3. 检查 coordinator 启动路径是否能将所选 device 映射到正确的 controller/executor；当前 arm 启动要求所选设备与运行工位匹配。若需要跨工位执行，必须先证明对应控制器目标与输出落点语义支持该操作。
4. 设备专属资源（例如 `arm_A`）不能错误地沿用为另一台设备资源；只在业务工序语义确认后，重算该 operation 的完整资源集合，保留 `ResourceTable.acquire_all()` 的原子申请约束。
5. 在 recipe allowlist 未改变的前提下，为目标候选做 focused CoppeliaSim 试验：目标 IK、路径、碰撞、抓取/吸附、载荷稳定、放置位置、物理属性、物料账本 commit 和后继解锁全部通过。静态绑定检查不能替代此证据。
6. focused 试验通过后，才把一个候选加入该 operation 的 `candidate_devices`。增加纯调度测试证明新候选可被选择、未验证组合仍拒绝、资源冲突无部分持锁；增加 coordinator 测试证明选定设备收到执行请求且任务实例记录选择设备。
7. 最后跑默认 `full_flow` 回归，确认设备选择与最终物料谱系仍符合预期；再保存目标替代设备的 focused evidence。默认 full_flow 通过不代替 focused evidence。

**停止条件：**若目标设备无法访问所需区域/线体、controller 不支持该 sink/source，或 IK/碰撞/负载/落点验证失败，则保留当前固定设备映射并记录失败原因。此时若业务仍要求该替代能力，再另立场景布局变更方案，明确新增承载、接驳、资源和 artifact 生命周期；不要通过放宽绑定或运动门槛伪造可替代性。

### WP4：可选的同一物料动态路线选择

只有业务明确要求同一产品输入 UID 可在直达路线与 AGV 路线之间择一时才另立此包。当前两条 housing route 的 `OrderEntry` 分别绑定不同物料类型和入口线，不能只在 `_spawn_source()` 加一个 route 参数就实现合法动态选路。

至少先定义并验证：

1. 两条路线接收的共同产品/原料类型与产品规格。
2. 该物料如何到达两个可选入口（物理可达路径、区域容量与资源）。
3. 路线选择的显式输入来源及确定性规则；首轮使用批次/订单输入，不读取不存在的健康、交期或队列事实。
4. 一个 UID 只实例化所选 route，未选分支不入图；路线在实例化后固定，运行中不得悄悄迁移。
5. 路线输入输出物料类型、区域、父子谱系、接续工序和 E 合流规则均通过工艺目录校验。

该包可能需要调整 `OrderEntry`/recipe 数据模型、来源生成、`OperationRuntime.instantiate_entry()` 参数、manifest `task_flow` 摘要与相应校验。它比批次 profile 明显更大，故不并入 WP1。

## 4. 实施与验证顺序

1. WP0：复核现有证据和本地变更边界。
2. WP1：增加可选批次输入及其验证/证据字段。
3. 跑受影响纯测试，再跑完整纯逻辑测试：`python -m pytest -q -p no:cacheprovider`。
4. 先跑默认 `full_flow` 基线，再跑一个不同 mix profile 的端到端验收；分开保存结果。
5. 只有 WP1 验收完成且存在明确业务用例时，再进入 WP3 或 WP4。每个条件包独立变更、独立 evidence、独立验收。

项目规定生产端到端测试需要 CoppeliaSim 4.10 和 ZeroMQ Remote API。运行仿真前先检查工件 digest 和加载校验，不执行 `build`、`migrate` 或覆盖当前工件来试验批次/调度逻辑。

## 5. 验收矩阵

| 维度 | WP1 批次混配 | WP3 设备替代 | WP4 同一物料路线选择 |
|---|---|---|---|
| 输入数据 | 已知原料类型的数量 profile | 一个已指定的 operation 与设备 | 明确产品类型、route 及选择输入 |
| 业务证明 | 路线产量占比可变 | 等价工序可由已验证设备执行 | 一个 UID 只走选定的合法路线 |
| 代码变更量 | 小：runner/coordinator 输入与记录 | 中：配方候选、资源/绑定及执行映射 | 大：入口/路线实例化与业务校验 |
| 仿真验证 | 默认与非默认 `full_flow` | focused 设备组合 + 默认回归 | 路线分支 focused + 默认回归 |
| 失败停止 | 输入非法时启动前拒绝 | 物理/绑定不成立则不开放候选 | 产品/承载/谱系未定义则不实现 |

## 6. 外部参考

- Google OR-Tools flexible job shop 示例：展示每道工序在允许机器集合中选择的建模方式。它支持“显式候选集合”的概念，但不覆盖本项目的 CoppeliaSim 几何验收与资源绑定。
  - [Flexible job shop CP-SAT example](https://github.com/google/or-tools/blob/stable/examples/python/flexible_job_shop_sat.py)
- CoppeliaSim 官方 IK 教程：说明逆运动学是实际机器人目标可达验证的一部分；本项目还需结合自身碰撞、路径、抓取与物料提交验证。
  - [Inverse kinematics tutorial](https://manual.coppeliarobotics.com/en/inverseKinematicsTutorial.htm)

