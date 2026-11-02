# 阶段四实施报告：多岛柔性车间

日期：2026-10-28（实施） / 2026-10-29（验收）
上游计划：[06-16-柔性工业车间阶段四多岛实施步骤](../wiki/06-工业案例/06-16-柔性工业车间阶段四多岛实施步骤.md)
父工件：`stage3/L2_MIRROR/v006_20261028_180722`（stage3，protected）
阶段四工件：`stage4/L4_COLUMN/v008_20261028_225634`（重排前基线）；阶段 4.1 工件：`stage4/L5_UFLOW/v004_20261029_133249`（§12）

> 本报告记录阶段四代码、L4_COLUMN 场景派生、五岛任务链、共享 AGV
> 调度、E 岛配对与验收结果。阶段四不包含阶段 2.1 的统一故障恢复：
> 安全锁存、航线失败或传送带停带故障后冻结现场，任务不得改写成 DONE；
> 复位/续作仍归项目最终故障治理阶段。
>
> 2026-11-02 的混线生产阶段 0-1（`full_flow` 基线冻结与静态工艺目录）
> 另见 [PHASE4_0_1_REPORT.md](PHASE4_0_1_REPORT.md)；本报告的验收数据
> 仍是其基线来源，场景与契约未变。

## 1. 结论

阶段四在已验收阶段三工件上增加 C/D/E 三个工位、S1→B / S2→D / C→E
三条物流线、S2 仓库、A→C 固定交接和 E 岛两个输入缓冲，形成
S1→A/B、S2→D、A→C、C→E、B→A、D→E 的多岛物流链，并在 E 岛按
SEMI_CE + PART_DE 配对生成 FINAL。

实现要点：

- 五台 UR5 各自拥有独立 simIK environment、影子臂、碰撞集合、
  `ConfigurationChecker`、`LocalPlanner` 和 `Stage2Safety`；其余真实臂的
  可见连杆/吸盘 Body 与岛体加入本臂工作区，会话结束恢复查询属性。
- 四条物流线在构建期把 `__config__.targetVel` 设为 0，仿真启动时保持
  停止；只有对应单元任务启动后才由控制器下令运行，避免等待期间物料
  自行走完行程。
- 任务按固定顺序**一次性申请完整资源集**；任一资源忙则不持有部分资源。
  AGV 航线持有 `agv_lane` 与全部 dock，相邻臂通过 AB/AC/CE 互锁区串行化。
- AGV 所有 dock 共线于 x=0.40，车头朝 −Y，单段直线，不在 dock 旋转；
  B→A 优先级 0、D→E 优先级 1 并等待老化；运行中任务不抢占；A/E 卸载
  任务待执行时车辆不离开该 dock。
- 物料谱系覆盖 RAW_A/RAW_B/SEMI_AB/SEMI_AC/SEMI_CE/RAW_D/PART_DE/FINAL；
  每条线/交接/缓冲/载台容量 1；E 岛只在两个缓冲各有正确类型时原子消费
  并生成一个 FINAL，记录两条父 UID，重复/缺件/错型拒绝。
- AGV 阻塞进入 BLOCKED，清障且无安全锁存、所有权重新确认后低速续行；
  传送带停带故障使受影响任务 BLOCKED 并冻结现场；安全锁存/航线失败/
  超时冻结全部任务，保留现场，不自动重试/复位。

> 验收结果见 §7；本报告在全部场景运行完成后冻结。

> 阶段四场景布局、平台高度差和设备联动的复审与优化计划见
> [[wiki/06-工业案例/06-17-柔性工业车间阶段4.1场景布局与联动优化方案]]；
> 该方案已于 2026-10-29 实施为 `L5_UFLOW`（§12）。`L4_COLUMN` 及本文
> §1–11 的验收数据作为重排前基线保留。

## 2. 工件与契约

- 父工件：`stage3/L2_MIRROR/v006_20261028_180722`
  - `source_phase = stage3`，stage3 契约 `stage3_contract_20261025_v1`；
  - 父工件未被修改。
- 阶段四工件：`stage4/L4_COLUMN/v008_20261028_225634`
  - `source_phase = stage4`，`protected = true`；
  - scene sha256 `f3733a14151d5ed6562df269f45b7ffd16a36cb9e2233e9ee9c5a0217857894d`；
  - manifest digest `5bbf177512c37facd50946fc3f2481343616869198604521aa100e2350a262f5`；
  - `extra.stage4.contract_version = stage4_contract_20261028_v1`；
  - 控制器 `stage4.unit_controller.Stage4UnitController`；
  - 协调器 `stage4.coordinator.Stage4Coordinator`；
  - runner `stage4.run.run_loaded_scene_stage4`；
  - stage4 config digest `7e298e21c9d5bd9d2f843b34d8ef5cc5f43152d1e6bd2856c64f49018dc88983`；
  - layout digest `180d9328a0089805592a5c5a160bbd555af4dd0801d37144a967debeb1e1d1f1`；
  - 保存后重载只读校验 **452 项通过，0 差异**；
  - 父差异 3 项：新增 C/D/E 与三条线等对象、AGV dock D/E 传感器、
    A→C 交接与 E 缓冲/输出；A/B 坐标、AGV 初始位姿和地面未变。
- 历史 `v001–v008`：v001 是父摘要读取错误的失败草稿（已删除）；v002 首次
  通过校验但物流线仍以 `targetVel=0.2` 启动；v003 起物流线启动即停止；
  v004 抬高 C 岛/基座到 0.45 m（v003 在 C 接近交接点时
  `GOAL_CONFIG_INVALID`）；v005 将 S1→B 支线靠近 B 臂；v006/v007/v008
  调整 S2→D 支线、D 岛高度/尺寸与 D 基座，使 B/D 的 H_transfer=0.80
  垂直抬升处于 UR5 可达范围。current 指向 v008。
- 阶段 4.1 的工件、契约（`stage4_contract_20261029_v2`）、布局与
  摘要见 §12.1；`L5_UFLOW/v004` 是当前 current，`L4_COLUMN/v008`
  作为重排前基线保留。

## 3. 代码模块

| 模块 | 作用 |
|---|---|
| `src/stage4/config.py` | 五岛布局、物流线、物料类型、区域容量、资源、AGV、场景与验收参数 |
| `src/stage4/layout.py` | `L4_COLUMN`、`LineSpec`、静态几何/粗可达性筛选、序列化 |
| `src/stage4/scene.py` | 从 stage3 工件派生 C/D/E/三条线/E 缓冲、`Stage4Scene` 与契约 |
| `src/stage4/materials.py` | 多类型谱系、区域占用、工序转换、E 岛原子配对 |
| `src/stage4/tasks.py` | 任务状态、优先级、D→E 老化、去重与拒绝记录 |
| `src/stage4/resources.py` | 固定顺序资源表与全有或全无申请 |
| `src/stage4/agv_map.py` | 直线路线图、空载调机、阶段三航线控制器包装 |
| `src/stage4/runtime.py` | 五台影子臂、碰撞集合/规划器/锁存、按包络过滤物流线 |
| `src/stage4/unit_controller.py` | 传送带/AGV/固定交接三种取料源与三种放置汇 |
| `src/stage4/coordinator.py` | 来源生成、任务编排、AGV 调度、配对、背压、冻结与证据 |
| `src/stage4/metrics.py` | 事件级节拍/吞吐/WIP/利用率重算 |
| `src/stage4/scene_contract.py` | `extra.stage4`、布局/配置摘要与只读校验 |
| `src/stage4/run.py` | 五个验收场景 runner、证据与 CLI |
| `src/run_stage4.py` | CLI 兼容入口；`uv run run-stage4` |

阶段一/二/三兼容扩展（默认行为不变）：

- `stage1/material_manager.py` 允许 `RELEASED → ON_CONVEYOR/READY_FOR_PICK`，
  支持固定交接台和 C→E 传送带入口的重新确认；不代表隐式故障恢复。
- `stage1/conveyor_controller.py` 增加方向感知 `BeltGeometry.advance`，
  `transport_step` 使用它；X 向线结果与旧实现等价，Y 向线可正确补偿。
- `stage2/collisions.py` 的 `discover_workcell`/`CollisionRuntime.build`
  接受 `conveyor_aliases`，默认仍为阶段一单带。
- `stage1/scene_artifact.py` 阶段列表加入 `stage4`，按阶段选择必需 alias。
- `stage1/lifecycle.py` 支持 `build --phase stage4 --parent`、阶段四
  adopt/contract/runner，以及 `save --phase stage4`；父摘要从 stage3
  `inheritance` 读取 stage2 契约/配置摘要。
- `reference/factory_mcp_server.py` 按工件 `source_phase` 路由 stage4；
  `status` 报告协调器任务/资源/冻结状态。

## 4. 布局

生产布局 `L4_COLUMN`：

| 对象 | A | B | C | D | E |
|---|---|---|---|---|---|
| UR5 基座 | (−0.20, +0.55, 0.30) | (−0.20, −0.55, 0.30) | (−0.20, 1.75, 0.45) | (−0.20, −2.45, 0.45) | (−0.20, 2.45, 0.30) |
| Island | (−0.20, +0.55) | (−0.20, −0.55) | (−0.20, 1.75) | (−0.20, −2.45) | (−0.20, 2.45) |
| 置物台/标称输出 | (−0.20, 1.20) | (−0.20, −1.20) | C→E 入口 | S2→D 停止区 | E 缓冲 C |
| AGV dock | (0.40, +0.70) | (0.40, −0.70) | — | (0.40, −2.45) | (0.40, +2.45) |

物流线（全部为内置 `generic conveyor (belt).ttm`，设计长度 1.216 m）：

| 线 | 方向 | 入口 | 停止区 |
|---|---|---|---|
| `line_s1a`（既有 S1→A） | +X | (−1.017, 0.000) | (−0.200, 0.000) |
| `line_s1b`（S1→B） | +Y | (−0.750, −1.467) | (−0.750, −0.650) |
| `line_s2d`（S2→D） | +X | (−1.507, −2.450) | (−0.690, −2.450) |
| `line_ce`（C→E） | +Y | (−0.850, 1.553) | (−0.850, 2.370) |

- A→C 固定交接复用 A 置物台 `(-0.20, 1.20)`，新增 `Sensor_Handoff_AC`；
  C 岛/基座抬高到 0.45 m，使 C 臂 link3 越过 A 置物台台面（0.30 m）；
  D 岛/基座抬高到 0.45 m（岛体 0.55 m），S1→B 与 S2→D 支线向
  各自机械臂靠近，使 H_transfer=0.80 的垂直抬升处于 UR5 可达范围；
  A 放置后撤离到交接台南侧派生 park 位姿 `(0.05, 0.90, 0.70)`，让出
  交接台包络，避免 A 腕部与 C 的接近/下探相撞。
- E 岛两个输入缓冲位于 E 岛台面 `(-0.42, 2.60)` / `(0.02, 2.60)`，
  成品输出 `E_Output` 位于 `(-0.20, 2.72)`；
- AGV 初始 dock A，航向 −π/2；四个 dock 共线 x=0.40；
- 静态筛选 145 项通过；五台臂的 pick/place/dock/交接/缓冲目标均通过
  `probe_stage4_layout.py --sim --ik` 的 simIK 求解与关节限位检查。
- 高度基准不一致：A/B/E 岛面与基座为 0.30 m（A/B 沿用阶段三，
  E 的缓冲/载台较低），C/D 为 0.45 m（C 避让 A 交接台与 link3，D 与
  传送带面齐平并保证 H_transfer 可达）。这是逐点几何修正，不是统一的
  工位高度设计；重排时应先确定统一 datum。

> **布局审查边界**：`L4_COLUMN` 是紧凑的“列式/穿梭”基线，不是
> 06-16 §3.2 推荐的“北侧固定物流 + 外围 AGV 环线”。五台臂同在
> x=−0.20 一线、四个 dock 共线 x=0.40、四条线均沿 X/Y 轴，AGV 只有
> 单段直线；没有外围环路、会车/等待点和多段路线。它是在保留阶段三
> A/B 锚点、复用单段路线控制器并逐点满足 H_transfer 可达性的条件下
> 收敛出的布局，满足当前五个验收场景，但不满足 06-16 §3.2
> “不要把五岛排成简单长队列”和 §3.3 “先画 From-To/AGV 路径再定坐标”
> 的要求。若要达到文档描述的场景，应作为新工件（例如 `L5_LOOP`）和
> 多段路线版本重排，不能只改坐标。

## 5. 物料、配对与资源

物料类型与工序转换：

```text
S1→A: RAW_A ──A──► SEMI_AC ──C──► SEMI_CE ─┐
S1→B: RAW_B ──B──► SEMI_AB ──AGV B→A──► A ──► SEMI_AC
S2→D: RAW_D ──D──► PART_DE ──AGV D→E──► E ──┤
E: SEMI_CE + PART_DE ──原子配对──► FINAL (父 UID 1 + UID 3)
```

- 工序转换只在放置证据（UID、类型、位姿、物理属性）确认后提交；
- 区域容量：每条线/交接台/缓冲/AGV 载台各 1，成品输出 4；
- E 配对键为 `(semi_uid, part_uid)`，重复/缺件/错型拒绝，不凭位置配对；
- 阶段四只做可追溯的逻辑装配，不宣称几何切削/真实物理装配。

资源固定顺序：

```text
line_s1a → line_s1b → line_s2d → line_ce → handoff_ac →
buffer_e_c → buffer_e_d → dock_A → dock_B → dock_D → dock_E →
agv_lane → zone_ab → zone_ac → zone_ce → arm_A..arm_E
```

- 任务一次性申请完整资源集；任一忙只记录 WAIT，不持有部分资源；
- AGV 航线持有 `agv_lane` 与全部 dock，车辆不穿过正在装卸的 dock；
- 相邻臂互锁区 AB/AC/CE 串行化可能相交的臂运动；D 与任何臂不相邻。

## 6. 场景与运行

五种有限批次场景：

| 场景 | 批次 | 检查 |
|---|---|---|
| `full_flow` | RAW_A=1, RAW_B=1, RAW_D=1 | 两条 AGV 路线、E 配对、缺件等待 |
| `no_pair` | RAW_A=1, RAW_D=1 | 合法配对恰好 1 个 FINAL |
| `agv_priority` | RAW_A=1, RAW_B=1, RAW_D=1 | 空闲派车优先级、运行任务不抢占 |
| `agv_blocked` | RAW_A=1, RAW_B=1, RAW_D=1 | AGV 阻塞/清障续行 |
| `conveyor_fault` | RAW_A=1, RAW_D=1 | C→E 停带、受影响任务 BLOCKED、无 FINAL |

运行命令：

```bash
python src/run_stage4.py --scenario full_flow --time 1500
python src/run_stage4.py --scenario no_pair --time 1200
python src/run_stage4.py --scenario agv_priority --time 1500
python src/run_stage4.py --scenario agv_blocked --time 1500
python src/run_stage4.py --scenario conveyor_fault --time 1200
```

## 7. 验收结果

全部五个阶段四场景与阶段三回归均通过（`all_ok = true`）。运行使用
`stage4/L4_COLUMN/v008_20261028_225634`，每次 `start` 前重新 `load
current --phase stage4` 并执行启动点全量只读校验（452 项通过）。

| 场景 | 运行证据 | 仿真时长 | 任务 | 成品 | 关键判据 |
|---|---|---|---|---|---|
| `full_flow` | `phase4_run_full_flow_20261028_234140.json` | 361.10 s | 13/13 DONE | 1 个 FINAL，父件 [1,3] | 4 条路线 DONE，1 个未配对 SEMI_CE，无锁存，资源全释放 |
| `no_pair` | `phase4_run_no_pair_20261029_000629.json` | 165.30 s | 7/7 DONE | 1 个 FINAL，父件 [1,2] | 无未配对半成品，2 条路线 DONE |
| `agv_priority` | `phase4_run_agv_priority_20261029_001906.json` | 362.40 s | 13/13 DONE | 1 个 FINAL，父件 [1,3] | 2 次派车决策均满足优先级，无抢占 |
| `agv_blocked` | `phase4_run_agv_blocked_20261029_010133.json` | 405.45 s | 13/13 DONE | 1 个 FINAL，父件 [1,3] | 两次阻塞/清障续行（B→A、D→E） |
| `conveyor_fault` | `phase4_run_conveyor_fault_20261029_012444.json` | 89.80 s | 4 DONE / 1 FAILED / 1 BLOCKED | 0 | C→E 停带、任务 BLOCKED、现场保留、整线冻结 |

- **full_flow**：A→B 空载 12.30 s、B→A 满载 11.85 s、A→D 空载
  26.85 s、D→E 满载 41.10 s；满载停靠误差 0.019636 / 0.019642 m，
  航向误差 3.4e-4 / 5.7e-4 rad，载荷滑移 0.0；FINAL 在 t=321.00，
  吞吐 9.99 件/h，AGV 忙碌 92.30 s（利用率 0.2562）。E 岛由 uid1
  （SEMI_CE）与 uid3（PART_DE）配对生成 uid4；uid2 的 SEMI_CE 留在
  `buffer_e_c`，与有限批次预期一致。
- **no_pair**：A→D 空载 26.90 s、D→E 满载 41.00 s，停靠误差
  0.019673 / 0.019672 m，滑移 0.0；uid1（SEMI_CE）与 uid2
  （PART_DE）配对生成 FINAL，无未配对半成品，FINAL 在 t=165.25。
- **agv_priority**：两次派车决策分别选择 `transport_ba_uid2`
  （优先级 0）与 `transport_de_uid3`（优先级 1），无待选任务被跳过，
  `no_preemption = true`；B→A 满载 11.95 s、D→E 满载 41.05 s，
  停靠误差 0.019473 / 0.019685 m，滑移 0.0。
- **agv_blocked**：AGV 在 B→A 的 t=113.15 进入 BLOCKED（阻塞窗口
  3 s、位移 0.0016 m），t=123.20 清障并确认资源所有权后续行，路线
  29.60 s 完成；D→E 在 t=282.40 再次 BLOCKED，t=292.40 清障续行，
  66.30 s 完成。两次满载停靠误差 0.019446 / 0.019734 m，滑移 0.0；
  FINAL 在 t=365.55。
- **conveyor_fault**：`c_handoff_uid1` 完成后于 t=88.80 停 `line_ce`；
  `e_conveyor_uid1` 进入 BLOCKED，SEMI_CE 保留在 `line_ce`，运行中的
  `transport_de_uid2` 按冻结语义置 FAILED，整线 `frozen = true`，
  无 FINAL、无安全锁存、资源全部释放。该结果是场景预期，不表示
  自动恢复能力。
- **阶段三回归**：`phase3_run_closed_loop_20261029_013344.json` 在
  `stage3/L2_MIRROR/v006_20261028_180722` 上 A→B 闭环三任务 DONE，
  停靠误差 0.019924 m，滑移 0.0，无锁存；阶段一/二/三默认行为未受
  阶段四扩展影响。
- **布局/IK 测量**：`stage4_layout_20261029_v008.json` 静态检查 145
  项通过，场景契约 185 项通过；五台臂对本岛 pick/place/dock/buffer
  目标共 13 个 IK 解全部成功且在关节限位内；四条线
  `config_targetVel = 0`、`config_accel = 2.0`；AGV 初始位于 dock A。

> 说明：`full_flow` 运行于 2026-10-28 23:41–00:03，早于
> `JOIN_COMMIT` 指标窗口修正；`full_flow`、`no_pair`、`agv_priority`
> 早于为 `agv_blocked` 引入的阶段四 150 s 路线超时显式传递。三者的
> 路线均短于 45 s，指标由 `analyze_stage4_run.py` 从事件重算，结论
> 不受影响。

## 8. 未实现 / 限制

- **阶段 2.1 统一故障恢复**：未实现；安全锁存/航线失败/超时只冻结现场，
  不自动复位/续作。
- **真实物理装配**：E 岛只做逻辑消费与 FINAL 生成，不做几何装配/治具。
- **OMPL**：未启用；局部规划失败保持失败。
- **多 AGV/多车会车**：单 AGV、单向直线路线；未实现双向会车/等待点。
- **视觉识别**：未使用 Vision Sensor；物料类型由生成参数与谱系确定。
- **碰撞证明**：阶段二仍是离散采样（2° 关节、25 mm 关键连杆位移），
  不是连续碰撞证明；布局静态筛选也不替代碰撞查询。
- **共享交接台几何前提**：C 岛/基座抬高到 0.45 m，且 A 放置后撤离到
  交接台南侧 park 位姿 `(0.05, 0.90, 0.70)`。修改 C 岛高度、A 基座或
  交接台位置后，必须重跑 C 臂接近/下探的碰撞检查。
- **B/D 可达性**：S1→B 线中心 `(-0.75,-1.07)`、S2→D 线中心
  `(-1.11,-2.45)`；D 岛 0.55×0.55×0.45 m、基座 `(-0.20,-2.45,0.45)`。
  这些值让取料点到基座在 H_transfer=0.80 时约 0.65–0.77 m；改变
  线/岛/基座后必须重跑 pick/lift/transfer 的 IK 与碰撞检查。
- **LIFTING 直连、D 关节回退与 E q0 解禁回退**：阶段四 LIFTING 先做同一
  lift 位姿的垂直笛卡尔直连（允许起点载荷-承载面接触），避免阶段二中转
  构型的起点载荷检查误报。D 的竖直路径关节插值偏差 6–8 mm > 5 mm 时，
  回退到 `cartesian=False` 的关节目标；E 臂做完 C→E 任务后若停在镜像
  肩部构型，对 dock E 的 AGV 载台抬升可能以
  `JOINT_LOCK_UNAVAILABLE` 失败，此时用同一 lift 位姿重规划一次
  `lock_joint=None` 的垂直笛卡尔直连。两种回退都保留逐层 IK/关节限位/
  碰撞/分支连续性与终点 TCP 检查，不放宽全局容差，也不做未检查的
  关节直赋。E 的 q0 解禁回退在 2026-10-29 的五个验收运行中均未被触发
  （E 臂恰好落在非镜像构型），其真实运行路径仍需在布局或任务顺序
  改变后复核。
- **AGV 路线超时预算**：阶段四 `AGV_ROUTE_TIMEOUT = 150 s`，由
  `AGVRouteController4` 显式传给阶段三路线控制器；D→E 满载单程约
  41 s，加入一次 10 s 阻塞后仍需约 52 s，不能沿用阶段三的 45 s 默认值。
- **E 缓冲近基座**：两个缓冲距 E 臂基座约 0.27 m、TCP 高于台面 0.10 m；
  若后续调整岛体/缓冲，必须重新执行 IK/碰撞与 E 任务回归。
- **IK 探针恢复**：临时探针调用 `Ur5IkController.solve` 后必须恢复 IK
  target dummy；否则 start 前 `world_pos:IK_Target_0x` 校验拒绝场景
  （实测 IK_Target_01/03 被留在 park/线入口位姿）。

## 9. 复现命令

```bash
# 1. 从 stage3 父工件派生 stage4 场景并保存
python src/run_scene_lifecycle.py load stage3/L2_MIRROR/v006_20261028_180722
python src/run_scene_lifecycle.py build --phase stage4 \
  --parent stage3/L2_MIRROR/v006_20261028_180722
python src/run_scene_lifecycle.py save --phase stage4 \
  --parent stage3/L2_MIRROR/v006_20261028_180722 --note "阶段四派生"

# 2. 布局/五台臂 IK 只读测量
python docs/measurement/probe_stage4_layout.py
python docs/measurement/probe_stage4_layout.py --sim --ik \
  --out docs/measurement/stage4_layout_20261029_v008.json

# 3. 验收场景
python src/run_stage4.py --scenario full_flow --time 1500
...
```

## 10. 代码版本 / 工作区

- 分支：`main` 工作区；本报告、阶段四代码/测试/工件/文档随同一提交进入
  版本库；具体 commit 以 `git log` 为准。
- 完整测试套件（2026-10-29）：`pytest -q` → **492 passed, 3 skipped**。
- 未改写或回退用户已有的未提交修改；未覆盖 stage1/2/3 父工件。
- 阶段一基线 `stage1/L1_S1/v002_20261020_234403`、阶段二
  `stage2/L1_S1/v014_20261028_175742`、阶段三
  `stage3/L2_MIRROR/v006_20261028_180722` 的 scene/manifest 摘要未变化。

## 11. 证据文件

`docs/implementation/evidence/` 中的长期证据副本：

- `phase4_analysis_full_flow_20261028_234140.json`
- `phase4_analysis_no_pair_20261029_000629.json`
- `phase4_analysis_agv_priority_20261029_001906.json`
- `phase4_analysis_agv_blocked_20261029_010133.json`
- `phase4_analysis_conveyor_fault_20261029_012444.json`
  — 由 `docs/measurement/analyze_stage4_run.py` 从对应
  `phase4_run_*.json` 提取的验收、任务、路线、物料与指标摘要；
- `scene_validation_stage4_L4_COLUMN_v008_20261028_225634_20261029_012444.json`
  — v008 工件启动点全量只读校验，452 项通过、0 差异；
- `stage4_layout_20261029_v008.json` — `L4_COLUMN` 静态检查、五臂
  IK 与场景只读测量；
- `phase3_run_closed_loop_20261029_013344.json` — 阶段三 v006
  闭环回归原始报告。

原始 `phase4_run_*.json`（每份 6–18 MB）与场景快照保留在
`docs/implementation/runs/`，该目录按仓库约定不纳入版本控制；其
文件名与分析摘要中的 `file` 字段一一对应。

阶段 4.1（`L5_UFLOW`）证据：

- `stage4_layout_L5_UFLOW_preview.svg` — 从最终布局配置生成的俯视预览；
- `stage4_layout_20261029_L5_UFLOW.json` — 274 项静态筛选、187 项场景
  契约与五臂 13 个多解种子 IK 只读测量；
- `scene_validation_stage4_L5_UFLOW_v004_20261029_133249_20261029_133359.json`
  — v004 保存/重载 461 项只读校验通过、0 差异；
- `phase4_41_analysis_full_flow_20261029_173632.json` — 修复后
  `full_flow`：343.90 s、13/13 DONE、1 个 FINAL；
- `phase4_41_analysis_staggered_batch_20261029_170923.json` — 修复后
  `staggered_batch`：549.05 s、20/20 DONE、2 个 FINAL、最大并发 3；
- `phase4_41_analysis_conveyor_fault_20261029_180818.json` — 停机回归：
  `line_ce` 停带后任务 BLOCKED、整线冻结、无 FINAL；
- `phase4_41_analysis_agv_blocked_20261029_175227.json` — 补充阻塞场景：
  B→A 续行滑移 1.36 cm，D→E 续行后滑移 6.15 cm 触发 `LOAD_SLIP` 冻结；
- 失败与修正证据：
  `phase4_41_analysis_full_flow_v001_failed_20261029_131209.json`、
  `phase4_41_analysis_staggered_batch_v004*_failed_20261029_*.json`。


## 12. 阶段 4.1：`L5_UFLOW` 布局与设备联动优化

> 对应 [06-17 方案](../wiki/06-工业案例/06-17-柔性工业车间阶段4.1场景布局与联动优化方案.md)。
> 本节只记录重排结果、必要取舍和验收结论；`L4_COLUMN/v008` 的
> 阶段四基线数据仍见 §1–11，不复制为新对照材料。

### 12.1 工件与布局

- 当前阶段 4.1 工件：`stage4/L5_UFLOW/v004_20261029_133249`
  - 父工件：`stage3/L2_MIRROR/v008_20261029_112305`（protected）；
  - 契约：`extra.stage4.contract_version = stage4_contract_20261029_v2`；
  - 保存/重载只读校验 **461 项通过、0 差异**；
  - scene sha256：`bf7e4df6681abf8027e3f04e3bb214fc5a1f707d1be8922a733fce424a2a352c`；
  - config digest：`45e46fa09a6d4f5557df0a23d3eb3568a208156ac56b6460df5527d2ace50a6c`；
  - layout digest：`ed7759fddfc55eb546fd29363cea003898236cac6a85b32f2cf9717b72afddef`。
- 重排前基线：`stage4/L4_COLUMN/v008_20261028_225634` 保留在磁盘，
  不再是 current；在 v2 契约下会因 config/layout digest 不符而拒绝
  启动，属预期保护。

| 对象 | A | B | C | D | E |
|---|---|---|---|---|---|
| UR5 基座 | (−0.20, +0.55, 0.30) | (−0.20, −0.55, 0.30) | (+0.35, +1.35, 0.30) | (+2.34, −1.35, 0.30) | (+2.34, +1.35, 0.30) |
| Island | (−0.20, +0.55) | (−0.20, −0.55) | (+0.35, +1.35) | (+2.34, −1.35) | (+2.34, +1.35) |
| 置物台/标称输出 | (−0.20, +1.20) | (−0.20, −1.20) | C→E 入口 | S2→D 停止区 | E 缓冲 C |
| AGV dock | (+0.40, +0.70) | (+0.40, −0.70) | — | (+2.90, −1.35) | (+2.90, +1.35) |

物流线（全部为内置 `generic conveyor (belt).ttm`，设计长度 1.216 m）：

| 线 | 方向 | 入口 | 停止区 |
|---|---|---|---|
| `line_s1a`（既有 S1→A） | +X | (−1.017, 0.000) | (−0.200, 0.000) |
| `line_s1b`（S1→B） | +Y | (−0.750, −1.467) | (−0.750, −0.650) |
| `line_s2d`（S2→D） | +X | (+1.013, −1.350) | (+1.830, −1.350) |
| `line_ce`（C→E） | +X | (+1.013, +1.350) | (+1.830, +1.350) |

高度 datum（`extra.stage4.layout.height_datum`）：

- `Z_base = 0.30 m`：A–E 全部 UR5 安装基面（统一，无高台例外）；
- `Z_work`：A/B 置物台 0.30、E 岛缓冲/输出 0.30、C/D 由传送带承载 0.45；
- `Z_belt = 0.45 m`；`H_transfer = 0.65 m`（06-15）；
- 物料中心 = 承载面 + 0.05 m；E 成品输出在岛外东侧独立台 `E_Output_Table (3.50,1.35)`，台面 0.30 m。

AGV 路网（`extra.stage4.layout.agv_network`）：

```text
A (0.40,0.70) ─┐  west
B (0.40,-0.70) ─┴────────────► J_SW (0.40,-2.10)
                                   │ south
                                   ▼
D (2.90,-1.35) ◄──── east ─── J_SE (2.90,-2.10)
E (2.90, 1.35) ◄──── east
```

- 载货 B→A（x=0.40）与 D→E（x=2.90）为单段直道，车头 −Y；
- 空载调机走最短路径，在 `J_SW`/`J_SE` 停车原地转向 90°；
- 每条路线持有实际经过的 `agv_lane_*` 与全部 dock；
- 自动预览：`docs/implementation/evidence/stage4_layout_L5_UFLOW_preview.svg`；
- 静态筛选 274 项通过；场景契约 187 项通过；五台臂 13 个
  pick/place/dock/buffer 目标用与规划器一致的多解种子枚举，全部在
  关节限位内求解成功。

### 12.2 与 `L4_COLUMN` 的差异

- A/B 基座、置物台、dock、S1 输入带与 AGV 初始位姿保持不变；
- C/D/E 从 x=−0.20 的列式排布移到东侧，固定物流改沿 +X；
- 四个 dock 不再共线；AGV 从单段直线改为西/南/东三段直道 + 掉头；
- C/D 基座由 0.45 m 降为统一 0.30 m；
- 资源表移除 `zone_ce`（C–E 距离 1.99 m，不再相邻），增加三条
  直道资源；
- E 两个缓冲改为基座东西两侧的径向布置。

### 12.3 受影响的路径与修正

- **A→C 交接**：A 放置后仍撤离到交接台西南侧 park 位姿
  `(−0.35, 0.90, 0.70)`；C 从东侧接近，A/C 通过 `zone_ac` 串行。
- **A→交接水平转运**：直线段仍会穿过 A 基座上方，按 06-15 回退到
  同高度 `detour +0.30 m`，不是 4.1 新缺陷。
- **C→E**：C 从交接台到带入口的水平段跨过 C 基座，同样使用 06-15
  的 `detour +0.30 m`；C 放到带入口前必须停带确认。
- **E AGV 卸货**：初版缓冲 `(2.50,1.13)` 使 dock→缓冲的水平段发生
  横向折叠，连续 IK 偏差 23.8 mm 被 `GOAL_CONFIG_INVALID` 拒绝；
  改为径向 `(2.58,1.35)` 后通过。该试错记录保留在此，避免把“IK
  可行”误当作“连续路径可行”。
- **E 成品输出**：v001 把 `E_Output` 放在 E 岛面 `(2.40,1.60)`，
  `full_flow` 最后一条 `e_conveyor_uid2` 接近 buffer 时 link3 与已生成
  的 FINAL `Part_0004` 发生 `robot_vs_parts` 碰撞并冻结整线。输出改为
  岛外独立台 `E_Output_Table (3.50,1.35)`（距 E 基座 1.16 m、
  东直道扫掠矩形 0.21 m）后，FINAL 不再进入任何机械臂包络。该失败是
  本轮唯一导致 full_flow 冻结的几何问题；v001 及两次失败保存草稿
  v002/v003 已删除，工作区只保留 v004。
- **A 臂 AGV→交接台转运**：`staggered_batch` 首轮中 A 臂由不同
  前置任务进入 AGV 取料后落入不同腕部分支，直连横向偏差 8.1 mm，
  `+0.30` 绕行 IK 无解、`-0.30` 绕行 5.0 mm 仍被拒绝。阶段四增加
  `horizontal_detour_park` 同高度 via-park 候选（仍为两段笛卡尔
  水平线，全部 IK/碰撞/分支/姿态复核）；阶段二/三默认候选不变。
- **AGV 转向**：原地 90° 转向实测产生约 4 cm 平移；阶段四直道
  控制器加入横向纠偏（前视 0.50 m，最大航向偏置 0.20 rad），距目标
  0.10 m 内恢复原航向，停靠/航向容差不变。

### 12.4 验收结果

阶段 4.1 的三组验收边界在 v004 工件上执行；命令见
[项目使用指南](../wiki/98-ProjectUsage/USAGE.md)，原始运行 JSON 在
`docs/implementation/runs/`（不提交），分析摘要复制到
`docs/implementation/evidence/phase4_41_analysis_*.json`。

| 场景 | 运行证据 | 仿真时间 | 任务 | FINAL | 关键判据 |
|---|---|---|---|---|---|
| `full_flow` | `phase4_41_analysis_full_flow_20261029_173632.json` | 343.90 s | 13/13 DONE | 1（父件 1+3） | B→A 11.90 s、D→E 23.00 s、A→D 空载 61.40 s（3 直道 + 2 转向）；停靠 ≤0.0199 m、滑移 ≤0.0090 m；1 个未配对 SEMI_CE；无锁存 |
| `staggered_batch` | `phase4_41_analysis_staggered_batch_20261029_170923.json` | 549.05 s | 20/20 DONE | 2（父件 1+3、4+5） | 最大并发 3、`interleaved=true`；B→A 11.90 s、D→E 23.05/22.85 s、E→D 空载 23.00 s；停靠 ≤0.0199 m、滑移 ≤0.0090 m；1 个未配对 SEMI_CE（uid2）；无锁存、资源全部释放 |
| `conveyor_fault` | `phase4_41_analysis_conveyor_fault_20261029_180818.json` | 95.40 s | 3 DONE / 1 BLOCKED / 1 冻结失败 | 0（预期） | 在 `c_handoff_uid1` 后停 `line_ce`：`e_conveyor_uid1` BLOCKED、整线冻结、无 FINAL；uid1 留在 `line_ce`、uid2 仍在吸盘；资源释放 |

- **布局与可达**：静态筛选 274 项通过；场景契约 187 项通过；五台臂
  13 个目标在限位内全部有解（`--ik` 使用与规划器一致的多解种子）；
  AGV 载货路线为单段直道，空载调机 5 航段/2 次原地转向。
- **流程联动**：`staggered_batch` 由 A/C/E 固定流、B→A 返工流和
  D→E 装配流交错组成；20 个任务全部 DONE，E 岛按 UID/类型配对生成
  两个 FINAL 并保留父 UID，未配对的 uid2 留在 `buffer_e_c`。
- **基本停机**：`conveyor_fault` 停 `line_ce` 后，`e_conveyor_uid1` 进入
  BLOCKED、整线冻结、无 FINAL，物料与现场保留、资源释放（验收通过）。
  另运行 `agv_blocked` 作为补充：B→A 段阻塞 10 s 后清障续行（滑移
  1.36 cm）；D→E 长直道阻塞续行后载荷滑移 6.15 cm > 2 cm，按
  `LOAD_SLIP` 冻结——停机/保留/不误报 DONE 正确，但该场景要求的
  “续行完成”未达到，作为后续控制优化项记录；第三组验收以
  `conveyor_fault` 为准。

- **失败证据**：v001 的 `full_flow` 在 E 输出位于岛面时以
  `PATH_COLLISION` 失败（`phase4_41_analysis_full_flow_v001_failed_20261029_131209.json`）；
  `staggered_batch` 在修复前有五次失败运行（首轮为 via-park 候选引入前
  的 8.1 mm 分支失败），分别记录执行期预算、pre_pick 分支、AGV 放置点
  与释放目标同源问题
  （`phase4_41_analysis_staggered_batch_v004*_failed_20261029_*.json`）。

### 12.5 交错批次暴露并修复的实现缺陷

交错批次除验证布局与联动外，还暴露了三处与布局无关、但会阻断多岛流程的实现缺陷。三处修复都保留既有检查边界：没有放宽容差、没有跳过碰撞/分支/水平姿态复核，也没有改变布局、工件契约或阶段二/三的默认路径。

1. **执行期继承规划墙钟预算**（`src/stage2/path_planning.py`、`src/stage2/unit_controller.py`）：`LocalPlanner.plan` 把本次 `PlanBudget` 挂到 `ConfigurationChecker` 后没有恢复，执行器每步 `check_segment` 继续消耗同一预算。三路并发把仿真推进压到约 0.16 仿真秒/墙钟秒，C 的 23 s 转运在执行中触发 `墙钟 150.33s > 150.0s`，以未捕获异常中止场景（`phase4_41_analysis_staggered_batch_v004b_failed_20261029_150621.json`）。现在 `_run_motion` 在计划通过、创建执行器前调用 `checker.set_budget(planner.budget)`；规划墙钟只约束规划与验证，执行期仍逐步做碰撞/偏差/超时复核。

2. **A 臂 AGV 取料的 `pre_pick` 分支选择**（`Goal.forced_q`、`Stage4UnitController._forced_pre_pick`）：默认 `pre_pick` 取离当前构型最近的 q[0]≈−1.5 肩部解，抬升后落入肘上分支，在 `H_transfer` 上无法把物料送到交接台——直连横向偏差 7.8 mm，固定高度绕行点（28 点与加宽西侧的 36 点两轮网格）、beam 整链搜索、0.5 cm 步长与全种子搜索均无解（`phase4_41_analysis_staggered_batch_v004c_failed_20261029_151907.json`）。同一 `pre_pick` 位姿的 q[0]≈+2.0 肩部解抬升后 q[3]≈0，既有 `detour +0.30` 即可通过。现在对少量 `pre_pick` IK 解各做一次“下探→抬升→水平转运”影子规划，只通过 `Goal.forced_q` 强制完整链已整体验证的构型；每段仍用同一 IK/碰撞/分支/水平姿态复核。生效记录：`APPROACH branch selected q0=-4.258 q3=0.356`（v004d，`a_agv_uid2` DONE；`full_flow` 为 q0=1.988/q3=0.339）。

3. **AGV 载台放置点/释放目标使用固定标称坐标**（`Stage4UnitController._place_pose`、`_material_pose_for_target`、`agv_place_pose`、`agv_material_position`，`Stage4Coordinator._expected_material_pose`）：AGV 停靠偏离标称时，固定标称放置点会让载荷降入托盘时蹭到 `AGV_TrayWall_Front`（v004d，t=459，停靠偏差 3.3 cm）；改为实测托盘中心后，释放校验与协调器 `PLACE_ERROR` 仍对比标称位姿，B 件报 `RELEASE_CORRECTION_EXCEEDED 0.0478 m`（`phase4_41_analysis_staggered_batch_v004e_failed_20261029_165914.json`）。现在放置 TCP、释放目标物料位姿和完成校验统一由 `agv_material_position(tray_pose)` 生成，工具姿态与竖向偏移不变；该修正只影响 `sink=agv` 任务，AGV 取料仍用实测物料位姿。
