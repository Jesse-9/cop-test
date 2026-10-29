# 场景工件目录 (步骤 1.2)

本目录是步骤 1.2 定义的版本化场景工件仓库。每个工件是一个目录：

```text
<phase>/<layout>/v<NNN>_<YYYYMMDD_HHMMSS>/
  scene.ttt          # CoppeliaSim 场景文件 (停止态保存)
  manifest.json      # 稳定身份/配置/模型/物理/契约摘要 (schema 1.0)
  validation.json    # 保存后重新加载执行的只读校验报告
  lifecycle.jsonl    # 工件级生命周期事件 (追加式; 受保护工件不再追加)
```

## 命名空间

| phase | 用途 | 保护 |
|---|---|---|
| `stage1` | 阶段一通过基线, 只读父工件 | `protected: true`, 文件只读 |
| `stage2` | 阶段二从阶段一基线派生的独立副本 | CLI 默认 `protected: true`；`--no-protect` 可派生可变诊断副本 |
| `stage3` | 阶段三从阶段二工件派生的双工位副本 | CLI 默认 `protected: true` |
| `stage4` | 阶段四从阶段三工件派生的五岛多岛副本 | CLI 默认 `protected: true` |
| `diagnostic` | 临时诊断/试验场景 | 不保护 |

`.runtime/` 保存跨进程会话状态与运行锁, 每次运行重新生成, 已 gitignore,
不随代码提交。

## 当前阶段一基线

- 工件 ID: `stage1/L1_S1/v002_20261020_234403`
- 布局: `L1_S1` (UR5 基座 `(-0.20, 0.55, 0.30)`, 置物台 `(-0.20, 1.20)`,
  AGV dock `(0.40, 0.75)`)
- manifest schema: `1.0`
- scene sha256: `6757d04051667eb834dc20c38c07f6c060ae3928f86b48c81b4f782cbc6673f0`
- 校验: 130 项检查通过, 0 差异 (保存后重载 / 重启 CoppeliaSim 后重载)
- 证据: [`step1_2_post_restart_reuse_v002_20261021_000357.json`](../implementation/evidence/step1_2_post_restart_reuse_v002_20261021_000357.json)

`v001_20261020_233504` 是首次正式保存; 其 manifest 的物理引擎字段因
`sim.intparam_engine` 常量名错误记录为 `unknown`, 已由 v002 取代, 仅保留为
审计历史。`current.json` 指向 v002, 阶段二必须从 v002 派生。

## 当前阶段二工件（06-15）

- 工件 ID: `stage2/L1_S1/v014_20261028_175742`
- 父工件: `stage1/L1_S1/v002_20261020_234403`
- 契约: `extra.stage2.contract_version = stage2_contract_20261028_v4`;
  `extra.stage2.transfer` 记录 `TRANSFER_HEIGHT=0.80`、水平绕行偏移与规划预算，
  控制器 `transfer_trajectory_version = "06-15"`
- 回归: stage2 station/agv/both 与 runtime_obstacle 已通过；证据见
  [`docs/implementation/runs/`](../implementation/runs/) 中
  `phase2_run_*_20261028_*`
- 历史: v013 是 2.02 工件（契约 v3、动作优化 2.01、AGV 转运优化 2.02）；
  v009–v012 是阶段二/2.01 历史与过渡工件。修改 `src/stage2/config.py`
  后旧工件会因 `stage2_config_digest`/契约版本拒绝 start，必须重新派生
- 保护: 工件均由 CLI 保存为 `protected: true`（文件只读）
- 运行: `python src/run_stage2.py --target station|agv|both`
- 报告: [`PHASE2_REPORT.md`](../implementation/PHASE2_REPORT.md)、
  [`PHASE2_01_REPORT.md`](../implementation/PHASE2_01_REPORT.md)、
  [`PHASE2_02_REPORT.md`](../implementation/PHASE2_02_REPORT.md)、
  [06-15 方案](../wiki/06-工业案例/06-15-柔性工业车间水平带载转运与放置下探修改方案.md)

影子臂/碰撞集合/载荷副本是会话资源, 不进入 `scene.ttt`; 保存和只读校验
会拒绝持久化的 `Shadow_*` 对象。阶段二 `save` 在写入前剥离会话句柄,
父差异只比较稳定字段。

## 阶段三工件

- 当前: `stage3/L2_MIRROR/v006_20261028_180722`（父
  `stage2/L1_S1/v014_20261028_175742`）
- 契约: `extra.stage3.contract_version = stage3_contract_20261025_v1`;
  `extra.stage3.inheritance` 记录 stage2 父工件/契约/配置摘要。
- 派生: `load stage2/...` → `build --phase stage3 --parent <stage2>` →
  `save --phase stage3 --parent <stage2>`。
- 历史: v001–v005 保留为演进记录；修改 `src/stage3/config.py` 后旧工件
  会因 `stage3_config_digest` 拒绝，必须重新派生。
- 报告: [`PHASE3_REPORT.md`](../implementation/PHASE3_REPORT.md)。

## 当前阶段四工件

- 工件 ID: `stage4/L4_COLUMN/v008_20261028_225634`
- 父工件: `stage3/L2_MIRROR/v006_20261028_180722`
- 契约: `extra.stage4.contract_version = stage4_contract_20261028_v1`;
  `extra.stage4.inheritance` 记录 stage3 契约/配置/布局摘要与 stage2
  上游摘要。
- 场景新增 C/D/E 工位、S1→B/S2→D/C→E 三条物流线、S2 仓库、A→C 交接
  传感器、E 两个输入缓冲与输出 dummy；A/B 坐标、AGV 初始位姿和地面不变。
- 几何约束：C 岛/基座 0.45 m 高，A 放置后撤离到交接台南侧 park 位姿，
  避免 C 臂 link3/link6 与 A 交接台/腕部碰撞；改变这些几何必须重新
  派生并重跑 C 任务回归。
- 派生: `load stage3/...` → `build --phase stage4 --parent <stage3>` →
  `save --phase stage4 --parent <stage3>`。
- 修改 `src/stage4/config.py` 后旧工件会因 `stage4_config_digest` 拒绝，
  必须重新派生；不得手改 manifest。
- 历史: v001 是父摘要读取错误的失败草稿（已删除）；v002 物流线仍以
  `targetVel=0.2` 启动；v003 起物流线启动即停止；v004 把 C 岛/基座抬高到
  0.45 m，避免 C 臂 link3 与 A→C 交接台碰撞；v005–v008 调整 S1→B/S2→D
  支线、B/D 取料点与 D 岛高度，使 H_transfer=0.80 抬升可达。
- 验收: 2026-10-29 五个场景（full_flow/no_pair/agv_priority/
  agv_blocked/conveyor_fault）全部通过；证据见
  [`phase4_analysis_*`](../implementation/evidence/) 与
  [`stage4_layout_20261029_v008.json`](../implementation/evidence/stage4_layout_20261029_v008.json)。
- 报告: [`PHASE4_REPORT.md`](../implementation/PHASE4_REPORT.md)。

## `current` 指针

`load current` 默认读取 `stage1/current.json`（`--phase` 可切换命名空间）：

```text
<artifact-root>/stage1/current.json
  {"artifact_id": "stage1/L1_S1/v002_20261020_234403",
   "path": "L1_S1/v002_20261020_234403",
   "manifest_digest": "...", "scene_sha256": "...", "updated_at": "..."}
        ↓
<artifact-root>/stage1/L1_S1/v002_20261020_234403/scene.ttt
        ↓
sim.loadScene(scene.ttt) → adopt_scene() 按 alias 重建句柄
```

- 指针只在新工件写入、`loadScene` 回读并只读校验通过后原子更新；指向的
  工件必然是保存时校验通过的版本。
- 加载不会构建场景，也不会读取 `factory-sim.ttt` 或 `scenes/*.ttt`。
- 加载后状态为 `SCENE_LOADED`，可直接 `start`；启动点会在取得运行锁后执行
  完整只读工件校验，失败则拒绝启动并释放锁。`validate` 是可选诊断入口，
  用于主动检查并查看详细差异报告。`save` 仍要求场景已处于
  `SCENE_VALIDATED`（派生保存需先 `validate`）。
- 也支持直接指定完整工件 ID、目录名或工件根内路径。

## 覆盖与派生规则

1. 每次 `save` 创建新的 `v<NNN>_<stamp>` 目录; 不覆盖已有版本。
2. `stage1` 工件 `protected: true`, 拒绝覆盖; 场景、manifest、validation
   或 lifecycle 文件变化都会导致摘要/校验失败。
3. 阶段二派生:
   ```bash
   python src/run_scene_lifecycle.py load current
   python src/run_scene_lifecycle.py validate   # save 要求 SCENE_VALIDATED
   python src/run_scene_lifecycle.py save --phase stage2 --parent <stage1-artifact-id>
   ```
   这里的 `validate` 是 `save` 的状态前置 (与 `start` 无关); `start` 本身
   可直接从加载态发起并在启动点校验。派生副本默认保持几何/布局/坐标/
   模型/物理参数不变, 只扩展 manifest
   契约; 任何物理改动必须在 manifest 的 `extra`/差异清单中记录理由和前后值。
   阶段二 `save` 会剥离 `tool.handles`、`conveyor.script_handle`、
   `sim_time` 等会话/易变字段, 父差异 `[]` 表示对象、配置、布局、模型和
   脚本摘要一致。
4. `current.json` 只在新工件写入、重新加载并校验通过后原子更新。
5. `build` 只执行构建契约检查，不产生工件 manifest；`build → save` 才会
   保存、回读并执行工件级完整只读校验。未保存的临时构建场景在 `start`
   时只复核 `scene.verify()`，不能报告为工件级全量校验通过。

## 命令

```bash
# 显式构建 → 保存 → 加载 → 启动 (启动点自动校验)
python src/run_scene_lifecycle.py build
python src/run_scene_lifecycle.py save --phase stage1 --note "..."
python src/run_scene_lifecycle.py load current
python src/run_scene_lifecycle.py start --target station --time 45

# 可选诊断: 主动查看完整校验报告 (start 仍会重新校验)
python src/run_scene_lifecycle.py validate

# 停止/状态/列表
python src/run_scene_lifecycle.py stop
python src/run_scene_lifecycle.py status
python src/run_scene_lifecycle.py list

# MCP: build_scene / save_scene / load_scene / validate_scene /
#      start_simulation / stop_simulation / lifecycle_status
```

`start` 不会隐式构建或清场; 没有已加载场景时会被拒绝; 加载后可直接启动,
启动点校验失败同样拒绝并释放本次运行锁。运行中 `build`/`load`/`save`/
`validate` 和第二路 `start` 都会被拒绝 (状态机 + 跨进程运行锁)。
