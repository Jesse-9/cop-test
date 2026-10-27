# 阶段二实施报告：物理约束与运动规划

日期：2026-10-21
分支：`codex/phase2-physics-motion`（工作区提交前状态见 §10）
上游计划：[06-09-柔性工业车间阶段二-物理约束与运动规划](../wiki/06-工业案例/06-09-柔性工业车间阶段二-物理约束与运动规划.md)
父工件：`stage1/L1_S1/v002_20261020_234403`
阶段二工件：`stage2/L1_S1/v009_20261021_190527`

> 2026-10-21 阶段 2.01 完成后派生工件 `stage2/L1_S1/v012_20261021_210539`；
> 2026-10-25 阶段 2.02 又派生 `stage2/L1_S1/v013_20261024_232558`
> （`stage2_contract_20261024_v3`）。本文记录阶段二原始验收；动作轨迹优化、
> 锁轴/分支不变量见 [PHASE2_01_REPORT.md](PHASE2_01_REPORT.md)，AGV 转运
> 路径能力见 [PHASE2_02_REPORT.md](PHASE2_02_REPORT.md)。

## 1. 结论

阶段二代码、契约、工件与最小验收已完成：

- 取放路径使用独立影子 UR5 做离散碰撞检查，真实臂执行由 `MotionPlan`
  逐段驱动，执行器每步复核下一构型；
- 吸附前校验物料局部顶面的带符号间隙、面内偏移、杯口覆盖与工具法向；
- 释放保持世界位姿解绑，修正量超限拒绝释放；
- 停带改为速度/独立 pad 位移双判据，未知读数锁存；
- 运行期受控障碍可触发锁存、取消剩余轨迹、停带/停车并确认真实停止；
- 无 OMPL、无自动恢复；统一故障恢复归项目最终阶段（06-05 §6），阶段二仅负责停止、锁存和明确失败。

关键验收结果：

| 检查 | 结果 |
|---|---|
| 相关测试 | 209 项非 MCP + 47 项 MCP = **256 项通过** |
| 正常 station | DONE，物料位置误差 **0.00062 m**，安全未锁存 |
| 正常 agv | DONE，载荷在载台，AGV 轮速行驶 0.5022 m，跟随误差 **0.0087 m** |
| both | 两任务 DONE（UID 1/2），无串任务 |
| 运行期受控障碍 | 锁存 `SAFETY_LATCH: COLLISION`，对象对 `link3_visible <-> Fault_Runtime_Obstacle`，停止确认 `speed=4e-5 m/s`，未恢复、未 DONE |
| 路径拒绝 | 基线 87 采样 OK；中段 `PATH_COLLISION`（`Body <-> Fault_Runtime_Obstacle`），两端安全；移除障碍后恢复 OK |
| 工件复用 | v009 加载/只读校验 **144 项通过**，父差异 `[]`，再次运行 station 通过 |
| MCP 路由 | `start_simulation` 自动 backend=stage2，station DONE（0.00062 m），报告 `phase2_mcp_station_none_20261021_191502.json` |
| 阶段一基线 | v002 scene sha `6757d0…`、manifest digest `961c6f…` 未变化 |

## 2. 工件与控制器

- 父工件：`stage1/L1_S1/v002_20261020_234403`（protected，未覆盖）。
- 阶段二工件：`stage2/L1_S1/v009_20261021_190527`
  - scene sha256 `43acbefa…`，manifest sha256 `e961b29c…`；
  - `extra.stage2.contract_version = stage2_contract_20261021`；
  - 控制器 `stage2.unit_controller.Stage2UnitController`；
  - runner `stage2.run.run_loaded_scene_stage2`；
  - `parent_artifact.differences = []`（对象/配置/布局/模型/脚本摘要一致；
    几何/工具/物理章节只保留稳定字段，不含会话句柄）。
- 阶段二配置摘要 digest `4c08edd4…` 与阶段一配置摘要一致：阶段一基线参数
  （布局、模型、带速、步长、TCP 安装、质量/摩擦）没有被复制或改写。
- 阶段二 v009 由 CLI 保存为 `protected: true`（文件只读）；仍可从父工件派生新版本。stage1 基线保持只读。

## 3. 代码模块

| 模块 | 作用 |
|---|---|
| `src/stage2/config.py` | 阶段二全部新参数；阶段一参数仍来自 `stage1.config` |
| `src/stage2/contact.py` | 物料局部包围盒、顶面 frame、带符号间隙/横向/杯口/法向判据（纯几何） |
| `src/stage2/collisions.py` | 对象角色、碰撞集合、4.10 查询适配、显式非相邻自碰撞对、查询语义自检 |
| `src/stage2/shadow.py` | 同基座影子 UR5/pad/TCP/载荷副本；FK 一致性；会话资源清理 |
| `src/stage2/path_planning.py` | 构型/段检查、自适应细分、预算、多解 IK、DFS 局部规划、最终复核 |
| `src/stage2/trajectory.py` | Cartesian 位姿序列、关节流式执行器、保持/取消 |
| `src/stage2/safety.py` | `SafetyObservation` / `SafetyLatch` / `request_stop` |
| `src/stage2/conveyor_safety.py` | 停带监视器、受限补偿、物料稳定跟踪 |
| `src/stage2/suction.py` | 外表面接触吸附、保持世界位姿释放 |
| `src/stage2/unit_controller.py` | 继承阶段一状态机，接入规划/执行/联锁/停带 |
| `src/stage2/runtime.py` | 影子臂/集合/检查器/规划器装配与销毁 |
| `src/stage2/scene_contract.py` | `extra.stage2` 契约、稳定 manifest、只读校验 |
| `src/stage2/run.py` | 阶段二 runner、受控障碍注入、报告 |
| `src/run_stage2.py` | CLI 薄包装 |

阶段一兼容扩展（默认行为不变）：

- `stage1/ur5_ik_controller.py` 增加 `solve_candidates()` 与
  `apply_joints(motion=...)`；
- `stage1/lifecycle.py` 增加阶段契约 provider/validator 扩展点和按
  `source_phase` 的 runner 路由；stage2 manifest 保存前剥离会话句柄；
- `stage1/scene_artifact.py` 修复 set/frozenset 序列化顺序不确定导致的
  config digest 跨进程漂移；
- `stage1/run.py` 拒绝用 stage1 runner 运行 stage2 工件；
- `reference/factory_mcp_server.py` 在 `start_simulation` 中按工件
  `source_phase` 路由到阶段二线程，并复用同一生命周期/运行锁。

## 4. 4.10 实测发现（影响实现）

1. **碰撞/距离返回形态**：`sim.checkCollision(e1,e2) -> (0|1,[h1,h2])`；
   `sim.checkDistance(e1,e2,threshold) -> (result,[7],[pair])`。
   `result=0` 且 data 全零只表示“阈值内无近邻”，不能解释为距离 0；
   `threshold=0` 才返回精确距离。
2. **collection self-collision indicator 无效**：实测 1/1/2、1/2/3、
   1/10/100 等组合在 `checkCollision(collection,collection)` 中均照样
   报碰撞。因此自碰撞使用显式非相邻连杆对 + 吸盘 Body 对 link1–5。
3. **内置 conveyor 粗代理**：模型根下 alias `shape` 是覆盖整条带的
   0.2×0.2×1.2 m 外包盒（顶面比真实带面高约 10 cm）。真实承载面是
   50 个 `pad`；实现把粗代理从查询角色中排除并记录原因，不修改其场景
   属性，也未收缩任何连杆代理。
4. **多解 IK 必需**：单一 IK 分支在工位/AGV 放置点会让 link3 与
   `Island_A_Station`/AGV 车体相交；使用 16 个 seed 的多解枚举后，
   station/agv 均有安全分支（`stage2_ik_candidates_*.json`）。
5. **腕部奇异性与已知限制**：按当前 UR5 模型关节坐标，工具竖直时 q5≈±90°，
   q4/q6 耦合；近似直线上升可能在中途产生绕工具 Z 的 yaw 旋转。q5 按 UR5
   轴号从底座数为第五轴 Wrist 2，在 Python 六元素数组中为 `q[4]`；这里的
   ±90° 是当前模型读数，不能直接推广为所有 UR5 模型的奇异角度。阶段二按
   “工具 Z 轴对齐”判据允许 yaw，最终放置点仍严格检查完整姿态；阶段二没有
   实现腕部分支锁定或禁止翻腕，因此正常流程通过不表示腕部动作已优化。后续
   阶段 2.01 方案要求标定当前模型的分支/奇异边界并硬性保持同一腕部分支，
   同分支无解时失败，见 [[wiki/06-工业案例/06-10-柔性工业车间阶段2.01-动作轨迹优化]]。
6. **真实/影子 FK 复核**：启动时和每次运动完成时在真实当前 q 比较
   真实/影子 7 个可见连杆与 TCP 世界位姿，容差 1e-4 m / 1e-3 rad；
   不一致立即锁存。最终 station/agv/both 证据中的 `fk.ok=true`。
7. **带载高位中转**：从 lift 直接关节插值到 pre_place 会让 10 cm 载荷
   扫过传送带/工位台面；`rise → transit(z≥0.80) → pre_place → place`
   在当前布局找到完整安全路径。该策略已固化到
   `TRANSFER_RISE_HEIGHT` / `TRANSFER_TRANSIT_*`。
   现阶段 `transit → pre_place` 仍由一个高位中转目标直接连接到较低的预接近目标，
   因而同时包含 XY 对位和 Z 下降；阶段 2.01 的待实施方案将其拆为同高的水平
   `transit → high-above-place` 与保持 XY 的竖直 `high-above-place → pre_place`。
   因此高位 `rise` 宜与 `transit`/`high-above-place` 对齐；不能在当前高度定义下
   直接把 `rise` 降到 station/AGV 的 `pre_place`，否则取料点带载净空不足。
8. **载荷承载接触**：抬升起点物料仍接触传送带，放置终点物料接触工位/
   载台。实现只在**这两个端点构型**放行载荷-承载面接触；中间构型仍
   严格检查。
9. **关节流式执行**：每步重复下发同一 `setJointTargetPosition` 会反复
   重置运动规划加速段，关节追不上目标。执行器改为“目标未达到时保持、
   达到后再下发下一构型”，并使用 0.20 rad 步长 + jerk=100 的实测参数。
10. **停止状态 `setJointPosition` 的测量陷阱**：对真实臂的部分关节在
   停止态调用 `setJointPosition` 不生效（影子臂同一模型正常）。生产路径
   不依赖它：规划/碰撞使用影子 FK，执行使用 `setJointTargetPosition`。
   诊断脚本不要用停止态 `setJointPosition` 构造真实臂验证位姿。
11. **摘要确定性**：`_jsonable` 原先直接迭代 set/frozenset，Python 哈希
    随机化会让 stage2 config digest 在不同进程间变化；已改为排序序列化。

## 5. 验收环境与参数

- CoppeliaSim 4.10.0.0，Bullet，步长 0.05 s。
- 名义带速 0.20 m/s，单物料 `Part_0001`，布局 `L1_S1`。
- 关键冻结参数（完整值见 `src/stage2/config.py` 与 manifest
  `extra.stage2`）：
  - 接触：max gap 5 mm，min signed −1.5 mm，lateral 20 mm，法向 0.12 rad，
    杯口半径设计 0.0235795 m（实测 0.023579 m）；
  - 碰撞：规划裕量 5 mm，最终接触/放置阶段不套用全工作区阈值；
  - 采样：关节 2°，候选粗筛 8°，关键连杆位移 25 mm，单段上限 250；
  - 规划：多解 12 seeds/6 candidates，DFS 节点 48，墙钟 60 s；
  - 执行：0.20 rad/命令，jerk 100，单路径点 20 s，偏差上限 0.30 rad；
  - 停带：速度上限 0.005 m/s，pad 位移 0.001 m，确认 2 步，超时 3 s，
    最大越程 0.03 m；
  - 补偿：只在每步物理位移落后带速 >2 mm 时补差额，上限 10 mm/步。

## 6. 验收细节

### 6.1 正常流程

- station：`phase2_run_station_none_20261021_190559.json`
  - 传送带传感器命中后 0.15 s 停带，越程 0.020 m，横向/高度漂移 <0.1 mm；
  - approach/lift/transfer/retreat 全部 `DIRECT_OK`；
  - 释放修正量与稳定窗口通过，最终物料 `[-0.20012, 1.20062, 0.35]`，
    误差 0.00062 m；
  - 安全未锁存。
- agv：`phase2_run_agv_none_20261021_190741.json`
  - 载荷在载台，AGV 轮速行驶 0.5022 m，跟随误差 0.0087 m；
  - `material_error` 是相对初始放置点的距离，AGV 行驶后不作为失败判据。
- both：`phase2_run_both_none_20261021_190928.json`
  - station UID 1、agv UID 2 均 DONE；任务间清理物料集合引用，无串任务。

### 6.2 运行期锁存

`phase2_run_station_runtime_obstacle_20261021_191237.json`：

- TRANSFERRING 阶段注入 0.08 m 受控障碍；
- 在下一控制步检出 `link3_visible <-> Fault_Runtime_Obstacle`；
- `SafetyLatch` 立即置位，剩余轨迹取消，不再下发运动目标；
- 停带/停车命令成功，真实带速 4e-5 m/s 确认停止；
- 任务 FAILED，未自动恢复/未误报 DONE；障碍在销毁时移除。

### 6.3 路径拒绝

`stage2_path_rejection_20261021_121749.json`：

- HOME → 已验证安全的工位预放置分支（3.48 rad，87 采样）基线 OK；
- 中间 TCP 插入 0.05 m 障碍，TCP 端点距中点 0.72/0.82 m；
- 段检查在 t=0.477 报 `PATH_COLLISION`，对象对
  `Body <-> Fault_Runtime_Obstacle`；
- 两端构型仍安全；移除障碍后同段恢复 OK；
- 未降级为只检查端点，未提交执行。

### 6.4 MCP 路由

- 通过 MCP `load_scene(current, phase="stage2")` → `validate_scene` →
  `start_simulation(backend="stage1", target="station")`：服务端按工件
  `source_phase` 路由到阶段二线程，日志显示 `backend=stage2`；
- station DONE，物料误差 0.00062 m；运行锁/生命周期复用同一套；
- 线程结束后仿真停止、会话状态回到 `SCENE_VALIDATED`，影子资源销毁。

### 6.5 工件复用

- `stage2/L1_S1/v009_20261021_190527` 保存时执行 load→read-only
  validate（144 项），父差异为空；
- `run_stage2.py` 默认 `load current --phase stage2` + validate + start；
- 最终 station 复跑通过（§6.1 的 190559 报告即 v009）。

## 7. 未实现 / 未验证范围

- **OMPL**：未启用，按 §6 记为 N/A；局部规划失败会停止，不回退到
  未检查的直线动作。
- **腕部分支连续性**：阶段二没有禁止腕部分支切换；q6 的小权重偏好不能
  替代分支跟踪，也不能证明没有翻腕。该动作优化要求由待实施的阶段 2.01 承接。
- **连续碰撞检测**：实现是离散采样（关节 2°、关键连杆 25 mm），
  不是连续碰撞证明；报告保留采样间隔与剩余漏检局限。
- **物理吸力**：仍为逻辑吸附，不模拟真空压力/流量/密封/负载极限。
- **自动恢复**：无重试、无重新规划、无隔离区释放、无人工复位流程，
  归项目最终阶段（06-05 §6）。
- **场景范围**：仍为单工位、单物料、名义带速；未扩展到多物料队列、
  第二工位、AGV 全局导航/多 AGV。
- **布局变更**：本阶段没有改布局、模型、TCP 安装、质量/摩擦/带速/
  步长；若后续修改这些参数，按 CLAUDE.md 必须重新标定并重跑受影响项。
- **`--keep-running`**：阶段二 runner 不支持（影子臂/集合必须在停止态
  销毁），传该参数会明确拒绝。
- **会话资源**：影子臂/集合是会话资源，进程崩溃时会留在场景中；
  重新 load 工件即恢复。stage2 保存/校验会拒绝持久化的 `Shadow_*`。

## 8. 已知性能

- 单次运动规划墙钟：approach ~1–2 s，lift ~1 s，transfer ~3–20 s
  （取决于分支回溯），retreat ~1 s。
- 最终 station 任务仿真时间约 37 s（含 4.65 s 输送/停带）；AGV 任务
  约 43 s（含 3.65 s 行驶）。
- 规划预算 60 s/运动，超限返回 `PLAN_BUDGET_EXCEEDED` 并停止。

## 9. 复现命令

```bash
# 工件（已存在时跳过）
.venv/Scripts/python src/run_scene_lifecycle.py load stage1/L1_S1/v002_20261020_234403
.venv/Scripts/python src/run_scene_lifecycle.py validate
.venv/Scripts/python src/run_scene_lifecycle.py save --phase stage2 \
  --parent stage1/L1_S1/v002_20261020_234403 --note "阶段二派生"

# 正常流程（默认加载 stage2 current）
.venv/Scripts/python src/run_stage2.py --target station --time 150
.venv/Scripts/python src/run_stage2.py --target agv --time 150
.venv/Scripts/python src/run_stage2.py --target both --time 180

# 运行期锁存（实验副本）
.venv/Scripts/python src/run_stage2.py --target station \
  --fault runtime_obstacle --time 150

# 路径拒绝 / 几何探针（只读影子查询）
.venv/Scripts/python docs/measurement/probe_stage2_path_rejection.py
.venv/Scripts/python docs/measurement/probe_stage2_inventory.py
.venv/Scripts/python docs/measurement/probe_stage2_ik_candidates.py
```

## 10. 代码版本 / 工作区

- 分支：`codex/phase2-physics-motion`（从 `main` 创建，未合并）。
- 本报告、阶段二代码/测试/工件/证据随同一提交进入该分支；具体 commit
  以 `git log` 为准。提交范围为 `src/stage2/`、`src/run_stage2.py`、
  `pyproject.toml`、`src/stage1/{lifecycle,run,scene_artifact,
  unit_controller,ur5_ik_controller}.py`、
  `src/reference/factory_mcp_server.py`、`tests/test_stage2_*.py`、
  `docs/measurement/probe_stage2_*.py`、
  `docs/scene_artifacts/stage2/L1_S1/v009_20261021_190527/`、本报告与
  evidence 归档。
- 阶段一父工件与 `stage1/current.json` 未被修改。

## 11. 证据文件

`docs/implementation/evidence/`：

- `phase2_run_station_none_20261021_190559.json`
- `phase2_run_agv_none_20261021_190741.json`
- `phase2_run_both_none_20261021_190928.json`
- `phase2_run_station_runtime_obstacle_20261021_191237.json`
- `stage2_path_rejection_20261021_121749.json`
- `stage2_inventory_20261021_104837.json`（P2-0 几何/4.10 API 盘点）
- `stage2_ik_candidates_20261021_105624.json`（多解安全分支）
- `phase2_mcp_station_none_20261021_191502.json`（MCP 阶段二路由）

原始日志（`stage2_sim.log`、`stage2_fault.log`、`coppelia_console*.log`）
不入库；运行原始输出在 `docs/implementation/runs/`，不提交。
