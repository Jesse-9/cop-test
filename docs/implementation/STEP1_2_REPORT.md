# 步骤 1.2 实施报告：场景工件与仿真生命周期解耦

- 日期：2026-10-20 / 2026-10-21
- 分支：`codex/step1.2-lifecycle`
- 对应计划：[[wiki/06-工业案例/06-08-柔性工业车间步骤1.2-场景生命周期与运行入口]]
- 结论：**通过**。阶段一场景已固化为版本化工件；构建、保存、加载、
  只读校验、启动、停止是可单独调用、语义一致的动作；`start` 不再隐式
  build/clear；两次 CoppeliaSim 进程重启后均可从工件恢复并完成 station 任务。

## 1. 目标与边界

本步骤只建立“场景可复用”的基础设施：工件、manifest、版本保护、只读
校验、运行锁、CLI/MCP 入口和状态机。碰撞集合、TCP 穿透、避障、conveyor
停机安全、脚本最终剥离策略仍属于阶段二，本步骤不验收。

## 2. 现状盘点与差距

| 现状 | 差距 |
|---|---|
| `stage1.run.main` 连接后 `build_scene(clear=True)` | 每次启动隐式清场重建，无法从同一场景继续 |
| MCP `_start_stage1` 在 start 内部 build | start 语义不纯，且会销毁当前场景 |
| 无 `.ttt` 保存/加载/工件版本 | 重启 CoppeliaSim 后只能重建 |
| 无 manifest/稳定身份 | 无法判断加载后的场景是否与构建时一致 |
| 无跨进程运行锁 | 第二个控制循环可能并发 `sim.step()` |

已有 `scenes/3arm_simulation_test.ttt`、`scenes/stage2_scene.ttt` 未读取、
未修改；用户未提交的文档改动全部保留。

## 3. 实现

### 3.1 模块

| 模块 | 职责 |
|---|---|
| `src/stage1/scene_artifact.py` | 工件仓库、manifest schema 1.0、sha256/配置/模型摘要、只读校验、原子写入、路径越界防护 |
| `src/stage1/lifecycle.py` | 生命周期状态机、会话状态（跨 CLI 进程）、PID+run_id 运行锁、build/save/load/validate/start/stop、CLI |
| `src/stage1/scene.py` | 新增 `adopt_scene()`：加载 `.ttt` 后按 alias 重建句柄、关节链、conveyor 元数据并重新执行契约 |
| `src/stage1/run.py` | 新增 `run_loaded_scene()`（不 build）；`main()` 默认加载当前工件，`--build` 才显式重建 |
| `src/run_scene_lifecycle.py` | 生命周期 CLI 兼容入口；`pyproject.toml` 增加 `scene-lifecycle` |
| `src/reference/factory_mcp_server.py` | 新增 `save_scene`/`load_scene`/`validate_scene`/`lifecycle_status`（共 24 工具）；stage1 start 改为只启动已校验场景 |

### 3.2 状态机

```text
DISCONNECTED → CONNECTED_STOPPED → SCENE_LOADED → SCENE_VALIDATED
                                      ↑              ↓
                                      └──── save ────┘
SCENE_VALIDATED → SIMULATION_RUNNING → SCENE_VALIDATED (stop)
校验失败 → ERROR；ERROR 只能通过显式 load/build 恢复
```

- 运行中拒绝 `build`/`load`/`save`/`validate`/重复 `start`。
- `prepare_start` 在只读校验**之前**获取运行锁：校验窗口内其他进程
  也不能 load/build（实测 `RUN_CONFLICT`）。
- 运行锁为 `docs/scene_artifacts/.runtime/run.lock`，记录 pid/run_id/
  artifact/target；持有进程死亡后由下一次 start 清理。

### 3.3 工件与 manifest

工件目录：`docs/scene_artifacts/<phase>/<layout>/v<NNN>_<stamp>/`，
包含 `scene.ttt`、`manifest.json`、`validation.json`、`lifecycle.jsonl`。

manifest schema 1.0 记录：

- schema/工件 ID/创建时间/来源阶段/父工件/保护标记/actor/note；
- git 分支、提交、脏文件；
- 配置摘要 + 165 项配置快照；CoppeliaSim 4.10.0.0、物理引擎 `bullet`、
  步长 0.05 s；
- 4 个内置模型的绝对路径、存在性、sha256、大小；
- 布局 `L1_S1` 与 26 个对象的 alias/类型/父 alias/世界坐标（无运行时句柄）；
- conveyor 几何、TCP/吸盘安装几何、物料/AGV 物理参数；
- conveyor 脚本保留、UR5/suction/p3dx 脚本剥离、吸盘模型传感器改名；
- 构建器版本与 42 项构建契约检查；
- 场景文件 sha256 与大小。

`validate_scene` 只读比较：场景文件摘要、schema、配置摘要（逐键差异）、
模型摘要、仿真器版本/引擎、alias 唯一性、对象类型/父级/世界坐标、
无旧挡停体、`Stage1Scene.verify()` 契约。缺失/重复 alias 或配置/模型漂移
都会在 start 前失败并输出差异；校验器不修复、不重命名、不补建。

## 4. 场景工件

| 工件 | 说明 |
|---|---|
| `stage1/L1_S1/v002_20261020_234403` | **当前阶段一基线**，`protected: true`，文件只读 |
| `stage1/L1_S1/v001_20261020_233504` | 首次保存；manifest 的物理引擎字段因 `sim.intparam_engine` 常量名错误记为 `unknown`，已被 v002 取代，仅保留审计历史 |

v002：

- manifest schema `1.0`
- manifest sha256 `961c6ff426686b9a633dada0541bc60bd2e908d72d9ddd395eea6af90271dc30`
- scene sha256 `6757d04051667eb834dc20c38c07f6c060ae3928f86b48c81b4f782cbc6673f0`
- 保存后重载校验 130 项全通过，0 差异
- 2026-10-21 00:03 第二次 CoppeliaSim 进程重启后再次加载/校验/运行通过

阶段二派生方式：

```bash
python src/run_scene_lifecycle.py load current
python src/run_scene_lifecycle.py validate
python src/run_scene_lifecycle.py save --phase stage2 \
  --parent stage1/L1_S1/v002_20261020_234403
```

`phase=stage2` 未指定 `--parent` 会被拒绝；派生工件的 manifest
`parent_artifact.differences` 记录 config/layout/models/objects/geometry/
tool/physics/scripts 的章节级差异，objects 另列新增/移除 alias。

## 5. 入口与用法

### 5.1 CLI

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
```

注意：全局参数（`--host`/`--port`/`--artifact-root`/`--out-dir`）必须写在
子命令之前。

### 5.2 MCP

| 工具 | 语义 |
|---|---|
| `build_scene` | 显式构建并执行构建契约；状态 → `SCENE_VALIDATED(BUILT)` |
| `save_scene` | 停止态保存新版本；保存后立即重载校验，通过才更新 current |
| `load_scene` | 停止态加载 `.ttt`，按 alias 重建句柄；状态 → `SCENE_LOADED` |
| `validate_scene` | 只读校验当前场景与 manifest |
| `start_simulation` | 只启动当前已加载且已校验场景；拒绝时返回 `INVALID_STATE`/`RUN_CONFLICT`/`VALIDATION_FAILED` |
| `stop_simulation` | 停止并等待停止态；释放本进程运行锁 |
| `lifecycle_status` | 状态、工件、run_id、锁、允许动作、工件列表 |

`build_scene` 仍是显式动作；`start_simulation` 不再调用它。

### 5.3 兼容入口

`python src/run_stage1.py --target station` 现在默认加载 `current` 工件并
校验后运行；`--build` 才显式执行 build → save → start。旧的“无工件直接
启动”行为已移除。

## 6. 测试与证据

### 6.1 自动化测试

| 文件 | 数量 | 内容 |
|---|---|---|
| `tests/test_scene_artifact.py` | 19 | 版本递增、路径越界、current 原子指针、protected/force、manifest schema、alias 格式/重复、配置摘要差异、原子写入、只读位 |
| `tests/test_scene_lifecycle_state.py` | 20 | 状态迁移矩阵、运行中拒绝、ERROR 恢复、会话持久化、运行锁独占/陈旧锁/只释放自己的锁 |
| `tests/test_scene_lifecycle_manager.py` | 14 | build/load/validate/start/save 前置条件、锁先于校验、校验失败释放锁、保存后 current、基线保护 |
| `tests/test_scene_lifecycle_contract.py` | 4 | CLI/MCP 动作名一致、24 工具、start 文档明确不隐式 build |
| `tests/test_mcp_lifecycle_tools.py` | 7 | MCP save/load/validate/status 路由与运行中拒绝 |
| `tests/test_mcp_stage1_backend.py` | 9 | stage1 路由、start 不调用 build、status 生命周期字段 |
| 全量 `pytest -q` | 273 passed / 3 skipped | 3 skipped 为需显式重建完整五岛场景的历史集成测试 |

### 6.2 真实 CoppeliaSim 验收

| 场景 | 结果 | 证据 |
|---|---|---|
| 首次 build → save → 重载校验 | 通过；130 项检查，0 差异 | `v002/validation.json` |
| 进程重启 1：加载 v001 → validate → station | 通过；130 项，run `all_ok=true`，255 步，sim_time 13.6 s | `step1_2_post_restart_reuse_20261020_234207.json` |
| 进程重启 2：加载 v002 → validate → station | 通过；130 项，run `all_ok=true`，255 步，sim_time 13.6 s | `step1_2_post_restart_reuse_v002_20261021_000357.json` |
| 移除必需 alias `Floor` | `VALIDATION_FAILED`，缺失 `["Floor"]`，状态 ERROR，start 被拒 | `step1_2_lifecycle_checks_20261020_235739.json` |
| 修改 `MATERIAL_MASS` | `VALIDATION_FAILED`，差异 `saved=0.1 current=1.1`，状态 ERROR | 同上 |
| 运行中 load/build | 均 `RUN_CONFLICT`，场景未被替换；运行本身 `all_ok=true` | 同上 |
| 基线保护 | 全部检查前后 scene sha256 不变 | 同上 |

负向检查脚本可复现：`python docs/measurement/check_step1_2.py`。

### 6.3 证据文件

- `docs/implementation/evidence/step1_2_lifecycle_checks_20261020_235739.json`
- `docs/implementation/evidence/step1_2_post_restart_reuse_20261020_234207.json`
- `docs/implementation/evidence/step1_2_post_restart_run_20261020_234207.json`
- `docs/implementation/evidence/step1_2_post_restart_reuse_v002_20261021_000357.json`
- `docs/implementation/evidence/step1_2_post_restart_run_v002_20261021_000357.json`
- `docs/scene_artifacts/stage1/L1_S1/v002_20261020_234403/`

## 7. 决策与偏差记录

| 决策 | 理由 | 代价 |
|---|---|---|
| `start` 允许在“显式 build + 构建契约通过”后直接启动 | build 已是显式动作且执行了同一契约校验；不违反“start 不构建” | 无工件时也可跑一次；正式回归仍要求工件 |
| `prepare_start` 先取运行锁再做只读校验 | 实测校验窗口内另一进程可 load 并替换场景，导致 start 句柄失效 | 校验期间 status 可能显示 `start_in_progress=true` |
| `save` 默认拒绝“构建后已运行”的场景 | 防止把非初始位姿固化成基线 | 需要显式 `--allow-after-run` 才能保存运行后快照 |
| 受保护工件不追加 `lifecycle.jsonl` | 保证阶段一基线文件哈希不变 | 运行/校验事件只写 `.runtime/lifecycle.jsonl` |
| v001 保留不删除 | 保留首次正式保存的审计历史 | `list` 会显示两个版本；current 明确指向 v002 |
| `stage1` 与 `stage2` 命名空间由 CLI/MCP 参数显式指定 | 防止 legacy 后端静默接管工件 | 调用方必须传 `--phase` |

## 8. 已知边界与阶段二交接

- 只声明“场景可加载/可校验/可重复运行”，不声明路径无碰撞、TCP 不穿透、
  conveyor 停止可靠或脚本剥离最终正确。
- manifest 的 `objects` 是世界坐标 + alias 身份，不是几何/碰撞契约；阶段二
  需在 `extra` 或 schema 兼容字段中加入碰撞集合、允许碰撞对、接触参数、
  路径配置，并重新执行加载/校验/重启回归。
- 阶段二不得修改 `v002` 或 `current.json`；必须派生 `stage2` 副本并记录
  父工件摘要与差异。
- 任何布局、模型、物理参数或构建逻辑变化都必须重新 build/save 新版本，
  旧工件的配置/模型摘要会拒绝 start。
