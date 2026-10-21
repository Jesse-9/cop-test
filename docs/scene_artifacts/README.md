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

## 当前阶段二工件

- 工件 ID: `stage2/L1_S1/v009_20261021_190527`
- 父工件: `stage1/L1_S1/v002_20261020_234403`
- 契约: `extra.stage2.contract_version = stage2_contract_20261021`;
  控制器 `stage2.unit_controller.Stage2UnitController`
- 校验: 144 项通过, 父差异 `[]`; stage2 manifest 不写会话句柄
  (`tool.handles`/`conveyor.script_handle`/`sim_time` 已剥离)
- 保护: v009 由 CLI 保存为 `protected: true`（文件只读）；派生新版本不受影响。修改 `src/stage2/config.py` 后旧工件会因
  `stage2_config_digest` 拒绝 start, 必须重新派生
- 运行: `python src/run_stage2.py --target station|agv|both`
- 报告: [`PHASE2_REPORT.md`](../implementation/PHASE2_REPORT.md)

影子臂/碰撞集合/载荷副本是会话资源, 不进入 `scene.ttt`; 保存和只读校验
会拒绝持久化的 `Shadow_*` 对象。阶段二 `save` 在写入前剥离会话句柄,
父差异只比较稳定字段。

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
- 加载后状态为 `SCENE_LOADED`，必须 `validate` 才能 `start`。
- 也支持直接指定完整工件 ID、目录名或工件根内路径。

## 覆盖与派生规则

1. 每次 `save` 创建新的 `v<NNN>_<stamp>` 目录; 不覆盖已有版本。
2. `stage1` 工件 `protected: true`, 拒绝覆盖; 场景、manifest、validation
   或 lifecycle 文件变化都会导致摘要/校验失败。
3. 阶段二派生:
   ```bash
   python src/run_scene_lifecycle.py load current
   python src/run_scene_lifecycle.py validate
   python src/run_scene_lifecycle.py save --phase stage2 --parent <stage1-artifact-id>
   ```
   派生副本默认保持几何/布局/坐标/模型/物理参数不变, 只扩展 manifest
   契约; 任何物理改动必须在 manifest 的 `extra`/差异清单中记录理由和前后值。
   阶段二 `save` 会剥离 `tool.handles`、`conveyor.script_handle`、
   `sim_time` 等会话/易变字段, 父差异 `[]` 表示对象、配置、布局、模型和
   脚本摘要一致。
4. `current.json` 只在新工件写入、重新加载并校验通过后原子更新。

## 命令

```bash
# 显式构建 → 保存 → 加载 → 校验
python src/run_scene_lifecycle.py build
python src/run_scene_lifecycle.py save --phase stage1 --note "..."
python src/run_scene_lifecycle.py load current
python src/run_scene_lifecycle.py validate

# 启动/停止/状态/列表
python src/run_scene_lifecycle.py start --target station --time 45
python src/run_scene_lifecycle.py stop
python src/run_scene_lifecycle.py status
python src/run_scene_lifecycle.py list

# MCP: build_scene / save_scene / load_scene / validate_scene /
#      start_simulation / stop_simulation / lifecycle_status
```

`start` 不会隐式构建或清场; 没有已加载且校验通过的场景时会被拒绝。
运行中 `build`/`load`/`save`/`validate` 和第二路 `start` 都会被拒绝
(状态机 + 跨进程运行锁)。
