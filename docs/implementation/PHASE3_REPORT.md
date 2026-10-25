		# 阶段三实施报告：双工位柔性搬运

日期：2026-10-25
分支：`codex/phase3-dual-station`
上游计划：[06-12-柔性工业车间阶段三双工位详细实施步骤](../wiki/06-工业案例/06-12-柔性工业车间阶段三双工位详细实施步骤.md)
父工件：`stage2/L1_S1/v013_20261024_232558`（stage2，protected）
阶段三工件：`stage3/L2_MIRROR/v004_20261025_131837`

> 本报告记录阶段三代码、场景派生、五个精简验收场景和 MCP 路由的实测结果。
> 阶段三不包含阶段 2.1 的统一故障恢复：遇到阶段二安全锁存时保持停止、
> 保留现场、任务不得改写为 DONE，复位/续作仍归项目最终故障恢复阶段。

## 1. 结论

阶段三双工位闭环、资源互斥、AGV 两站转运、受控阻塞与不稳定卸载拒绝
均已实现并通过实机验收：

- 两个工位各自拥有独立 simIK environment、影子 UR5、碰撞集合、
  `ConfigurationChecker`、`LocalPlanner` 和 `Stage2Safety`；
- A 工位从共享传送带取料并装载 AGV；AGV 沿 x=0.40 直线带载运行到
  dock B；B 工位从 AGV 载台取料并放置到 Station B；
- 共享取料区 `pick_zone`、AGV 通道 `agv_lane`、两个 dock 和两台臂的
  envelope 由固定顺序资源锁表互斥；重复任务按 `(kind, uid)` 拒绝；
- AGV 停靠、航向、载荷滑移和动作剖面全部写入报告；受控阻塞可进入
  BLOCKED、停车并保留任务/资源，清障且现场重新确认后低速续行；
- 不稳定卸载在释放前触发 `LOAD_DRIFT` 锁存，B 任务 FAILED，载荷保持
  `HELD_BY_SUCTION`，未发生释放；锁存后的停止命令经真实读数确认。

关键验收结果：

| 用例 | 结果 |
|---|---|
| 全量测试 | **419 passed, 3 skipped**（阶段三新增 34 项） |
| 场景构建契约 | **109/109** 通过（A 43 项基线 + B/共享对象） |
| 工件只读校验 | **229 项通过**，差异 0 |
| A→B 闭环 | 3 任务 DONE；停靠误差 **0.019733 m**，航向 **−0.000203 rad**，滑移 **0.0 m** |
| 竞争与重复请求 | 3 个重复请求被拒绝；dock_B 等待 1 次；任务全部 DONE，资源全部释放 |
| 受控 AGV 阻塞 | t=44.90 进入 BLOCKED；t=54.95 清障续行；停靠误差 **0.019773 m** |
| 不稳定卸载拒绝 | `LOAD_DRIFT(0.0400>0.02)` 锁存；unload FAILED；release_attempted=false；载荷仍吸附 |
| 双向路线基线 | A→B 带载停靠 **0.019742 m**；B→A 空载 **0.019522 m**；滑移 0.0 |
| MCP 路由 | `start_simulation` 自动 backend=stage3；3 任务 DONE；报告已写入 |

## 2. 工件与契约

- 父工件：`stage2/L1_S1/v013_20261024_232558`
  - stage2 契约 `stage2_contract_20261024_v3`；父工件未被修改。
- 阶段三工件：`stage3/L2_MIRROR/v004_20261025_131837`
  - `source_phase = stage3`，`protected = true`；
  - scene sha256 `7801de7eb4b9cd1773afc5148b1e270d26de3bd278121bcb4fd8ba99030b7b21`；
  - manifest sha256 `98163750b698f9bce8d3dc86edf87f795ea0761514210e02ce670784609270af`；
  - `extra.stage3.contract_version = stage3_contract_20261025_v1`；
  - 控制器 `stage3.unit_controller.Stage3UnitController`；
  - 协调器 `stage3.coordinator.Stage3Coordinator`；
  - runner `stage3.run.run_loaded_scene_stage3`；
  - stage3 config digest `1ff5ea50…`，layout digest `0357cef0…`；
  - 父差异：新增 11 个 B/共享对象 alias，无 alias 被移除；
    layout/geometry 差异来自 AGV 初始位姿与 dock A 位置调整。

历史中间工件 `v001`、`v002`、`v003` 保留在 stage3 命名空间，仅用于
演进记录；当前指针与报告引用 `v004`。

## 3. 代码模块

| 模块 | 作用 |
|---|---|
| `src/stage3/config.py` | 双工位布局、资源表、任务流、AGV 路线、故障注入与验收参数 |
| `src/stage3/layout.py` | `L2_MIRROR` 布局、`UnitLayoutView`、候选静态筛选、布局序列化 |
| `src/stage3/scene.py` | 从 stage2 工件加载并增加 B 单元、`Stage3Scene`、109 项构建契约 |
| `src/stage3/resources.py` | 6 资源固定顺序锁表、等待/释放/顺序违规日志 |
| `src/stage3/tasks.py` | UID 任务、状态机、活动任务去重、拒绝记录 |
| `src/stage3/agv_route.py` | 直线穿梭、速度剖面、停靠/航向/滑移指标、BLOCKED/续行 |
| `src/stage3/runtime.py` | 两台影子臂、独立碰撞集合/规划器/锁存、会话资源清理 |
| `src/stage3/unit_controller.py` | 传送带/AGV 托盘两种取料源、AGV 保持静止、不驱动 |
| `src/stage3/coordinator.py` | 任务编排、资源互斥、AGV 调度、故障注入、验收 |
| `src/stage3/scene_contract.py` | `extra.stage3`、布局/配置摘要、只读校验 |
| `src/stage3/run.py` | 场景 runner、证据、CLI |
| `src/run_stage3.py` | CLI 兼容入口；`uv run run-stage3` |

阶段一/二兼容扩展（默认行为不变）：

- `stage1/unit_controller.py`、`stage2/unit_controller.py` 把单步拆成
  `pre_tick()` / `post_tick()`，让阶段三两台控制器共享**一次** `sim.step()`；
  原 `step()` 行为不变；
- `stage1/material_manager.py` 增加 `ON_TRAY→READY_FOR_PICK` 与
  `RELEASED→ON_TRAY`，支持 B 工位重新确认 AGV 载台物料；
- `stage2/shadow.py` 支持按臂别名构建/精确清理影子臂；
- `stage2/collisions.py` 支持按工位指定 mount、双 station 工作区和
  “另一台真实臂”障碍；
  另一台真实臂的 `collidable`/`measurable` 只在会话期启用，销毁时恢复原值
  （最终闭环实测 `restored=32, failed=[]`），不改变 static/respondable/质量/摩擦。
- `stage1/scene_artifact.py` 阶段列表加入 `stage3`，manifest 按阶段选择
  必需 alias，并允许场景自带 builder 信息；
- `stage1/lifecycle.py` 支持 `build --phase stage3 --parent`、阶段三分派
  adopt/contract/runner，以及 `save --phase stage3`；
- `reference/factory_mcp_server.py` 按工件 `source_phase` 路由 stage3，
  `status` 报告任务/资源状态。

## 4. 布局与选型

生产布局 `L2_MIRROR`（06-12 §3.1 的传送带两侧镜像候选）：

| 对象 | A 工位 | B 工位 |
|---|---|---|
| UR5 基座 | (−0.20, +0.55, 0.30) | (−0.20, −0.55, 0.30) |
| Island | (−0.20, +0.55) | (−0.20, −0.55) |
| Station | (−0.20, +1.20) | (−0.20, −1.20) |
| AGV dock | (0.40, +0.70) | (0.40, −0.70) |

- 共享取料区：`(-0.20, 0.0, 0.50)`，仍在传送带上表面有效行程内；
- AGV 初始位姿：dock A `(0.40, 0.70)`，yaw = −π/2（面向 B）；
- AGV 路线：x=0.40 直线，A→B 前进、B→A 倒车，不在 dock 处原地旋转；
- 静态筛选：`L2_MIRROR` 50 项检查全部通过；同侧沿 X 候选 `L3_SAME_SIDE`
  因 B 臂取料可达性、Island/dock 压占、通道/路线轴检查失败被拒绝；
- 选型筛选只证明静态几何/粗可达性，不代替碰撞证明；生产布局的完整
  取放/转运路径已由五个实机场景通过阶段二 `check_segment` 检查。

## 5. 资源锁与任务流

资源固定顺序：

```text
pick_zone → agv_lane → dock_A → dock_B → arm_A → arm_B
```

- `pick_zone`：A 装载任务从计划开始持有；TCP 退出 conveyor envelope
  后释放。B 的 dock 请求在 AGV 运输途中记录 WAIT，但不提前获取 dock，
  避免与运输任务形成死锁。
- `agv_lane` / `dock_B`：由 AGV 运输任务持有；到站停稳并确认后释放，
  B 工位再获取 `dock_B` 执行卸载。
- `arm_A` / `arm_B`：本任务运动包络；另一台真实臂的可见连杆和吸盘
  Body 同时加入本臂碰撞工作区，形成“真实互扰查询 + 包络令牌”的双重
  约束。
- 失败/停止释放普通调度锁；安全锁存由 `Stage2Safety` 独立持有，
  资源释放不会清除锁存。

任务链：

```text
load_A_uid1 (conveyor → AGV@A)
  → transport_uid1 (AGV 带载 A→B)
  → unload_B_uid1 (AGV@B → Station B)
```

`route_baseline` 追加 `return_uid1`（AGV 空载 B→A）。同一 UID 的活动
`load_A` / `transport` / `unload_B` 重复提交均被拒绝，并写入
`rejections`。

## 6. 验收细节

### 6.1 A→B 闭环

`phase3_run_closed_loop_20261025_140903.json`

- 三个任务 DONE；UID 全程为 1；物料最终 `RELEASED`，位置
  `(-0.20032, -1.197541, 0.35)`，相对 Station B 目标误差约 **0.0027 m**；
- AGV 带载 A→B：停靠误差 **0.019733 m**，航向误差 **−0.000203 rad**，
  载荷相对载台滑移 **0.0 m**，时长 12.35 s，稳态速度窗口占比 0.9073；
- 两臂 `Stage2Safety` 均未锁存；资源全部释放；无重复任务。

### 6.2 竞争与重复请求

`phase3_run_contention_20261025_132245.json`

- 同时提交 A 装载、B 卸载和 AGV 运输任务，并重复提交三次同一 UID；
- 3 个重复请求被拒绝（`load_A_uid1`/`transport_uid1`/`unload_B_uid1`）；
- B 工位在 AGV 运输途中请求 `dock_B`，资源日志记录 1 次 WAIT；
- 三个真实任务全部 DONE，无双重吸附/重复派车，资源最终全部释放。

### 6.3 单次受控阻塞

`phase3_run_blocked_20261025_141704.json`

- AGV 运输开始时在路线中点放置静态可响应障碍；
- t=44.90 轮速已下发但 3 s 窗口仅移动 0.00003 m → `BLOCKED`，立即停车；
- t=54.95 移除障碍，确认无安全锁存、`agv_lane` 所有权仍属于运输任务后
  以 0.08 m/s 续行；
- 最终到站误差 **0.019773 m**，任务完成后才标记 DONE；BLOCKED 期间
  未误报完成。

### 6.4 不稳定卸载拒绝

`phase3_run_unstable_unload_20261025_134050.json`

- B 进入 PLACING 前注入 0.04 m 载荷相对 TCP 偏移；
- 下一控制步 `Stage2Safety` 检出 `LOAD_DRIFT(0.0400>0.02)` 并锁存；
- B 任务 FAILED，`release_attempted=false`，物料保持 `HELD_BY_SUCTION`，
  未放置到 Station B；
- 锁存后停止命令成功，真实带速/车体静止经 20 步窗口确认
  （`safety_stop_confirmed=true`）；未自动恢复/重试。

### 6.5 双向路线基线

`phase3_run_route_baseline_20261025_133223.json`

| 方向 | 负载 | 停靠误差 | 航向误差 | 滑移 | 时长 | 稳态窗口 |
|---|---|---|---|---|---|---|
| A→B | 带载 | 0.019742 m | −0.000370 rad | 0.0 m | 12.35 s | 0.9073 |
| B→A | 空载 | 0.019522 m | +0.000822 rad | N/A | 12.00 s | 0.9087 |

### 6.6 MCP 路由

`phase3_mcp_closed_loop_20261025_133838.json`

- 独立 MCP 服务器实例执行 `load_scene(current, phase="stage3")` →
  `validate_scene` → `start_simulation(scenario="closed_loop")`；
- 服务端自动识别 `source_phase=stage3`，backend=stage3，复用同一
  生命周期/运行锁；
- 三任务 DONE，报告写入 `docs/implementation/runs/`，结束后会话回到
  `SCENE_VALIDATED`，影子资源清理无残留。

## 7. 动作质量与遗留问题

阶段三不改变阶段二规划/联锁/轨迹参数，也未关闭 06-11 的遗留问题。
本阶段实测（`closed_loop`，A 装载）：

| 指标 | A 装载 | B 卸载 |
|---|---|---|
| 运动总 TV | 18.731 rad | 11.649 rad |
| `TV(q[0])` | 1.689 rad | 2.889 rad |
| `TV(q[3])` | 8.133 rad | 3.164 rad |
| `pre_place` 的 `TV(q[3])` | **4.931 rad**（净 +4.931） | 0.588 rad（净 +0.588） |
| 实测 jerk 峰值 | **120.69 rad/s³**（TRANSFERRING） | 113.94 rad/s³ |
| 实测速度峰值 | 0.903 rad/s | 0.891 rad/s |
| 实测加速度峰值 | 5.999 rad/s² | 5.998 rad/s² |

- **MQ-01/MQ-02 未关闭**：A 装载到 dock A 的 `pre_place` 仍产生约
  4.931 rad 的 q[3] 行程；总 TV 中 q[0]/q[3] 的分配与阶段二基线同类，
  未因双工位布局改善，也未把问题转移到未记录轴。
- **MQ-03 未关闭**：执行 jerk 峰值 113.9–120.7 rad/s³ > 100 rad/s³；
  速度/加速度在目标限值内，但阶段三不宣称动作已平顺。
- **MQ-04/MQ-05 未关闭**：未启用 high_align/detour 拆分，未改变
  `baseline` + `greedy` + 无平滑的默认策略。
- 阶段三新增路线（A→AGT dock A、AGV→B、B 从载台取料）已记录逐段
  q/TCP/净空/分支指标；完整 trace 保留在 `docs/implementation/runs/`
  原始报告中，证据目录保留裁剪后的计划/执行剖面。

## 8. 未实现 / 限制

- **阶段 2.1 统一故障恢复**：未实现；安全锁存后只停止、保留现场、
  任务 FAILED，不自动复位/续作。
- **OMPL**：未启用；局部规划失败保持失败。
- **多物料/多 AGV/五岛**：未实现；阶段三只验证单 UID、单 AGV、两个工位。
- **真实真空/视觉识别**：仍为逻辑吸附；未使用 Vision Sensor。
- **双臂并发运动**：任务链串行；资源表与 `arm_A/arm_B` 令牌保留并发
  扩展能力，但阶段三未验证两臂同时运动。
- **碰撞证明**：阶段二仍是离散采样（2° 关节、25 mm 关键连杆位移），
  不是连续碰撞证明；布局静态筛选也不替代碰撞查询。
- **载荷滑移**：本轮实测滑移为 0.0 m，但只覆盖名义速度/单载荷；改变
  质量、摩擦、轮速或载台几何必须重跑阶段三路线与动作质量验收。

## 9. 复现命令

```bash
# 1. 从 stage2 父工件派生 stage3 场景并保存 (当前已存在 v004)
.venv/Scripts/python src/run_scene_lifecycle.py load stage2/L1_S1/v013_20261024_232558
.venv/Scripts/python src/run_scene_lifecycle.py build --phase stage3 \
  --parent stage2/L1_S1/v013_20261024_232558
.venv/Scripts/python src/run_scene_lifecycle.py save --phase stage3 \
  --parent stage2/L1_S1/v013_20261024_232558 --note "阶段三派生"

# 2. 五个精简验收场景 (默认加载 stage3 current + validate + start)
.venv/Scripts/python src/run_stage3.py --scenario closed_loop --time 300
.venv/Scripts/python src/run_stage3.py --scenario contention --time 300
.venv/Scripts/python src/run_stage3.py --scenario blocked --time 300
.venv/Scripts/python src/run_stage3.py --scenario unstable_unload --time 300
.venv/Scripts/python src/run_stage3.py --scenario route_baseline --time 300

# 3. 布局静态筛选 + 当前场景只读测量
.venv/Scripts/python docs/measurement/probe_stage3_layout.py
.venv/Scripts/python docs/measurement/probe_stage3_layout.py --sim \
  --out docs/measurement/stage3_layout_<stamp>.json
```

生命周期 CLI 也可直接启动：

```bash
.venv/Scripts/python src/run_scene_lifecycle.py load current --phase stage3
.venv/Scripts/python src/run_scene_lifecycle.py validate
.venv/Scripts/python src/run_scene_lifecycle.py start --scenario closed_loop --time 300
```

## 10. 代码版本 / 工作区

- 分支：`codex/phase3-dual-station`（从 `codex/phase2-physics-motion` 创建）。
- 本报告、阶段三代码/测试/工件/证据随同一提交进入该分支；具体 commit
  以 `git log` 为准。
- 工作区包含用户此前未提交的 stage2/文档修改；本次没有改写或回退这些
  内容，也没有覆盖 stage1/stage2 父工件。
- 阶段一基线 `stage1/L1_S1/v002_20261020_234403`、阶段二父工件
  `stage2/L1_S1/v013_20261024_232558` 的 scene/manifest 摘要未变化。

## 11. 证据文件

`docs/implementation/evidence/`：

- `phase3_run_closed_loop_20261025_140903.json`
- `phase3_run_contention_20261025_132245.json`
- `phase3_run_blocked_20261025_141704.json`
- `phase3_run_unstable_unload_20261025_134050.json`
- `phase3_run_route_baseline_20261025_133223.json`
- `phase3_mcp_closed_loop_20261025_133838.json`
- `scene_validation_stage3_L2_MIRROR_v004_20261025_131837_20261025_134050.json`
- `stage3_layout_20261025.json`

原始完整 trace 在 `docs/implementation/runs/`（不提交）；证据目录中的
JSON 是裁剪后的计划/执行剖面与验收字段，保留 TV/jerk/停靠/滑移/锁存
等结论所需数据。
